"""End-to-end PII redaction pipeline and recording policy tests (Part 1D / Gate G2)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from twilio.twiml.voice_response import Connect, VoiceResponse

from app.ai.guardrails import pii as guardrails_pii
from app.db.models import AuditAction, AuditLog, CallStatus, Speaker
from app.gdpr.redact import TOKEN_CARD, TOKEN_EMAIL, TOKEN_PHONE, TOKEN_SSN
from app.telephony.call_state import apply_status
from app.telephony.recording import CallRecording, request_recording, start_recording_if_enabled
from app.telephony.recording_policy import (
    disclosure_twiml,
    effective,
    prepend_disclosure_twiml,
    save,
)
from app.telephony.transcription import finalize_stored_turns, record_turn
from app.tenancy.isolation import Forbidden
from app.webhooks.call_event_bridge import redact_webhook_payload
from tests.conftest import auth_headers, make_call

pytestmark = pytest.mark.asyncio

SAMPLE_UTTERANCE = (
    "My Visa is 4111 1111 1111 1111, my order ID is 4111 1111 1111 1112, "
    "email jane.doe@example.com, phone +1 (555) 234-5678, and SSN 123-45-6789."
)


def test_guardrails_pii_delegates_to_gdpr_redact_with_luhn_and_ssn():
    redacted = guardrails_pii.redact(SAMPLE_UTTERANCE)
    assert isinstance(redacted, str)
    text, findings = redacted
    assert text == redacted
    assert set(findings) == {"card", "email", "phone", "ssn"}
    assert guardrails_pii.has_pii(SAMPLE_UTTERANCE) is True
    assert guardrails_pii.has_pii("Order reference 4111 1111 1111 1112 only.") is False

    # Luhn-valid Visa is scrubbed; non-Luhn 16-digit order number is preserved.
    assert "4111 1111 1111 1111" not in redacted
    assert TOKEN_CARD in redacted
    assert "4111 1111 1111 1112" in redacted
    assert "jane.doe@example.com" not in redacted
    assert TOKEN_EMAIL in redacted
    assert "234-5678" not in redacted
    assert TOKEN_PHONE in redacted
    assert "123-45-6789" not in redacted
    assert TOKEN_SSN in redacted


async def test_turn_persistence_llm_input_and_webhook_payload_redact_pii(db, tenant_a):
    await save(db, tenant_a, enabled=True, redact_pii=True)
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)

    turn = await record_turn(
        db,
        call,
        speaker=Speaker.USER,
        text=SAMPLE_UTTERANCE,
    )
    await db.commit()
    await db.refresh(turn)

    # 1. Persisted Turn.text has Visa, email, phone, and SSN redacted, order ID preserved
    for leaked in ("4111 1111 1111 1111", "jane.doe@example.com", "234-5678", "123-45-6789"):
        assert leaked not in turn.text
    assert "4111 1111 1111 1112" in turn.text

    # 2. Finalized transcript passed to post-call LLM preserves order ID and scrubs PII
    apply_status(call, CallStatus.COMPLETED)
    await db.commit()
    llm_transcript, evidence = await finalize_stored_turns(db, call)
    assert evidence["pii_redacted"] is True
    for leaked in ("4111 1111 1111 1111", "jane.doe@example.com", "234-5678", "123-45-6789"):
        assert leaked not in llm_transcript
    assert "4111 1111 1111 1112" in llm_transcript

    # 3. Webhook payload sanitizer scrubs PII while preserving non-Luhn order ID
    hook_payload = redact_webhook_payload(
        {"call_id": str(call.id), "snippet": SAMPLE_UTTERANCE},
        redact_pii=True,
    )
    for leaked in ("4111 1111 1111 1111", "jane.doe@example.com", "234-5678", "123-45-6789"):
        assert leaked not in hook_payload["snippet"]
    assert "4111 1111 1111 1112" in hook_payload["snippet"]
    assert hook_payload["call_id"] == str(call.id)


async def test_disabling_pii_redaction_requires_security_write_and_audits(
    client, db, tenant_a, owner_a, admin_a
):
    # Admin has compliance:write but NOT security:settings (owner-only)
    admin_headers = await auth_headers(client, admin_a)
    denied = await client.post(
        "/api/retention/recording-policy",
        headers=admin_headers,
        json={
            "enabled": True,
            "consent_mode": "one_party",
            "retention_days": 30,
            "redact_pii": False,
        },
    )
    assert denied.status_code == 403, denied.text

    # Owner has security:settings -> allowed and emits AuditAction.PII_REDACTION_DISABLED
    owner_headers = await auth_headers(client, owner_a)
    allowed = await client.post(
        "/api/retention/recording-policy",
        headers=owner_headers,
        json={
            "enabled": True,
            "consent_mode": "two_party",
            "disclosure_text": "This call is recorded for quality assurance.",
            "retention_days": 30,
            "redact_pii": False,
        },
    )
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["redact_pii"] is False

    audit_rows = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_a.id,
                AuditLog.action == AuditAction.PII_REDACTION_DISABLED,
            )
        )
    ).scalars().all()
    assert len(audit_rows) >= 1


async def test_recording_policy_disabled_skips_recording_and_two_party_prepends_twiml(
    db, tenant_a
):
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    await save(db, tenant_a, enabled=False, consent_mode="one_party")
    await db.commit()

    called_provider = False

    async def _fake_provider(_call):
        nonlocal called_provider
        called_provider = True
        return "RE123"

    rec = await start_recording_if_enabled(db, call, provider_start_fn=_fake_provider)
    assert rec is None
    assert called_provider is False
    count = (
        await db.execute(select(CallRecording).where(CallRecording.call_id == call.id))
    ).scalars().all()
    assert count == []
    with pytest.raises(Forbidden):
        await request_recording(db, call, consent_category="one_party", consent_state="not_required")

    # Enable two_party consent mode and verify disclosure TwiML is prepended before <Connect>
    await save(
        db,
        tenant_a,
        enabled=True,
        consent_mode="two_party",
        disclosure_text="Please note this call is being recorded.",
    )
    await db.commit()
    pol = await effective(db, tenant_a)
    frag = disclosure_twiml(pol)
    assert frag is not None and "<Say>Please note this call is being recorded.</Say>" in frag

    vr = VoiceResponse()
    assert prepend_disclosure_twiml(vr, pol) is True
    connect = Connect()
    connect.stream(url="wss://example.com/telephony/ws")
    vr.append(connect)
    xml = str(vr)
    assert xml.index("<Say>") < xml.index("<Connect>")
