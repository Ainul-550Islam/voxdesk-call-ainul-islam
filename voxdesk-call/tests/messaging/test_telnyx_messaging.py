"""Behavioural tests for ``TelnyxMessagingAdapter`` & Ed25519 webhook verification (Capability 11)."""
from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.messaging.channels.telnyx_messaging import TelnyxMessagingAdapter
from app.telephony import dnc
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_telnyx_messaging_outbound_send() -> None:
    captured: list[dict] = []

    async def fake_transport(method: str, url: str, *, headers: dict, json: dict):
        captured.append({"method": method, "url": url, "headers": headers, "json": json})
        return 200, {"data": {"id": "msg_telnyx_42", "to": [{"status": "queued"}]}}

    adapter = TelnyxMessagingAdapter(transport=fake_transport)
    res = await adapter.send_message(
        to_number="+14155550142",
        from_number="+14155550100",
        body="Telnyx verification code: 8142",
        channel_config={"api_key": "KEY_telnyx_test", "messaging_profile_id": "prof_1"},
    )
    assert res.provider == "telnyx"
    assert res.provider_message_id == "msg_telnyx_42"
    assert captured[0]["headers"]["Authorization"] == "Bearer KEY_telnyx_test"
    assert captured[0]["json"]["messaging_profile_id"] == "prof_1"
    assert captured[0]["json"]["text"] == "Telnyx verification code: 8142"


@pytest.mark.asyncio
async def test_telnyx_inbound_ed25519_verification_and_stop_keyword(
    client: AsyncClient,
    db: AsyncSession,
    tenant_a,
    owner_a,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(
        priv.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")

    monkeypatch.setattr(settings, "telnyx_api_key", "KEY_live_telnyx")
    monkeypatch.setattr(settings, "telnyx_public_key", pub_b64)

    headers = await auth_headers(client, owner_a)
    await client.post(
        "/api/channels",
        headers=headers,
        json={
            "channel_type": "sms",
            "provider": "telnyx",
            "config": {"from_number": "+14155550100"},
            "is_active": True,
        },
    )

    body_dict = {
        "data": {
            "event_type": "message.received",
            "payload": {
                "id": "telnyx_in_stop_1",
                "from": {"phone_number": "+14155550177"},
                "to": [{"phone_number": "+14155550100"}],
                "text": "STOP",
            },
        }
    }
    raw_bytes = json.dumps(body_dict).encode("utf-8")
    ts = str(int(time.time()))
    valid_sig = base64.b64encode(priv.sign(f"{ts}|".encode("utf-8") + raw_bytes)).decode("ascii")

    # Invalid Ed25519 signature -> 403
    bad_resp = await client.post(
        "/api/channels/webhook/telnyx",
        content=raw_bytes,
        headers={
            "Content-Type": "application/json",
            "telnyx-timestamp": ts,
            "telnyx-signature-ed25519": base64.b64encode(b"\x00" * 64).decode("ascii"),
        },
    )
    assert bad_resp.status_code == 403

    # Valid Ed25519 signature -> 200 + DNC STOP recorded
    ok_resp = await client.post(
        "/api/channels/webhook/telnyx",
        content=raw_bytes,
        headers={
            "Content-Type": "application/json",
            "telnyx-timestamp": ts,
            "telnyx-signature-ed25519": valid_sig,
        },
    )
    assert ok_resp.status_code == 200, ok_resp.text
    assert ok_resp.json()["keyword"] == "STOP"
    assert await dnc.is_dnc(db, tenant_id=tenant_a.id, phone="+14155550177") is True
