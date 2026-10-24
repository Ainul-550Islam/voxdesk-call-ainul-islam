"""Protocol-faithful offline tests for Zendesk Support REST v2 + Talk Partner voice comments + cursor sync."""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations_v2.core.provider_base import ConnectionRuntimeContext
from app.integrations_v2.helpdesk.models import (
    TicketCreateInput,
    TicketPriority,
    TicketStatus,
    TicketUpdateInput,
)
from app.integrations_v2.helpdesk.zendesk import ZendeskProvider
from tests.integrations_v2.conftest import make_protocol_http_client


@pytest.mark.asyncio
async def test_zendesk_ticket_lifecycle_voice_comment_and_cursor_pagination():
    recorded_requests: list[tuple[str, str, dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "acme.zendesk.com"
        assert req.headers.get("Authorization") == "Bearer zd-oauth-token"
        path = req.url.path
        body = json.loads(req.content) if req.content else {}
        recorded_requests.append((req.method, path, body))

        if path == "/api/v2/users/me.json":
            return httpx.Response(200, json={"user": {"id": 77, "name": "Support Bot", "role": "admin"}})
        if path == "/api/v2/users/search.json":
            return httpx.Response(200, json={"users": [{"id": 501, "name": "Alice", "email": "alice@acme.com"}]})
        if path == "/api/v2/tickets.json" and req.method == "POST":
            t = body["ticket"]
            return httpx.Response(
                201,
                json={
                    "ticket": {
                        "id": 1001,
                        "subject": t.get("subject", "Call"),
                        "description": (t.get("comment") or {}).get("body", ""),
                        "status": t.get("status", "open"),
                        "priority": t.get("priority", "normal"),
                        "requester_id": t.get("requester_id", 501),
                        "tags": t.get("tags", []),
                    }
                },
            )
        if path == "/api/v2/tickets/1001.json" and req.method == "PUT":
            t = body["ticket"]
            return httpx.Response(
                200,
                json={
                    "ticket": {
                        "id": 1001,
                        "subject": "Updated",
                        "status": t.get("status", "solved"),
                        "priority": "high",
                    }
                },
            )
        if path == "/api/v2/search.json":
            return httpx.Response(
                200,
                json={"results": [{"id": 1001, "subject": "Billing", "status": "open", "priority": "high"}]},
            )
        if path == "/api/v2/incremental/tickets/cursor.json":
            cur = req.url.params.get("cursor")
            if not cur:
                return httpx.Response(
                    200,
                    json={
                        "tickets": [{"id": 1, "subject": "T1", "updated_at": "2026-10-24T01:00:00Z"}],
                        "after_cursor": "zd-cur-2",
                        "end_of_stream": False,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "tickets": [{"id": 2, "subject": "T2", "updated_at": "2026-10-24T02:00:00Z"}],
                    "after_cursor": "zd-cur-end",
                    "end_of_stream": True,
                },
            )
        return httpx.Response(404)

    ctx = ConnectionRuntimeContext(
        tenant_id="t1",
        connection_id="c1",
        provider="zendesk",
        credentials={"access_token": "zd-oauth-token"},
        config={"subdomain": "acme"},
    )
    provider = ZendeskProvider(ctx, http_client=make_protocol_http_client(handler))

    hs = await provider.health()
    assert hs.ok is True
    assert hs.account_id == "77"

    created = await provider.create_ticket(
        TicketCreateInput(
            subject="Billing Question",
            description="Caller asked about invoice #44",
            priority=TicketPriority.HIGH,
            requester_email="alice@acme.com",
            idempotency_key="idem-zd-1",
        )
    )
    assert created.external_id == "1001"
    assert created.priority == TicketPriority.HIGH

    updated = await provider.update_ticket(
        "1001",
        TicketUpdateInput(status=TicketStatus.RESOLVED, comment_body="Resolved on call"),
    )
    assert updated.status == TicketStatus.RESOLVED

    voice_tid = await provider.log_voice_call(
        {
            "external_ticket_id": "1001",
            "duration_seconds": 120,
            "summary": "AI call summary",
            "caller_phone": "+14155550100",
            "recording_url": "https://signed.voxdesk.example/rec/1?sig=abc",
        }
    )
    assert voice_tid == "1001"
    put_bodies = [b for m, p, b in recorded_requests if m == "PUT" and p == "/api/v2/tickets/1001.json"]
    assert put_bodies[-1]["ticket"]["voice_comment"]["call_duration"] == 120

    found = await provider.search_tickets(email="alice@acme.com")
    assert len(found) == 1

    pages = [batch async for batch in provider.iter_updated_tickets()]
    assert len(pages) == 2
    assert [r.external_id for p in pages for r in p.items] == ["1", "2"]
