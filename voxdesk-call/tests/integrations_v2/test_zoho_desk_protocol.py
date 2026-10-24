"""Protocol-faithful offline tests for Zoho Desk v1 (multi-DC, orgId header, /api/v1/calls)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations_v2.core.provider_base import ConnectionRuntimeContext
from app.integrations_v2.helpdesk.models import TicketCreateInput, TicketPriority
from app.integrations_v2.helpdesk.zoho_desk import ZohoDeskProvider
from tests.integrations_v2.conftest import make_protocol_http_client


@pytest.mark.asyncio
async def test_zoho_desk_eu_datacenter_org_header_tickets_and_calls():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "desk.zoho.eu"
        assert req.headers.get("Authorization") == "Zoho-oauthtoken zdesk-eu-tok"
        path = req.url.path
        if path == "/api/v1/organizations":
            return httpx.Response(200, json={"data": [{"id": "org-eu-1", "companyName": "Acme EU"}]})

        assert req.headers.get("orgId") == "org-eu-1"
        if path == "/api/v1/contacts/search":
            return httpx.Response(200, json={"data": [{"id": "c-700", "firstName": " Clara", "lastName": "Oswald"}]})
        if path == "/api/v1/tickets" and req.method == "POST":
            body = json.loads(req.content)
            assert body["departmentId"] == "dep-99"
            assert body["contactId"] == "c-700"
            return httpx.Response(
                200,
                json={"id": "zd-t-1", "subject": body["subject"], "status": "Open", "priority": "High"},
            )
        if path == "/api/v1/calls" and req.method == "POST":
            body = json.loads(req.content)
            assert body["duration"] == 210
            return httpx.Response(200, json={"id": "zcall-888"})
        return httpx.Response(404)

    ctx = ConnectionRuntimeContext(
        tenant_id="t1",
        connection_id="c-zd",
        provider="zoho_desk",
        region="eu",
        credentials={"access_token": "zdesk-eu-tok"},
        config={"region": "eu", "org_id": "org-eu-1", "department_id": "dep-99"},
    )
    provider = ZohoDeskProvider(ctx, http_client=make_protocol_http_client(handler))

    assert (await provider.health()).account_id == "org-eu-1"
    ticket = await provider.create_ticket(
        TicketCreateInput(
            subject="EU GDPR Request",
            description="Caller requested export",
            priority=TicketPriority.HIGH,
            requester_email="clara@acme.eu",
        )
    )
    assert ticket.external_id == "zd-t-1"

    call_id = await provider.log_voice_call(
        {
            "caller_email": "clara@acme.eu",
            "subject": "GDPR Voice Call",
            "summary": "Handled export verification",
            "duration_seconds": 210,
            "direction": "inbound",
        }
    )
    assert call_id == "zcall-888"
