"""Behavioural tests for ``TwilioWhatsAppAdapter`` & ``/api/channels/webhook/whatsapp`` (Capability 11)."""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.messaging.channels.twilio_whatsapp import TwilioWhatsAppAdapter
from app.telephony import dnc
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_twilio_whatsapp_outbound_session_and_template_formatting() -> None:
    calls: list[dict] = []

    async def fake_transport(method: str, url: str, *, auth: tuple[str, str], data: dict):
        calls.append({"method": method, "url": url, "auth": auth, "data": data})
        return 201, {"sid": "SM_wa_1001", "status": "queued"}

    adapter = TwilioWhatsAppAdapter(transport=fake_transport)
    cfg = {
        "account_sid": "AC_test_wa",
        "auth_token": "tok_test_wa",
        "whatsapp_number": "+14155238886",
    }

    # 1. Freeform message normalizes whatsapp:+E164
    res = await adapter.send_message(
        to_number="+14155550123",
        body="Your appointment is confirmed.",
        channel_config=cfg,
    )
    assert res.provider_message_id == "SM_wa_1001"
    assert calls[0]["data"]["To"] == "whatsapp:+14155550123"
    assert calls[0]["data"]["From"] == "whatsapp:+14155238886"
    assert calls[0]["data"]["Body"] == "Your appointment is confirmed."

    # 2. ContentSid template message
    await adapter.send_message(
        to_number="whatsapp:+14155550123",
        body="ignored when template_sid present",
        template_sid="HX1234567890",
        template_variables={"1": "9:30 AM"},
        channel_config=cfg,
    )
    assert calls[1]["data"]["ContentSid"] == "HX1234567890"
    assert calls[1]["data"]["ContentVariables"] == '{"1":"9:30 AM"}'


@pytest.mark.asyncio
async def test_twilio_whatsapp_webhook_hmac_verification_and_stop_start_help(
    client: AsyncClient,
    db: AsyncSession,
    tenant_a,
    owner_a,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_token = "wa_hmac_secret_token_99"
    tenant_a_id = tenant_a.id
    monkeypatch.setattr(settings, "twilio_account_sid", "AC_wa_live")
    monkeypatch.setattr(settings, "twilio_auth_token", auth_token)
    monkeypatch.setattr(settings, "twilio_whatsapp_number", "whatsapp:+14155238886")

    headers = await auth_headers(client, owner_a)
    ch = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "channel_type": "whatsapp",
            "provider": "twilio",
            "config": {"whatsapp_number": "whatsapp:+14155238886"},
            "is_active": True,
        },
    )
    assert ch.status_code == 201

    webhook_url = "http://test/api/channels/webhook/whatsapp"
    validator = RequestValidator(auth_token)

    # Forged signature -> 403
    bad = await client.post(
        "/api/channels/webhook/whatsapp",
        data={
            "From": "whatsapp:+14155550188",
            "To": "whatsapp:+14155238886",
            "Body": "Hello",
            "MessageSid": "SM_bad_sig",
        },
        headers={"X-Twilio-Signature": "forged=="},
    )
    assert bad.status_code == 403

    # Valid signature with STOP -> adds DNC entry & returns unsubscribe TwiML
    stop_form = {
        "From": "whatsapp:+14155550188",
        "To": "whatsapp:+14155238886",
        "Body": "STOP",
        "MessageSid": "SM_wa_stop_1",
    }
    stop_sig = validator.compute_signature(webhook_url, stop_form)
    stop_resp = await client.post(
        "/api/channels/webhook/whatsapp",
        data=stop_form,
        headers={"X-Twilio-Signature": stop_sig},
    )
    assert stop_resp.status_code == 200
    assert "unsubscribed" in stop_resp.text.lower()
    assert await dnc.is_dnc(db, tenant_id=tenant_a_id, phone="+14155550188") is True

    # Outbound send to opted-out recipient -> 403 RECIPIENT_OPTED_OUT
    blocked = await client.post(
        "/api/channels/send",
        headers=headers,
        json={"channel_type": "whatsapp", "to": "+14155550188", "message": "Promo"},
    )
    assert blocked.status_code == 403

    # Valid signature with START -> removes DNC entry
    start_form = {
        "From": "whatsapp:+14155550188",
        "To": "whatsapp:+14155238886",
        "Body": "START",
        "MessageSid": "SM_wa_start_1",
    }
    start_sig = validator.compute_signature(webhook_url, start_form)
    start_resp = await client.post(
        "/api/channels/webhook/whatsapp",
        data=start_form,
        headers={"X-Twilio-Signature": start_sig},
    )
    assert start_resp.status_code == 200
    assert "resubscribed" in start_resp.text.lower()
    db.expire_all()
    assert await dnc.is_dnc(db, tenant_id=tenant_a_id, phone="+14155550188") is False

    # Valid signature with HELP -> returns help TwiML
    help_form = {
        "From": "whatsapp:+14155550188",
        "To": "whatsapp:+14155238886",
        "Body": "HELP",
        "MessageSid": "SM_wa_help_1",
    }
    help_sig = validator.compute_signature(webhook_url, help_form)
    help_resp = await client.post(
        "/api/channels/webhook/whatsapp",
        data=help_form,
        headers={"X-Twilio-Signature": help_sig},
    )
    assert help_resp.status_code == 200
    assert "reply stop" in help_resp.text.lower()
