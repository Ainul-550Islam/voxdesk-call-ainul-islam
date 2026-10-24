"""Protocol-faithful offline tests for Zoom REST API v2 (S2S OAuth, meetings, lookup, Phone scope honesty, Team Chat)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations_v2.collab.zoom import ZoomProvider
from app.integrations_v2.core.errors import CapabilityUnsupported
from app.integrations_v2.core.provider_base import ConnectionRuntimeContext
from tests.integrations_v2.conftest import make_protocol_http_client


@pytest.mark.asyncio
async def test_zoom_s2s_oauth_meetings_lookup_phone_scope_gate_and_chat():
    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url).startswith("https://zoom.us/oauth/token"):
            assert req.url.params.get("grant_type") == "account_credentials"
            assert req.url.params.get("account_id") == "zm-acct-1"
            return httpx.Response(200, json={"access_token": "zm-s2s-token", "expires_in": 3600})

        assert req.url.host == "api.zoom.us"
        assert req.headers.get("Authorization") == "Bearer zm-s2s-token"
        path = req.url.path
        if path == "/v2/users/me":
            return httpx.Response(200, json={"id": "zu-1", "account_id": "zm-acct-1", "email": "host@acme.com"})
        if path == "/v2/users/host@acme.com":
            return httpx.Response(200, json={"id": "zu-1", "email": "host@acme.com", "first_name": "Host"})
        if path == "/v2/users/me/meetings" and req.method == "POST":
            body = json.loads(req.content)
            assert body["type"] == 2
            return httpx.Response(
                201,
                json={"id": 88990011, "join_url": "https://zoom.us/j/88990011", "topic": body["topic"]},
            )
        if path == "/v2/users/me/meetings" and req.method == "GET":
            return httpx.Response(
                200,
                json={"meetings": [{"id": 88990011, "topic": "Customer Onboarding", "join_url": "https://zoom.us/j/88990011"}]},
            )
        if path == "/v2/meetings/88990011" and req.method == "PATCH":
            return httpx.Response(204)
        if path == "/v2/phone/call_logs":
            return httpx.Response(200, json={"call_logs": [{"id": "log-1", "duration": 62}]})
        if path == "/v2/chat/users/me/messages":
            return httpx.Response(201, json={"id": "zchat-55"})
        return httpx.Response(404)

    ctx_with_phone = ConnectionRuntimeContext(
        tenant_id="t1",
        connection_id="c-zoom",
        provider="zoom",
        scopes=("meeting:write:meeting", "phone:read:list_call_logs:admin"),
        credentials={
            "account_id": "zm-acct-1",
            "client_id": "zm-cid",
            "client_secret": "zm-csec",
        },
    )
    provider = ZoomProvider(ctx_with_phone, http_client=make_protocol_http_client(handler))

    assert (await provider.health()).account_id == "zm-acct-1"
    user = await provider.lookup_user("host@acme.com")
    assert user is not None and user["id"] == "zu-1"

    mtg = await provider.create_meeting(topic="Customer Onboarding", duration_minutes=45)
    assert mtg["meeting_id"] == "88990011"
    assert mtg["join_url"] == "https://zoom.us/j/88990011"

    updated = await provider.update_meeting("88990011", topic="Updated Onboarding")
    assert updated["updated"] is True

    upcoming = await provider.list_upcoming_meetings()
    assert len(upcoming) == 1

    logs = await provider.list_phone_call_logs()
    assert len(logs) == 1 and logs[0]["id"] == "log-1"

    chat_id = await provider.send_chat_message(to_channel="ch-zoom-1", message="Call escalated")
    assert chat_id == "zchat-55"

    # Without Zoom Phone scope, list_phone_call_logs must raise CapabilityUnsupported honestly
    ctx_no_phone = ConnectionRuntimeContext(
        tenant_id="t1",
        connection_id="c-zoom-no-phone",
        provider="zoom",
        scopes=("meeting:write:meeting",),
        credentials={"access_token": "zm-s2s-token"},
    )
    provider_no_phone = ZoomProvider(ctx_no_phone, http_client=make_protocol_http_client(handler))
    with pytest.raises(CapabilityUnsupported) as exc_info:
        await provider_no_phone.list_phone_call_logs()
    assert exc_info.value.http_status == 501
