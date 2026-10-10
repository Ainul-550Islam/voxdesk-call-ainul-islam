"""End-to-end recording consent, disclosure TwiML, signed access, and legal-hold compliance tests (Part 7 / Gate G9).

Covers:
1. Jurisdiction & consent evaluation (``one_party``, ``two_party`` / ``all_party``, ``explicit``, ``unspecified``):
   - ``unspecified`` and ``all_party``/``two_party`` with ``unknown`` or ``denied`` state refuse recording capture (`Forbidden`).
   - ``RecordingConsent`` persisted via ``record()`` records the grant/denial and sets ``legal_certification = False``.
   - ``disclosure_twiml`` / ``prepend_disclosure_twiml`` prepends the mandatory disclosure ``<Say>`` before ``<Connect>`` when ``consent_mode`` is ``two_party`` or ``explicit``.
2. HTTP recording lifecycle (`POST /api/recordings`, `GET /api/recordings/{id}`, `POST /api/recordings/{id}/signed-access`):
   - Rejects unconsented capture in two-party mode with HTTP 403.
   - Permits capture once ``consent_state == "granted"`` and issues time-limited signed access tokens.
   - Enforces tenant isolation (Tenant B gets HTTP 404 and logs ``recording_access_denied``) and RBAC (viewer gets HTTP 403).
3. Legal hold & retention purge (`purge_for_calls`):
   - Recordings under ``legal_hold=True`` are preserved when purge runs; releasing legal hold allows purge to transition recordings to ``deleted``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from twilio.twiml.voice_response import Connect, VoiceResponse

from app.db.models import AuditLog, CallStatus
from app.telephony.consent import RecordingConsent, evaluate, get_owned, record
from app.telephony.recording import (
    authorize_read,
    purge_for_calls,
    request_recording,
    start_recording_if_enabled,
    transition,
)
from app.telephony.recording_policy import (
    disclosure_twiml,
    effective,
    prepend_disclosure_twiml,
    save,
)
from app.tenancy.isolation import Forbidden, NotFound
from tests.conftest import auth_headers, make_call

pytestmark = pytest.mark.asyncio


async def test_two_party_and_explicit_consent_gate_recording_and_prepend_disclosure(
    db, tenant_a, tenant_b
):
    """Recording is blocked until consent is granted under two_party/all_party policy, and disclosure TwiML is prepended."""
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)

    # 1. Configure tenant_a with two_party consent mode
    await save(
        db,
        tenant_a,
        enabled=True,
        consent_mode="two_party",
        disclosure_text="This call is recorded. By continuing you consent to recording.",
        retention_days=90,
    )
    await db.commit()

    policy = await effective(db, tenant_a)
    assert policy["enabled"] is True
    assert policy["consent_mode"] == "two_party"

    # Disclosure TwiML is generated and prepended before <Connect>
    twiml_tag = disclosure_twiml(policy)
    assert twiml_tag == "<Say>This call is recorded. By continuing you consent to recording.</Say>"
    vr = VoiceResponse()
    assert prepend_disclosure_twiml(vr, policy) is True
    connect = Connect()
    connect.stream(url="wss://stream.voxdesk.test/ws")
    vr.append(connect)
    rendered = str(vr)
    assert rendered.index("<Say>") < rendered.index("<Connect>")

    # 2. Recording is refused while consent is unknown or denied
    assert evaluate("two_party", "unknown").allowed is False
    assert evaluate("two_party", "denied").allowed is False
    assert evaluate("unspecified", "granted").allowed is False

    denied_consent = await record(
        db,
        tenant_id=tenant_a.id,
        call_id=call.id,
        jurisdiction="two_party",
        state="denied",
        source="ivr_dtmf_2",
    )
    await db.commit()
    assert denied_consent.as_dict()["legal_certification"] is False
    assert denied_consent.requirement == "required"

    with pytest.raises(Forbidden):
        await start_recording_if_enabled(
            db,
            call,
            consent_category="two_party",
            consent_state="denied",
        )

    # Cross-tenant isolation on RecordingConsent
    with pytest.raises(NotFound):
        await get_owned(db, tenant_b.id, denied_consent.id)

    # 3. Caller grants consent -> RecordingConsent persisted and recording starts
    granted_consent = await record(
        db,
        tenant_id=tenant_a.id,
        call_id=call.id,
        jurisdiction="two_party",
        state="granted",
        source="ivr_dtmf_1",
    )
    await db.commit()
    assert granted_consent.state == "granted"

    started_ext_ids: list[str] = []

    async def _provider_start(_call):
        started_ext_ids.append("RE9988776655")
        return "RE9988776655"

    rec = await start_recording_if_enabled(
        db,
        call,
        consent_category="two_party",
        consent_state=granted_consent.state,
        provider_start_fn=_provider_start,
    )
    await db.commit()
    assert rec is not None
    assert rec.state == "recording"
    assert rec.external_recording_id == "RE9988776655"
    assert started_ext_ids == ["RE9988776655"]

    consents = (
        await db.execute(
            select(RecordingConsent).where(RecordingConsent.call_id == call.id)
        )
    ).scalars().all()
    assert len(consents) == 2


async def test_http_recording_consent_enforcement_signed_access_and_isolation(
    client, db, tenant_a, tenant_b, owner_a, viewer_a, owner_b
):
    """HTTP /api/recordings enforces consent state, RBAC, signed access grants, and tenant isolation."""
    await save(db, tenant_a, enabled=True, consent_mode="two_party", retention_days=30)
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    await db.commit()

    headers_owner_a = await auth_headers(client, owner_a)
    headers_viewer_a = await auth_headers(client, viewer_a)
    headers_owner_b = await auth_headers(client, owner_b)

    # 1. Requesting recording without consent under all_party/two_party returns 403
    unconsented = await client.post(
        "/api/recordings",
        headers=headers_owner_a,
        json={
            "call_id": str(call.id),
            "provider": "twilio",
            "consent_category": "all_party",
            "consent_state": "unknown",
        },
    )
    assert unconsented.status_code == 403, unconsented.text

    # 2. Requesting recording with granted consent succeeds (201)
    consented = await client.post(
        "/api/recordings",
        headers=headers_owner_a,
        json={
            "call_id": str(call.id),
            "provider": "twilio",
            "consent_category": "all_party",
            "consent_state": "granted",
        },
    )
    assert consented.status_code == 201, consented.text
    recording_id = consented.json()["id"]

    # 3. Owner can issue signed-access grant
    signed_res = await client.post(
        f"/api/recordings/{recording_id}/signed-access",
        headers=headers_owner_a,
    )
    assert signed_res.status_code == 200, signed_res.text
    assert signed_res.json()["token"]
    assert signed_res.json()["recording_id"] == recording_id

    # 4. Viewer lacks recording:read permission -> 403
    viewer_get = await client.get(
        f"/api/recordings/{recording_id}",
        headers=headers_viewer_a,
    )
    assert viewer_get.status_code == 403

    # 5. Foreign tenant gets 404 on HTTP and service-layer authorize_read logs recording_access_denied
    cross_get = await client.get(
        f"/api/recordings/{recording_id}",
        headers=headers_owner_b,
    )
    assert cross_get.status_code == 404

    import uuid as _uuid

    with pytest.raises(NotFound):
        await authorize_read(
            db,
            tenant_id=tenant_a.id,
            recording_id=_uuid.UUID(recording_id),
            role=owner_b.role,
            actor_user_id=owner_b.id,
        )
    await db.commit()

    audits_b = (
        await db.execute(
            select(AuditLog).where(AuditLog.tenant_id == tenant_b.id)
        )
    ).scalars().all()
    assert any("recording_access_denied" in str(a.detail) for a in audits_b)


async def test_legal_hold_prevents_recording_purge_until_released(db, tenant_a):
    """purge_for_calls respects legal_hold=True and purges only after legal hold is lifted."""
    await save(
        db,
        tenant_a,
        enabled=True,
        consent_mode="one_party",
        legal_hold=True,
        retention_days=30,
    )
    call = await make_call(db, tenant_a, status=CallStatus.COMPLETED)
    rec = await request_recording(
        db,
        call,
        provider="twilio",
        consent_category="one_party",
        consent_state="not_required",
    )
    transition(rec, "recording")
    transition(rec, "ready")
    await db.commit()

    # While legal_hold is True, purge_for_calls refuses to delete the recording
    purged_while_held = await purge_for_calls(db, [call.id])
    await db.commit()
    await db.refresh(rec)
    assert purged_while_held["purged_recordings"] == 0
    assert call.id in purged_while_held["held_calls"]
    assert rec.state == "ready"

    # Lift legal hold -> purge_for_calls deletes the recording
    await save(
        db,
        tenant_a,
        enabled=True,
        consent_mode="one_party",
        legal_hold=False,
        retention_days=30,
    )
    await db.commit()

    purged_after_release = await purge_for_calls(db, [call.id])
    await db.commit()
    await db.refresh(rec)
    assert purged_after_release["purged_recordings"] == 1
    assert rec.state == "deleted"
    assert rec.deleted_at is not None
