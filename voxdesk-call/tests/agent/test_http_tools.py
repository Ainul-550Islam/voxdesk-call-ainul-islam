"""Unit and integration tests for Custom HTTP Tools & MCP Tools (Sub-Phase 2D)."""

from __future__ import annotations

import uuid

import httpx
import pytest
import respx

from app.agent.tools.http_tools import build_http_tool_schemas, execute_agent_http_tool
from app.agent.tools.mcp_tools import build_mcp_tool_schemas, execute_agent_mcp_tool
from app.db.models import McpServer, McpTool
from tests.conftest import make_tenant


@pytest.mark.asyncio
async def test_http_tool_interpolation_secret_header_and_ssrf_guard(monkeypatch):
    monkeypatch.setenv("CRM_API_SECRET", "topsecret-crm-token-123")

    tool_spec = {
        "name": "lookup_order_status",
        "description": "Look up an order by order_id.",
        "schema": {
            "url": "https://api.orders.example.com/v1/orders/{{order_id}}",
            "method": "POST",
            "headers": {"X-Custom-Source": "voxdesk-{{region}}"},
            "body_template": {"order_id": "{{order_id}}", "region": "{{region}}"},
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "region": {"type": "string"},
                },
                "required": ["order_id"],
            },
        },
        "auth_binding": {"type": "bearer", "token": "env:CRM_API_SECRET"},
    }

    schemas = build_http_tool_schemas([tool_spec])
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "lookup_order_status"

    with respx.mock(assert_all_called=True) as mock_http:
        route = mock_http.post("https://api.orders.example.com/v1/orders/ORD-900").mock(
            return_value=httpx.Response(
                200,
                json={"order_id": "ORD-900", "status": "shipped", "eta": "2026-10-10"},
            )
        )
        res = await execute_agent_http_tool(
            tool_spec=tool_spec,
            arguments={"order_id": "ORD-900", "region": "us-east"},
        )

    assert res["ok"] is True
    assert res["status_code"] == 200
    assert res["result"]["status"] == "shipped"
    sent_req = route.calls[0].request
    assert sent_req.headers["Authorization"] == "Bearer topsecret-crm-token-123"
    assert sent_req.headers["X-Custom-Source"] == "voxdesk-us-east"

    # SSRF check: private IP 127.0.0.1 or 169.254.169.254 must be blocked
    ssrf_spec = {
        "name": "metadata_probe",
        "schema": {"url": "http://169.254.169.254/latest/meta-data/", "method": "GET"},
    }
    blocked = await execute_agent_http_tool(tool_spec=ssrf_spec, arguments={})
    assert blocked["ok"] is False
    assert "SSRF" in (blocked.get("error") or "")

    # Timeout check
    timeout_spec = {
        "name": "slow_endpoint",
        "schema": {
            "url": "https://api.orders.example.com/v1/slow",
            "method": "GET",
            "timeout_seconds": 0.1,
        },
    }
    with respx.mock(assert_all_called=True) as mock_http:
        mock_http.get("https://api.orders.example.com/v1/slow").mock(
            side_effect=httpx.ReadTimeout("read timed out")
        )
        timed_out = await execute_agent_http_tool(tool_spec=timeout_spec, arguments={})
    assert timed_out["ok"] is False
    assert "timed out" in (timed_out.get("error") or "")


@pytest.mark.asyncio
async def test_mcp_tool_schema_and_execution(db, monkeypatch):
    tenant = await make_tenant(db, name="MCP Tool Tenant")
    server = McpServer(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="clinic_ehr",
        endpoint="https://mcp.ehr.example.com/rpc",
        transport="streamable_http",
        status="configured",
    )
    db.add(server)
    await db.flush()

    tool = McpTool(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        server_id=server.id,
        name="check_patient_chart",
        description="Look up chart summary.",
        input_schema={
            "type": "object",
            "properties": {"patient_id": {"type": "string"}},
            "required": ["patient_id"],
        },
        enabled=True,
    )
    db.add(tool)
    await db.commit()

    descriptor = {
        "id": str(tool.id),
        "server_id": str(server.id),
        "name": tool.name,
        "qualified_name": f"mcp__{server.name}__{tool.name}",
        "remote_name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
    }
    schemas = build_mcp_tool_schemas([descriptor])
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "mcp__clinic_ehr__check_patient_chart"

    async def _fake_mcp_execute(session, srv, tl, arguments, *, timeout=10.0):
        return {"patient_id": arguments["patient_id"], "allergies": ["penicillin"]}

    monkeypatch.setattr("app.agent.tools.mcp_tools.mcp_client.execute", _fake_mcp_execute)

    res = await execute_agent_mcp_tool(
        db,
        tenant_id=tenant.id,
        tool_descriptor=descriptor,
        arguments={"patient_id": "PT-42"},
    )
    assert res["ok"] is True
    assert res["result"]["allergies"] == ["penicillin"]
