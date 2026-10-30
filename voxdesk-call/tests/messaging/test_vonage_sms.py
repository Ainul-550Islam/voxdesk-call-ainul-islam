"""Behavioural tests for ``VonageSmsAdapter`` & JWT/HMAC webhook verification (Capability 11)."""
from __future__ import annotations

import hashlib
import json
import time

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.messaging.channels.vonage_sms import VonageSmsAdapter
from app.telephony import dnc
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_vonage_sms_outbound_send() -> None:
    captured: list[dict] = []

    async def fake_transport(method: str, url: str, *, auth: tuple[str, str], json: dict):
        captured.append({"method": method, "url": url, "auth": auth, "json": json})
        return 202, {"message_uuid": "von_uuid_777"}

    adapter = VonageSmsAdapter(transport=fake_transport)
    res = await adapter.send_message(
        to_number="+14155550166",
        from_number="+14155550100",
        body="Vonage reminder: appointment tomorrow.",
        channel_config={"api_key": "von_k", "api_secret": "von_s"},
    )
    assert res.provider == "vonage"
    assert res.provider_message_id == "von_uuid_777"
    assert captured[0]["auth"] == ("von_k", "von_s")
    assert captured[0]["json"]["to"] == "14155550166"
    assert captured[0]["json"]["from"] == "14155550100"


@pytest.mark.asyncio
async def test_vonage_inbound_jwt_payload_hash_verification_and_stop(
    client: AsyncClient,
    db: AsyncSession,
    tenant_a,
    owner_a,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sig_secret = "vonage_hs256_webhook_secret_key_32b!"
    monkeypatch.setattr(settings, "vonage_api_key", "von_k")
    monkeypatch.setattr(settings, "vonage_api_secret", "von_s")
    monkeypatch.setattr(settings, "vonage_signature_secret", sig_secret)

    headers = await auth_headers(client, owner_a)
    await client.post(
        "/api/channels",
        headers=headers,
        json={
            "channel_type": "sms",
            "provider": "vonage",
            "config": {"from_number": "+14155550100"},
            "is_active": True,
        },
    )

    body_bytes = json.dumps(
        {
            "message_uuid": "von_in_stop_1",
            "from": "14155550166",
            "to": "14155550100",
            "text": "STOP",
        }
    ).encode("utf-8")

    # Wrong JWT secret -> 403
    bad_jwt = jwt.encode(
        {
            "iat": int(time.time()),
            "payload_hash": hashlib.sha256(body_bytes).hexdigest(),
        },
        "wrong_secret_key_for_vonage_webhook!",
        algorithm="HS256",
    )
    bad_resp = await client.post(
        "/api/channels/webhook/vonage",
        content=body_bytes,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bad_jwt}",
        },
    )
    assert bad_resp.status_code == 403

    # Valid HS256 JWT + matching payload_hash -> 200 + DNC STOP recorded
    good_jwt = jwt.encode(
        {
            "iat": int(time.time()),
            "payload_hash": hashlib.sha256(body_bytes).hexdigest(),
        },
        sig_secret,
        algorithm="HS256",
    )
    ok_resp = await client.post(
        "/api/channels/webhook/vonage",
        content=body_bytes,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {good_jwt}",
        },
    )
    assert ok_resp.status_code == 200, ok_resp.text
    assert ok_resp.json()["keyword"] == "STOP"
    assert await dnc.is_dnc(db, tenant_id=tenant_a.id, phone="+14155550166") is True
