"""MCP streamable-HTTP client with discovery, schema normalization and limits."""

from __future__ import annotations
import uuid
import httpx
from jsonschema import validate
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.ssrf import (
    OutboundUrlError,
    validate_outbound_url,
    validate_resolved_outbound_url,
)
from app.core.tracing import span
from app.db.models import AuditAction, McpServer, McpTool
from app.security.secret_store import get_secret_store, SecretStoreError


class McpError(RuntimeError):
    """MCP protocol, validation, security, or execution failure."""


def validate_server(endpoint: str, transport: str) -> None:
    if transport not in {"streamable_http"}:
        raise McpError("only the implemented streamable_http transport is accepted")
    try:
        validate_outbound_url(endpoint, require_https=True)
    except OutboundUrlError as exc:
        raise McpError("MCP endpoint is not an allowed HTTPS destination") from exc


def normalize_tool(raw: dict) -> dict:
    name = raw.get("name")
    if not isinstance(name, str) or not name or len(name) > 128:
        raise McpError("MCP tool has an invalid name")
    schema = raw.get("inputSchema", {"type": "object", "properties": {}})
    if not isinstance(schema, dict) or schema.get("type", "object") != "object":
        raise McpError("MCP tool input schema must be an object schema")
    return {
        "name": name,
        "description": str(raw.get("description", ""))[:1000],
        "inputSchema": schema,
        "annotations": raw.get("annotations", {})
        if isinstance(raw.get("annotations", {}), dict)
        else {},
    }


async def _request(server: McpServer, method: str, params: dict, *, timeout: float = 10.0) -> dict:
    validate_server(server.endpoint, server.transport)
    try:
        await validate_resolved_outbound_url(server.endpoint, require_https=True)
    except OutboundUrlError as exc:
        raise McpError("MCP endpoint DNS resolution is not allowed") from exc
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if server.auth_ref:
        try:
            token = get_secret_store().get(
                str(server.tenant_id), f"mcp:{server.id}", server.auth_ref
            )
        except SecretStoreError as exc:
            raise McpError("MCP authentication reference is unavailable") from exc
        headers["Authorization"] = f"Bearer {token}"
    request_id = str(uuid.uuid4())
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        with span(
            "voxdesk.mcp.request",
            tenant_id=str(server.tenant_id),
            server_id=str(server.id),
            method=method,
        ):
            response = await client.post(server.endpoint, headers=headers, json=payload)
    if response.status_code >= 400:
        raise McpError(f"MCP endpoint returned HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise McpError("MCP endpoint did not return JSON") from exc
    if data.get("error"):
        raise McpError("MCP endpoint returned a JSON-RPC error")
    result = data.get("result")
    if not isinstance(result, dict):
        raise McpError("MCP response result is invalid")
    return result


async def health(server: McpServer) -> dict:
    """Use MCP ping, bounded by the same timeout and endpoint validation."""
    return await _request(server, "ping", {}, timeout=5.0)


async def discover(session: AsyncSession, server: McpServer) -> list[McpTool]:
    from app.auth.identity.events import emit as audit_event

    # MCP servers may require initialization before tools/list. The initialize
    # exchange is a real protocol handshake, not a connectivity-only probe.
    await _request(
        server,
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "VoxDesk", "version": "1"},
        },
    )
    result = await _request(server, "tools/list", {})
    raw_tools = result.get("tools", [])
    if not isinstance(raw_tools, list):
        raise McpError("MCP tools/list result is invalid")
    await session.execute(
        delete(McpTool).where(McpTool.server_id == server.id, McpTool.tenant_id == server.tenant_id)
    )
    rows = []
    for raw in raw_tools:
        normalized = normalize_tool(raw)
        row = McpTool(
            tenant_id=server.tenant_id,
            server_id=server.id,
            name=normalized["name"],
            description=normalized["description"],
            input_schema=normalized["inputSchema"],
            annotations=normalized["annotations"],
        )
        session.add(row)
        rows.append(row)
    server.status, server.server_info, server.last_health_at = (
        "healthy",
        {"tool_count": len(rows)},
        __import__("datetime").datetime.utcnow(),
    )
    await audit_event(
        session,
        AuditAction.INTEGRATION_TESTED,
        tenant_id=server.tenant_id,
        detail={"server_id": str(server.id), "outcome": "discovered", "tool_count": len(rows)},
    )
    await session.flush()
    return rows


async def execute(
    session: AsyncSession,
    server: McpServer,
    tool: McpTool,
    arguments: dict,
    *,
    timeout: float = 10.0,
) -> dict:
    from app.auth.identity.events import emit as audit_event

    if server.tenant_id != tool.tenant_id or tool.server_id != server.id or not tool.enabled:
        raise McpError("MCP tool is not authorized")
    try:
        validate(arguments, tool.input_schema or {"type": "object"})
    except Exception as exc:
        raise McpError("MCP tool arguments do not match its schema") from exc
    result = await _request(
        server, "tools/call", {"name": tool.name, "arguments": arguments}, timeout=timeout
    )
    await audit_event(
        session,
        AuditAction.INTEGRATION_TESTED,
        tenant_id=server.tenant_id,
        detail={"server_id": str(server.id), "tool": tool.name, "outcome": "success"},
    )
    return result
