"""Protocol-faithful offline tests for ServiceNow Table API (incident, sys_user, work_notes, offset sync)."""

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
from app.integrations_v2.helpdesk.servicenow import ServiceNowProvider
from tests.integrations_v2.conftest import make_protocol_http_client


@pytest.mark.asyncio
async def test_servicenow_incident_crud_state_codes_and_work_notes():
    seen_patches: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "acme.service-now.com"
        path = req.url.path
        if path == "/api/now/table/sys_user":
            if req.method == "GET":
                return httpx.Response(
                    200,
                    json={"result": [{"sys_id": "usr-001", "user_name": "admin", "email": "caller@acme.com"}]},
                )
        if path == "/api/now/table/incident" and req.method == "POST":
            body = json.loads(req.content)
            assert body["impact"] == "1" and body["urgency"] == "1"
            return httpx.Response(
                201,
                json={
                    "result": {
                        "sys_id": "inc-abc-123",
                        "number": "INC0010042",
                        "short_description": body["short_description"],
                        "description": body["description"],
                        "state": "1",
                        "priority": "1",
                        "caller_id": body.get("caller_id", ""),
                    }
                },
            )
        if path == "/api/now/table/incident/inc-abc-123" and req.method == "PATCH":
            patch = json.loads(req.content)
            seen_patches.append(patch)
            return httpx.Response(
                200,
                json={
                    "result": {
                        "sys_id": "inc-abc-123",
                        "short_description": "Outage",
                        "state": patch.get("state", "6"),
                        "priority": "1",
                    }
                },
            )
        if path == "/api/now/table/incident" and req.method == "GET":
            offset = int(req.url.params.get("sysparm_offset", "0"))
            if offset == 0:
                return httpx.Response(
                    200,
                    json={
                        "result": [
                            {
                                "sys_id": "inc-1",
                                "short_description": "VPN",
                                "state": "2",
                                "priority": "3",
                                "sys_updated_on": "2026-10-24 10:00:00",
                            }
                        ]
                    },
                )
            return httpx.Response(200, json={"result": []})
        return httpx.Response(404)

    ctx = ConnectionRuntimeContext(
        tenant_id="t1",
        connection_id="c2",
        provider="servicenow",
        instance_url="https://acme.service-now.com",
        credentials={"access_token": "sn-token"},
    )
    provider = ServiceNowProvider(ctx, http_client=make_protocol_http_client(handler))

    assert (await provider.health()).ok is True

    inc = await provider.create_ticket(
        TicketCreateInput(
            subject="Production Outage",
            description="Reported via voice IVR",
            priority=TicketPriority.URGENT,
            requester_email="caller@acme.com",
        )
    )
    assert inc.external_id == "inc-abc-123"
    assert inc.priority == TicketPriority.URGENT
    assert inc.custom_fields["number"] == "INC0010042"

    resolved = await provider.update_ticket(
        "inc-abc-123",
        TicketUpdateInput(status=TicketStatus.RESOLVED, comment_body="Root cause mitigated", comment_public=False),
    )
    assert resolved.status == TicketStatus.RESOLVED
    assert seen_patches[-1]["state"] == "6"
    assert seen_patches[-1]["work_notes"] == "Root cause mitigated"

    batches = [b async for b in provider.iter_updated_tickets()]
    assert len(batches) == 1
    assert batches[0].items[0].external_id == "inc-1"
