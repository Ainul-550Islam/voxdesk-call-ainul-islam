"""Tests for MCP server bridge exposing Integrations v2 agent tools with identical tenant + RBAC authorization."""

from __future__ import annotations

import json

import httpx
import pytest

from app.db.integrations_v2_models import ToolEnablement
from app.integrations_v2.agent_tools.mcp_bridge import (
    call_mcp_v2_tool,
    list_mcp_v2_tools,
)
from app.integrations_v2.core.connection_store import upsert_connection
from tests.conftest import make_tenant
from tests.integrations_v2.conftest import make_protocol_http_client


@pytest.mark.asyncio
async def test_mcp_bridge_enforces_tenant_enablement_and_redacts_secrets(db):
    tenant_a = await make_tenant(db, "MCP Tenant A")
    tenant_b = await make_tenant(db, "MCP Tenant B")

    secret_token = "fd_api_key_super_secret_987654"
    conn_a = await upsert_connection(
        db,
        tenant_id=tenant_a.id,
        provider="freshdesk",
        category="helpdesk",
        auth_kind="api_key",
        credentials={"api_key": secret_token},
        config={"domain": "acme"},
    )
    db.add(
        ToolEnablement(
            tenant_id=tenant_a.id,
            agent_id="mcp-agent-1",
            tool_name="helpdesk_lookup_customer",
            connection_id=conn_a.id,
            enabled=True,
            max_calls_per_conversation=3,
            timeout_ms=3000,
        )
    )
    await db.commit()

    tools_a = await list_mcp_v2_tools(db, tenant_id=tenant_a.id, agent_id="mcp-agent-1")
    assert [t["name"] for t in tools_a] == ["helpdesk_lookup_customer"]
    assert "inputSchema" in tools_a[0]

    tools_b = await list_mcp_v2_tools(db, tenant_id=tenant_b.id, agent_id="mcp-agent-1")
    assert tools_b == []

    def fd_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v2/tickets":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 99,
                        "subject": f"Ticket Authorization: Basic {secret_token}",
                        "status": 2,
                        "priority": 3,
                    }
                ],
            )
        return httpx.Response(404)

    denied_cross_tenant = await call_mcp_v2_tool(
        db,
        tenant_id=tenant_b.id,
        tool_name="helpdesk_lookup_customer",
        arguments={"email": "caller@acme.com"},
        call_id="mcp-call-1",
        agent_id="mcp-agent-1",
        http_client=make_protocol_http_client(fd_handler),
    )
    assert denied_cross_tenant["ok"] is False

    allowed = await call_mcp_v2_tool(
        db,
        tenant_id=tenant_a.id,
        tool_name="helpdesk_lookup_customer",
        arguments={"email": "caller@acme.com"},
        call_id="mcp-call-1",
        agent_id="mcp-agent-1",
        http_client=make_protocol_http_client(fd_handler),
    )
    assert allowed["ok"] is True
    assert secret_token not in json.dumps(allowed)
