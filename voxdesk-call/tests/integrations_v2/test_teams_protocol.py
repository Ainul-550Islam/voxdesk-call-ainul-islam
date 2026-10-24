"""Protocol-faithful offline tests for Microsoft Teams Graph v1.0 (Adaptive Cards, onlineMeetings, calendar, subscriptions, Direct Routing honesty)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations_v2.collab.teams import (
    TeamsProvider,
    build_teams_call_adaptive_card,
)
from app.integrations_v2.core.errors import CapabilityUnsupported
from app.integrations_v2.core.provider_base import ConnectionRuntimeContext
from tests.integrations_v2.conftest import make_protocol_http_client


@pytest.mark.asyncio
async def test_teams_graph_adaptive_card_online_meeting_calendar_subscriptions_and_honesty():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "graph.microsoft.com"
        assert req.headers.get("Authorization") == "Bearer graph-tok"
        path = req.url.path
        if path == "/v1.0/me":
            return httpx.Response(200, json={"id": "ms-user-1", "displayName": "Ops Bot"})
        if path == "/v1.0/me/joinedTeams":
            return httpx.Response(200, json={"value": [{"id": "team-1", "displayName": "Support Team"}]})
        if path == "/v1.0/teams/team-1/channels":
            return httpx.Response(200, json={"value": [{"id": "chan-1", "displayName": "Escalations"}]})
        if path == "/v1.0/teams/team-1/channels/chan-1/messages" and req.method == "POST":
            body = json.loads(req.content)
            att = body["attachments"][0]
            assert att["contentType"] == "application/vnd.microsoft.card.adaptive"
            assert att["content"]["version"] == "1.4"
            return httpx.Response(201, json={"id": "msg-900"})
        if path == "/v1.0/me/onlineMeetings" and req.method == "POST":
            return httpx.Response(
                201,
                json={"id": "mtg-123", "joinWebUrl": "https://teams.microsoft.com/l/meetup-join/123", "subject": "Escalation"},
            )
        if path == "/v1.0/me/events" and req.method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": "cal-77",
                    "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/cal77"},
                },
            )
        if path == "/v1.0/users" and req.method == "GET":
            return httpx.Response(
                200,
                json={"value": [{"id": "agent-42", "displayName": "Agent 42", "mail": "a42@acme.com"}]},
            )
        if path == "/v1.0/users/agent-42/presence":
            return httpx.Response(200, json={"id": "agent-42", "availability": "Available", "activity": "Available"})
        if path == "/v1.0/subscriptions" and req.method == "POST":
            body = json.loads(req.content)
            assert body["clientState"] == "secret-state-1"
            return httpx.Response(
                201,
                json={"id": "sub-1", "resource": body["resource"], "expirationDateTime": body["expirationDateTime"]},
            )
        if path == "/v1.0/subscriptions/sub-1" and req.method == "PATCH":
            body = json.loads(req.content)
            return httpx.Response(200, json={"id": "sub-1", "expirationDateTime": body["expirationDateTime"]})
        return httpx.Response(404)

    ctx = ConnectionRuntimeContext(
        tenant_id="t1",
        connection_id="c-teams",
        provider="teams",
        credentials={"access_token": "graph-tok"},
    )
    provider = TeamsProvider(ctx, http_client=make_protocol_http_client(handler))

    assert (await provider.health()).account_id == "ms-user-1"
    teams = await provider.list_joined_teams()
    assert teams[0]["id"] == "team-1"
    channels = await provider.list_channels("team-1")
    assert channels[0]["id"] == "chan-1"

    card = build_teams_call_adaptive_card(
        title="VIP Escalation",
        summary="Caller requested supervisor callback",
        caller_phone="+14155550101",
        duration_seconds=140,
        dashboard_url="https://app.voxdesk.ai/calls/1",
    )
    msg_id = await provider.post_channel_adaptive_card(
        team_id="team-1", channel_id="chan-1", card=card
    )
    assert msg_id == "msg-900"

    mtg = await provider.create_online_meeting(subject="Escalation", duration_minutes=30)
    assert mtg["join_url"].startswith("https://teams.microsoft.com/")

    cal = await provider.create_calendar_event(
        subject="Follow-up", attendees=["a42@acme.com"], duration_minutes=30
    )
    assert cal["event_id"] == "cal-77"
    assert cal["join_url"].startswith("https://teams.microsoft.com/")

    found_user = await provider.lookup_user(email="a42@acme.com")
    assert found_user is not None and found_user["id"] == "agent-42"

    pres = await provider.get_user_presence("agent-42")
    assert pres["availability"] == "Available"

    sub = await provider.create_subscription(
        resource="/teams/team-1/channels/chan-1/messages",
        notification_url="https://api.voxdesk.ai/webhooks/int2/teams/c-teams",
        client_state="secret-state-1",
    )
    assert sub["id"] == "sub-1"
    renewed = await provider.renew_subscription("sub-1")
    assert renewed["id"] == "sub-1"

    with pytest.raises(CapabilityUnsupported) as exc_info:
        await provider.place_direct_routing_pstn_call(to_e164="+14155550199")
    assert exc_info.value.http_status == 501
