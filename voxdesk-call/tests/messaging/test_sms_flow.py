"""Tests for Twilio Messaging SMS/WhatsApp webhook flow, ChatAgent routing, STOP -> DNC (1B), and signature verification (Part 6 / Gate G7)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.enterprise_models import DncEntry, MessageChannel
from app.db.models import Lead, LeadStatus, MessageWebhookReceipt
from app.db.retell_models import ChatAgent, ChatAgentVersion
from app.messaging.sms import compute_twilio_signature
from app.telephony.dnc import evaluate_dnc
from tests.conftest import make_tenant


async def _seed_sms_tenant_and_chat_agent(
    db: AsyncSession,
) -> tuple[uuid.UUID, str, ChatAgent, Lead]:
    now = datetime.now(timezone.utc)
    to_number = f"+1415555{uuid.uuid4().int % 10000:04d}"
    tenant = await make_tenant(db, name="SMS Flow Tenant")
    tenant.twilio_number = to_number
    await db.flush()

    cfg_snap = {
        "system_prompt": "You are an SMS receptionist for Bright Smile Dental. Keep replies concise.",
        "first_message": "Hi! Thanks for texting Bright Smile Dental.",
        "model": "gpt-4o-mini",
    }
    chat_agent = ChatAgent(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="SMS Receptionist",
        description="SMS channel agent",
        status="published",
        published_version=1,
        draft_version=1,
        draft_config=cfg_snap,
        published_config=cfg_snap,
        created_at=now,
        updated_at=now,
    )
    db.add(chat_agent)
    await db.flush()

    ver = ChatAgentVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        chat_agent_id=chat_agent.id,
        version=1,
        status="published",
        config=dict(cfg_snap),
        change_summary="Initial publish",
        published_at=now,
        created_at=now,
    )
    db.add(ver)

    channel = MessageChannel(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        channel_type="sms",
        provider="twilio",
        is_active=True,
        config={
            "phone_number": to_number,
            "chat_agent_id": str(chat_agent.id),
            "account_sid": "AC00000000000000000000000000000000",
            # "account_sid": "AC00000000000000000000000000000000",
            "auth_token_ref": "env:TWILIO_AUTH_TOKEN",
        },
        health_status="healthy",
        created_at=now,
        updated_at=now,
    )
    db.add(channel)

    lead = Lead(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Sam Caller",
        phone="+14155550999",
        status=LeadStatus.NEW,
        created_at=now,
    )
    db.add(lead)
    await db.commit()
    return tenant.id, to_number, chat_agent, lead


@pytest.mark.asyncio
async def test_inbound_sms_routes_to_chat_agent_and_returns_twiml_reply(
    client: AsyncClient, db: AsyncSession
):
    """Inbound SMS -> ChatAgent -> TwiML <Message> reply + MessageWebhookReceipt."""
    tenant_id, to_number, _, _ = await _seed_sms_tenant_and_chat_agent(db)

    form_data = {
        "MessageSid": f"SM{uuid.uuid4().hex[:30]}",
        "From": "+14155550999",
        "To": to_number,
        "Body": "Hi, what are your office hours on Tuesday?",
    }
    res = await client.post("/messaging/sms/webhook", data=form_data)
    assert res.status_code == 200, res.text
    assert "application/xml" in res.headers.get("content-type", "")
    assert "<Response><Message>" in res.text
    assert "</Message></Response>" in res.text

    receipt = (
        await db.execute(
            select(MessageWebhookReceipt).where(
                MessageWebhookReceipt.provider_message_id == form_data["MessageSid"]
            )
        )
    ).scalar_one_or_none()
    assert receipt is not None
    assert receipt.tenant_id == tenant_id
    assert receipt.channel == "sms"


@pytest.mark.asyncio
async def test_inbound_sms_stop_keyword_adds_dnc_and_blocks_outbound(
    client: AsyncClient, db: AsyncSession
):
    """Inbound STOP keyword records DncEntry + Lead opt-out (1B) and blocks subsequent outbound DNC checks."""
    tenant_id, to_number, _, lead = await _seed_sms_tenant_and_chat_agent(db)

    stop_sid = f"SM{uuid.uuid4().hex[:30]}"
    res = await client.post(
        "/messaging/sms/webhook",
        data={
            "MessageSid": stop_sid,
            "From": lead.phone,
            "To": to_number,
            "Body": "STOP",
        },
    )
    assert res.status_code == 200, res.text
    assert "unsubscribed" in res.text.lower()

    # Verify DncEntry was persisted for (tenant_id, +14155550999)
    dnc_row = (
        await db.execute(
            select(DncEntry).where(
                DncEntry.tenant_id == tenant_id,
                DncEntry.phone == "+14155550999",
            )
        )
    ).scalar_one_or_none()
    assert dnc_row is not None
    assert dnc_row.source == "sms:stop"

    # Verify Lead status updated to DNC and evaluate_dnc blocks outbound calls
    await db.refresh(lead)
    assert lead.status == LeadStatus.DNC
    decision = await evaluate_dnc(db, tenant_id, "+14155550999")
    assert decision.blocked is True


@pytest.mark.asyncio
async def test_sms_webhook_signature_verification_and_not_configured_fail_closed(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """Invalid X-Twilio-Signature is rejected (403) and missing credentials fail closed with NOT_CONFIGURED (503)."""
    _, to_number, _, lead = await _seed_sms_tenant_and_chat_agent(db)

    # 1. Disable test bypass and remove auth token -> must fail closed with 501 NOT_CONFIGURED
    monkeypatch.setattr(settings, "twilio_skip_webhook_verify", False)
    monkeypatch.setattr(settings, "twilio_auth_token", "")

    unconfigured_res = await client.post(
        "/messaging/sms/webhook",
        data={
            "MessageSid": f"SM{uuid.uuid4().hex[:30]}",
            "From": lead.phone,
            "To": to_number,
            "Body": "Hello",
        },
    )
    assert unconfigured_res.status_code == 501
    assert unconfigured_res.json()["detail"]["code"] == "NOT_CONFIGURED"

    # 2. Configure auth token and send an invalid signature -> must reject with 403 INVALID_TWILIO_SIGNATURE
    monkeypatch.setattr(settings, "twilio_auth_token", "test_twilio_secret_token_123")
    bad_sig_res = await client.post(
        "/messaging/sms/webhook",
        headers={"X-Twilio-Signature": "invalid-signature-value"},
        data={
            "MessageSid": f"SM{uuid.uuid4().hex[:30]}",
            "From": lead.phone,
            "To": to_number,
            "Body": "Hello",
        },
    )
    assert bad_sig_res.status_code == 403
    assert bad_sig_res.json()["detail"]["code"] == "INVALID_TWILIO_SIGNATURE"

    # 3. Compute valid HMAC-SHA1 X-Twilio-Signature -> accepted (200)
    valid_params = {
        "MessageSid": f"SM{uuid.uuid4().hex[:30]}",
        "From": "+14155550888",
        "To": to_number,
        "Body": "HELP",
    }
    valid_sig = compute_twilio_signature(
        url="http://test/messaging/sms/webhook",
        params=valid_params,
        auth_token="test_twilio_secret_token_123",
    )
    ok_res = await client.post(
        "/messaging/sms/webhook",
        headers={"X-Twilio-Signature": valid_sig},
        data=valid_params,
    )
    assert ok_res.status_code == 200
    assert "Reply STOP to unsubscribe" in ok_res.text
