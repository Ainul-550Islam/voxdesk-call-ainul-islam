"""Tests for voice-agent mid-call tool executor (canonical names, dry_run without vendor call, ceiling, timeout, redaction, MCP)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.db.integrations_v2_models import ToolEnablement
from app.integrations_v2.agent_tools.executor import AgentToolExecutor
from app.integrations_v2.agent_tools.mcp_bridge import (
    call_mcp_v2_tool,
    list_mcp_v2_tools,
)
from app.integrations_v2.core.connection_store import upsert_connection
from tests.conftest import make_tenant
from tests.integrations_v2.conftest import make_protocol_http_client


@pytest.mark.asyncio
async def test_agent_tool_executor_enablement_dry_run_ceiling_and_secret_redaction(db):
    tenant = await make_tenant(db, "Agent Tool Tenant")

    secret_token = "zd_super_secret_oauth_token_12345"
    conn = await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="zendesk",
        category="helpdesk",
        auth_kind="oauth2",
        credentials={"access_token": secret_token},
        config={"subdomain": "acme"},
    )

    vendor_call_count = 0

    def zd_server(req: httpx.Request) -> httpx.Response:
        nonlocal vendor_call_count
        vendor_call_count += 1
        if req.url.path == "/api/v2/users/search.json":
            return httpx.Response(200, json={"users": [{"id": 10, "name": "Caller"}]})
        if req.url.path == "/api/v2/tickets.json":
            body = json.loads(req.content)["ticket"]
            return httpx.Response(
                201,
                json={
                    "ticket": {
                        "id": 4242,
                        "subject": body["subject"],
                        "description": f"Created with Bearer {secret_token}",
                        "priority": "high",
                        "status": "open",
                    }
                },
            )
        return httpx.Response(404)

    executor = AgentToolExecutor(db, http_client=make_protocol_http_client(zd_server))

    disabled_res = await executor.execute(
        tenant_id=tenant.id,
        tool_name="helpdesk_create_ticket",
        arguments={"subject": "Help", "description": "Need support"},
        call_id="call-1",
    )
    assert disabled_res.ok is False
    assert "disabled" in disabled_res.error.lower()

    enablement = ToolEnablement(
        tenant_id=tenant.id,
        agent_id="agent-alpha",
        tool_name="helpdesk_create_ticket",
        connection_id=conn.id,
        enabled=True,
        max_calls_per_conversation=2,
        timeout_ms=3000,
    )
    db.add(enablement)
    await db.commit()

    # Dry-run validates arguments + enablement WITHOUT calling vendor HTTP transport
    dry = await executor.dry_run(
        tenant_id=tenant.id,
        tool_name="helpdesk_create_ticket",
        arguments={"subject": "Valid Subject", "description": "Valid Body"},
        agent_id="agent-alpha",
    )
    assert dry["valid"] is True
    assert dry["vendor_called"] is False
    assert vendor_call_count == 0

    dry_invalid = await executor.dry_run(
        tenant_id=tenant.id,
        tool_name="helpdesk_create_ticket",
        arguments={"subject": ""},
        agent_id="agent-alpha",
    )
    assert dry_invalid["valid"] is False
    assert vendor_call_count == 0

    mcp_tools = await list_mcp_v2_tools(db, tenant_id=tenant.id, agent_id="agent-alpha")
    assert [t["name"] for t in mcp_tools] == ["helpdesk_create_ticket"]

    for idx in range(2):
        ok_res = await call_mcp_v2_tool(
            db,
            tenant_id=tenant.id,
            tool_name="helpdesk_create_ticket",
            arguments={
                "subject": f"Issue {idx}",
                "description": "Voice caller reported outage",
                "priority": "high",
                "requester_email": "caller@acme.example",
            },
            call_id="call-1",
            agent_id="agent-alpha",
            http_client=make_protocol_http_client(zd_server),
        )
        assert ok_res["ok"] is True
        assert secret_token not in json.dumps(ok_res)

    capped_res = await executor.execute(
        tenant_id=tenant.id,
        tool_name="helpdesk_create_ticket",
        arguments={"subject": "Third", "description": "Over limit"},
        call_id="call-1",
        agent_id="agent-alpha",
    )
    assert capped_res.ok is False
    assert "ceiling" in capped_res.error.lower()
