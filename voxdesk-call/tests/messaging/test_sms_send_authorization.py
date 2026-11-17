from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.enterprise_models import DncEntry, MessageChannel
from tests.conftest import auth_headers


async def _grant_sms_consent(db, tenant, phone: str) -> None:
    from sqlalchemy import select

    from app.db.models import Environment, Lead
    from app.leads.consent import record_consent

    environment_id = await db.scalar(
        select(Environment.id).where(
            Environment.tenant_id == tenant.id,
            Environment.kind == "production",
        )
    )
    lead = Lead(
        tenant_id=tenant.id,
        environment_id=environment_id,
        phone=phone,
        name="Consent test recipient",
    )
    db.add(lead)
    await db.flush()
    await record_consent(
        db, lead, channel="sms", decision="granted", source="test:explicit-opt-in"
    )
    await db.commit()


@pytest.mark.asyncio
async def test_there_is_one_public_sms_send_surface(client, owner_a):
    headers = await auth_headers(client, owner_a)
    payload = {"channel_type": "sms", "to": "+14155550100", "message": "Hello"}

    unified = await client.post("/api/channels/send", json=payload)
    assert unified.status_code == 401

    legacy = await client.post("/messaging/sms/send", headers=headers, json=payload)
    assert legacy.status_code == 404, "legacy SMS send route must not remain public"


@pytest.mark.asyncio
async def test_unified_sms_send_does_not_accept_a_body_tenant(client, owner_a, tenant_b):
    response = await client.post(
        "/api/channels/send",
        headers=await auth_headers(client, owner_a),
        json={
            "channel_type": "sms",
            "tenant_id": str(tenant_b.id),
            "to": "+14155550100",
            "message": "Hello",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_authenticated_unified_send_without_channel_is_not_configured(client, owner_a):
    response = await client.post(
        "/api/channels/send",
        headers=await auth_headers(client, owner_a),
        json={"channel_type": "sms", "to": "+14155550100", "message": "Hello"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_unified_sms_send_requires_explicit_opt_in(client, db, owner_a, tenant_a):
    db.add(
        MessageChannel(
            tenant_id=tenant_a.id,
            channel_type="sms",
            provider="twilio",
            is_active=True,
            config={
                "account_sid": "AC_sms_consent_test",
                "auth_token": "test-token",
                "from_number": "+14155550999",
            },
            health_status="healthy",
        )
    )
    await db.flush()
    response = await client.post(
        "/api/channels/send",
        headers=await auth_headers(client, owner_a),
        json={"channel_type": "sms", "to": "+14155550100", "message": "Promo"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "SMS_CONSENT_REQUIRED"


@pytest.mark.asyncio
async def test_unified_sms_send_applies_dnc_before_touching_the_provider(
    client, db, owner_a, tenant_a
):
    db.add(
        MessageChannel(
            tenant_id=tenant_a.id,
            channel_type="sms",
            provider="twilio",
            is_active=True,
            config={
                "account_sid": "AC_test_guard",
                "auth_token": "test-token",
                "from_number": "+14155550999",
            },
            health_status="healthy",
        )
    )
    db.add(
        DncEntry(
            tenant_id=tenant_a.id,
            phone="+14155550100",
            reason="sms_stop_keyword",
            source="sms:stop",
        )
    )
    await db.flush()

    response = await client.post(
        "/api/channels/send",
        headers=await auth_headers(client, owner_a),
        json={"channel_type": "sms", "to": "+14155550100", "message": "Do not send"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "RECIPIENT_OPTED_OUT"


@pytest.mark.asyncio
async def test_multichannel_send_enforces_quiet_hours_and_channel_sender_scope(
    client, db, owner_a, tenant_a, monkeypatch
):
    headers = await auth_headers(client, owner_a)
    created = await client.post(
        "/api/channels",
        headers=headers,
        json={
            "channel_type": "sms",
            "provider": "twilio",
            "config": {
                "account_sid": "AC_sms_guard_test",
                "auth_token": "twilio-test-secret",
                "from_number": "+14155550999",
            },
            "is_active": True,
        },
    )
    assert created.status_code == 201, created.text
    await _grant_sms_consent(db, tenant_a, "+14155550100")

    monkeypatch.setattr(
        "app.messaging.sms._now",
        lambda: datetime(2026, 11, 8, 23, 30, tzinfo=timezone.utc),
    )
    quiet = await client.post(
        "/api/channels/send",
        headers=headers,
        json={"channel_type": "sms", "to": "+14155550100", "message": "Promo"},
    )
    assert quiet.status_code == 403
    assert quiet.json()["detail"]["code"] == "QUIET_HOURS"

    monkeypatch.setattr(
        "app.messaging.sms._now",
        lambda: datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc),
    )
    wrong_sender = await client.post(
        "/api/channels/send",
        headers=headers,
        json={
            "channel_type": "sms",
            "to": "+14155550100",
            "from_number": "+14155550001",
            "message": "Spoofed sender",
        },
    )
    assert wrong_sender.status_code == 403
    assert wrong_sender.json()["detail"]["code"] == "SENDER_NOT_AUTHORIZED"
