"""Protocol-faithful offline tests for Freshdesk REST API v2 (Basic api_key:X, numeric status/priority, notes)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.integrations_v2.core.provider_base import ConnectionRuntimeContext
from app.integrations_v2.helpdesk.freshdesk import FreshdeskProvider
from app.integrations_v2.helpdesk.models import (
    TicketCreateInput,
    TicketPriority,
    TicketStatus,
)
from tests.integrations_v2.conftest import make_protocol_http_client


@pytest.mark.asyncio
async def test_freshdesk_basic_auth_ticket_create_note_and_sync():
    expected_basic = base64.b64encode(b"fd-secret-key:X").decode("ascii")

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "acme.freshdesk.com"
        assert req.headers.get("Authorization") == f"Basic {expected_basic}"
        path = req.url.path
        if path == "/api/v2/agents/me":
            return httpx.Response(200, json={"id": 88, "contact": {"name": "FD Agent"}})
        if path == "/api/v2/tickets" and req.method == "POST":
            body = json.loads(req.content)
            assert body["priority"] == 4
            assert body["status"] == 2
            assert body["source"] == 3
            return httpx.Response(
                201,
                json={"id": 555, "subject": body["subject"], "status": 2, "priority": 4},
            )
        if path == "/api/v2/tickets/555/notes" and req.method == "POST":
            body = json.loads(req.content)
            assert body["private"] is True
            return httpx.Response(201, json={"id": 901, "body": body["body"]})
        if path == "/api/v2/tickets" and req.method == "GET":
            return httpx.Response(
                200,
                json=[{"id": 555, "subject": "Urgent Phone Ticket", "status": 2, "priority": 4, "updated_at": "2026-10-24T05:00:00Z"}],
            )
        return httpx.Response(404)

    ctx = ConnectionRuntimeContext(
        tenant_id="t1",
        connection_id="c-fd",
        provider="freshdesk",
        auth_kind="api_key",
        credentials={"api_key": "fd-secret-key"},
        config={"domain": "acme"},
    )
    provider = FreshdeskProvider(ctx, http_client=make_protocol_http_client(handler))

    assert (await provider.health()).account_id == "88"
    created = await provider.create_ticket(
        TicketCreateInput(
            subject="Urgent Phone Ticket",
            description="Caller reported login failure",
            priority=TicketPriority.URGENT,
            status=TicketStatus.OPEN,
            requester_email="bob@acme.com",
        )
    )
    assert created.external_id == "555"
    assert created.priority == TicketPriority.URGENT

    logged_id = await provider.log_voice_call(
        {
            "external_ticket_id": "555",
            "duration_seconds": 95,
            "summary": "Verified identity and reset MFA",
            "recording_url": "https://signed.voxdesk.example/r/555",
        }
    )
    assert logged_id == "555"

    batches = [b async for b in provider.iter_updated_tickets()]
    assert len(batches) == 1
    assert batches[0].items[0].external_id == "555"
