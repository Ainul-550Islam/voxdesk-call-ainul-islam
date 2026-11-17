"""Unit and integration tests for Custom HTTP Tools & MCP Tools (Sub-Phase 2D)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.agent.tools.http_tools import build_http_tool_schemas, execute_agent_http_tool
from app.agent.tools.mcp_tools import build_mcp_tool_schemas, execute_agent_mcp_tool
from app.agent.functions import FunctionHandlers
from app.agent.pipeline import filter_enabled_tools
from app.agent.s2s.s2s_tools_bridge import S2SToolsBridge
from app.db.models import ApiTool, CallStatus, McpServer, McpTool, TransferState
from app.runtime.agent_config_resolver import _resolve_scoped_tools
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


@pytest.mark.asyncio
async def test_persisted_api_tool_schema_matches_cascaded_and_s2s_contracts(db, monkeypatch):
    tenant = await make_tenant(db, name="API Tool Parity Tenant")
    tool = ApiTool(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="lookup_order",
        description="Look up an order and optional filter.",
        method="GET",
        url_template="https://api.example.test/orders/{order_id}",
        parameter_schema={
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
        body_schema={
            "type": "object",
            "properties": {"status": {"type": "string"}},
            "required": ["status"],
        },
        enabled=True,
    )
    db.add(tool)
    await db.flush()

    descriptors = await _resolve_scoped_tools(
        db,
        tenant_id=tenant.id,
        agent_id=None,
        snapshot={},
    )
    descriptor = next(item for item in descriptors if item["name"] == "lookup_order")
    call = SimpleNamespace(status=CallStatus.IN_PROGRESS, transfer_state=TransferState.NONE)
    handlers = FunctionHandlers(
        session=db,
        tenant=tenant,
        call=call,
        runtime_config=SimpleNamespace(custom_tools=[descriptor], mcp_tools=[]),
    )
    cascaded = [
        schema
        for schema in handlers.available_tools()
        if schema["function"]["name"] == "lookup_order"
    ]
    helper_schema = build_http_tool_schemas([descriptor])
    assert cascaded == helper_schema
    assert cascaded[0]["function"]["parameters"]["required"] == ["order_id", "body"]
    assert cascaded[0]["function"]["parameters"]["properties"]["body"] == tool.body_schema
    direct_api_schema = build_http_tool_schemas([], api_tools=[tool])
    assert direct_api_schema[0]["function"]["parameters"] == cascaded[0]["function"]["parameters"]

    active = filter_enabled_tools(cascaded, [], custom_names={"lookup_order"})
    bridge = S2SToolsBridge(
        handlers=handlers,
        session=db,
        tenant_id=tenant.id,
        custom_tools=[descriptor],
        tools=active,
    )
    openai_tool = bridge.tools_for("openai_realtime")[0]
    gemini_tool = bridge.tools_for("gemini_live")[0]["functionDeclarations"][0]
    expected_parameters = cascaded[0]["function"]["parameters"]
    assert openai_tool["parameters"] == expected_parameters
    assert gemini_tool["parameters"] == expected_parameters

    executions = []

    async def fake_http_execution(**kwargs):
        executions.append(kwargs)
        return {"ok": True, "tool": kwargs["tool_spec"]["name"]}

    monkeypatch.setattr("app.agent.functions.execute_agent_http_tool", fake_http_execution)
    cascaded_result = await handlers.dispatch(
        "lookup_order", {"order_id": "ORD-42", "body": {"status": "open"}}
    )
    monkeypatch.setattr("app.agent.s2s.s2s_tools_bridge.execute_agent_http_tool", fake_http_execution)
    realtime_result = await bridge.execute(
        "lookup_order", {"order_id": "ORD-42", "body": {"status": "open"}}
    )
    assert cascaded_result == realtime_result == {"ok": True, "tool": "lookup_order"}
    assert len(executions) == 2
    assert all(item["tool_spec"] == descriptor for item in executions)
    assert all(item["arguments"] == {"order_id": "ORD-42", "body": {"status": "open"}} for item in executions)
