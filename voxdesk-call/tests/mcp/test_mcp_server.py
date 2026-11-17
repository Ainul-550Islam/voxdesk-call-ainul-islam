"""PART 14 / P14-07 — MCP protocol, tenancy and tool failures."""

from __future__ import annotations

import uuid

import pytest

from app.mcp.server import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PROTOCOL_VERSION,
    CallContext,
    MCPServer,
    ToolSpec,
    _schema,
    default_tools,
)


def context(*scopes: str) -> CallContext:
    return CallContext(tenant_id=uuid.uuid4(), scopes=tuple(scopes))


def server_with(handler=None, scopes=("agents:read",), allowed=None) -> MCPServer:
    tools = default_tools()
    tools["list_agents"] = ToolSpec(
        "list_agents",
        "list",
        _schema({}, []),
        handler=handler or (lambda _args, _ctx: {"agents": [{"id": "a1"}]}),
        scopes=scopes,
    )
    return MCPServer(tools=tools, allowed_tools=allowed)


@pytest.mark.asyncio
async def test_initialize_negotiates_a_supported_version() -> None:
    server = server_with()
    reply = await server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION}},
        context(),
    )
    assert reply["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert reply["result"]["capabilities"]["tools"]["listChanged"] is False
    assert reply["result"]["serverInfo"]["name"] == "voxdesk-mcp"


@pytest.mark.asyncio
async def test_an_unknown_version_falls_back_instead_of_failing() -> None:
    server = server_with()
    reply = await server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "1999-01-01"}},
        context(),
    )
    assert reply["result"]["protocolVersion"] == PROTOCOL_VERSION


@pytest.mark.asyncio
async def test_tools_list_exposes_schemas_and_never_handlers() -> None:
    server = server_with()
    reply = await server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, context())
    names = {tool["name"] for tool in reply["result"]["tools"]}
    assert {"list_agents", "get_call", "search_knowledge", "create_contact"} <= names
    assert all("handler" not in tool for tool in reply["result"]["tools"])
    get_call = next(tool for tool in reply["result"]["tools"] if tool["name"] == "get_call")
    assert get_call["inputSchema"]["required"] == ["call_id"]


@pytest.mark.asyncio
async def test_a_tool_is_hidden_from_a_workspace_that_may_not_use_it() -> None:
    async def allowed(_tenant: uuid.UUID) -> set[str]:
        return {"get_call"}

    server = server_with(allowed=allowed)
    listing = await server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, context())
    assert [tool["name"] for tool in listing["result"]["tools"]] == ["get_call"]

    refused = await server.handle(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "list_agents", "arguments": {}}},
        context("agents:read"),
    )
    assert refused["error"]["code"] == INVALID_PARAMS
    assert "not enabled" in refused["error"]["message"]


@pytest.mark.asyncio
async def test_a_missing_scope_is_refused_before_the_handler_runs() -> None:
    called = {"count": 0}

    def handler(_args, _ctx):
        called["count"] += 1
        return {"ok": True}

    server = server_with(handler=handler, scopes=("agents:write",))
    reply = await server.handle(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "list_agents", "arguments": {}}},
        context("agents:read"),
    )
    assert reply["error"]["code"] == INVALID_PARAMS
    assert called["count"] == 0


@pytest.mark.asyncio
async def test_a_missing_required_argument_is_an_error_not_a_call() -> None:
    server = server_with()
    reply = await server.handle(
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "get_call", "arguments": {}}},
        context("calls:read"),
    )
    assert reply["error"]["code"] == INVALID_PARAMS
    assert "call_id" in reply["error"]["message"]


@pytest.mark.asyncio
async def test_an_unknown_tool_is_method_not_found() -> None:
    server = server_with()
    reply = await server.handle(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "drop_database", "arguments": {}}},
        context(),
    )
    assert reply["error"]["code"] == METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_a_raising_tool_becomes_isError_true_with_its_message() -> None:
    def handler(_args, _ctx):
        raise RuntimeError("crm unavailable")

    server = server_with(handler=handler)
    reply = await server.handle(
        {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "list_agents", "arguments": {}}},
        context("agents:read"),
    )
    assert reply["result"]["isError"] is True
    assert "crm unavailable" in reply["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_an_async_handler_is_awaited_and_json_encoded() -> None:
    async def handler(_args, _ctx):
        return {"agents": [{"id": "a1"}]}

    server = server_with(handler=handler)
    reply = await server.handle(
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "list_agents", "arguments": {}}},
        context("agents:read"),
    )
    assert reply["result"]["isError"] is False
    assert "a1" in reply["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_notifications_get_no_response_and_ping_does() -> None:
    server = server_with()
    assert await server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, context()) is None
    ping = await server.handle({"jsonrpc": "2.0", "id": 10, "method": "ping"}, context())
    assert ping["result"] == {}


@pytest.mark.asyncio
async def test_a_malformed_request_is_invalid_request() -> None:
    server = server_with()
    reply = await server.handle({"jsonrpc": "2.0", "id": 11}, context())
    assert reply["error"]["code"] == -32600


@pytest.mark.asyncio
async def test_an_unknown_method_is_method_not_found() -> None:
    server = server_with()
    reply = await server.handle({"jsonrpc": "2.0", "id": 12, "method": "resources/list"}, context())
    assert reply["error"]["code"] == METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_a_tool_without_a_handler_is_an_internal_error_not_a_crash() -> None:
    tools = default_tools()
    server = MCPServer(tools=tools)
    reply = await server.handle(
        {"jsonrpc": "2.0", "id": 13, "method": "tools/call", "params": {"name": "get_call", "arguments": {"call_id": "c1"}}},
        context("calls:read"),
    )
    assert reply["error"]["code"] == -32603


@pytest.mark.asyncio
async def test_the_http_surface_requires_authentication(client) -> None:
    response = await client.get("/api/mcp/voxdesk/tools")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_the_http_surface_lists_tools_and_answers_tools_list(client, owner_a) -> None:
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    listing = await client.get("/api/mcp/voxdesk/tools", headers=headers)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["server"]["name"] == "voxdesk-mcp"
    assert {tool["name"] for tool in body["tools"]} >= {"list_agents", "get_call"}

    reply = await client.post(
        "/api/mcp/voxdesk/rpc",
        headers=headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
    )
    assert reply.status_code == 200, reply.text
    payload = reply.json()
    assert payload["id"] == 1
    assert payload["result"]["tools"], "tools/list returned nothing"


@pytest.mark.asyncio
async def test_get_call_refuses_another_tenants_call_without_revealing_it(
    client, owner_a, owner_b
) -> None:
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    missing = await client.post(
        "/api/mcp/voxdesk/rpc",
        headers=headers,
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "get_call",
                "arguments": {"call_id": "11111111-1111-1111-1111-111111111111"},
            },
        },
    )
    assert missing.status_code == 200, missing.text
    assert missing.json()["result"]["isError"] is True
    assert "no such call" in missing.json()["result"]["content"][0]["text"]
