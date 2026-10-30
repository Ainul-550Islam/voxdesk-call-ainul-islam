"""Behavioural tests for the Messaging ``ChannelAdapter`` registry & ``/api/channels`` routes (Capability 11)."""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.messaging.channels import (
    ChannelUnsupportedError,
    TelnyxMessagingAdapter,
    TwilioSmsChannelAdapter,
    TwilioWhatsAppAdapter,
    VonageSmsAdapter,
    get_channel_adapter,
)
from tests.conftest import auth_headers


def test_registry_dispatches_supported_pairs_and_rejects_unsupported() -> None:
    assert isinstance(get_channel_adapter("twilio", "sms"), TwilioSmsChannelAdapter)
    assert isinstance(get_channel_adapter("twilio", "whatsapp"), TwilioWhatsAppAdapter)
    assert isinstance(get_channel_adapter("telnyx", "sms"), TelnyxMessagingAdapter)
    assert isinstance(get_channel_adapter("vonage", "sms"), VonageSmsAdapter)

    for bad_provider, bad_channel in [
        ("slack", "slack"),
        ("microsoft", "teams"),
        ("twilio", "slack"),
        ("vonage", "whatsapp"),
        ("sendgrid", "email"),
    ]:
        with pytest.raises(ChannelUnsupportedError):
            get_channel_adapter(bad_provider, bad_channel)


@pytest.mark.asyncio
async def test_multichannel_routes_reject_slack_teams_422_and_unconfigured_409(
    client: AsyncClient,
    db: AsyncSession,
    tenant_a,
    owner_a,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del db, tenant_a
    headers = await auth_headers(client, owner_a)

    # Unsupported channels (Slack / Teams) -> 422 UNSUPPORTED_CHANNEL
    for bad_chan, bad_prov in [("slack", "slack"), ("teams", "teams")]:
        c_res = await client.post(
            "/api/channels",
            headers=headers,
            json={"channel_type": bad_chan, "provider": bad_prov, "config": {}},
        )
        assert c_res.status_code == 422, c_res.text
        assert c_res.json()["detail"]["code"] == "UNSUPPORTED_CHANNEL"

        s_res = await client.post(
            "/api/channels/send",
            headers=headers,
            json={"channel_type": bad_chan, "to": "+14155550101", "message": "Hello"},
        )
        assert s_res.status_code == 422, s_res.text
        assert s_res.json()["detail"]["code"] == "UNSUPPORTED_CHANNEL"

    # Unconfigured Telnyx SMS -> 201 create, 409 NOT_CONFIGURED on health-check & send
    monkeypatch.setattr(settings, "telnyx_api_key", "")
    created = await client.post(
        "/api/channels",
        headers=headers,
        json={"channel_type": "sms", "provider": "telnyx", "config": {}, "is_active": True},
    )
    assert created.status_code == 201, created.text
    cid = created.json()["id"]

    hc = await client.post(f"/api/channels/{cid}/health-check", headers=headers)
    assert hc.status_code == 409
    assert hc.json()["detail"]["code"] == "NOT_CONFIGURED"

    send_unconf = await client.post(
        "/api/channels/send",
        headers=headers,
        json={"channel_type": "sms", "provider": "telnyx", "to": "+14155550199", "message": "Hi"},
    )
    assert send_unconf.status_code == 409
    assert send_unconf.json()["detail"]["code"] == "NOT_CONFIGURED"
    assert "no message or call was created" in send_unconf.json()["detail"]["message"]
