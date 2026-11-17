from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.models import McpServer, McpTool
from app.db.session import get_session
from app.mcp.client import McpError, discover, execute, health, validate_server

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class ServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    endpoint: str
    transport: str = "streamable_http"
    auth_ref: str | None = None


class CallRequest(BaseModel):
    arguments: dict = Field(default_factory=dict)


@router.get("/servers")
async def servers(
    ctx: TenantContext = Depends(require_permission(Permission.MCP_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        (await session.execute(select(McpServer).where(McpServer.tenant_id == ctx.tenant_id)))
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "name": r.name,
            "endpoint": r.endpoint,
            "transport": r.transport,
            "status": r.status,
        }
        for r in rows
    ]


@router.post("/servers", status_code=201)
async def create_server(
    body: ServerCreate,
    ctx: TenantContext = Depends(require_permission(Permission.MCP_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        validate_server(body.endpoint, body.transport)
    except McpError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.auth_ref and not body.auth_ref.startswith("secret://"):
        raise HTTPException(422, "auth_ref must be an opaque secret:// reference")
    row = McpServer(
        tenant_id=ctx.tenant_id,
        name=body.name,
        endpoint=body.endpoint,
        transport=body.transport,
        auth_ref=body.auth_ref,
    )
    session.add(row)
    await session.commit()
    return {"id": str(row.id), "name": row.name}


@router.post("/servers/{server_id}/health")
async def health_server(
    server_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.MCP_EXECUTE)),
    session: AsyncSession = Depends(get_session),
):
    row = (
        await session.execute(
            select(McpServer).where(McpServer.id == server_id, McpServer.tenant_id == ctx.tenant_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "MCP server not found")
    try:
        result = await health(row)
        row.status = "healthy"
        await session.commit()
        return {"ok": True, "result": result}
    except McpError as exc:
        row.status = "unhealthy"
        await session.commit()
        raise HTTPException(502, str(exc)) from exc


@router.post("/servers/{server_id}/discover")
async def discover_server(
    server_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.MCP_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = (
        await session.execute(
            select(McpServer).where(McpServer.id == server_id, McpServer.tenant_id == ctx.tenant_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "MCP server not found")
    try:
        tools = await discover(session, row)
        await session.commit()
    except McpError as exc:
        row.status = "unhealthy"
        await session.commit()
        raise HTTPException(502, str(exc)) from exc
    return [
        {
            "id": str(t.id),
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]


@router.post("/servers/{server_id}/tools/{tool_id}/call")
async def call_tool(
    server_id: uuid.UUID,
    tool_id: uuid.UUID,
    body: CallRequest,
    ctx: TenantContext = Depends(require_permission(Permission.MCP_EXECUTE)),
    session: AsyncSession = Depends(get_session),
):
    server = (
        await session.execute(
            select(McpServer).where(McpServer.id == server_id, McpServer.tenant_id == ctx.tenant_id)
        )
    ).scalar_one_or_none()
    tool = (
        await session.execute(
            select(McpTool).where(
                McpTool.id == tool_id,
                McpTool.tenant_id == ctx.tenant_id,
                McpTool.server_id == server_id,
            )
        )
    ).scalar_one_or_none()
    if server is None or tool is None:
        raise HTTPException(404, "MCP resource not found")
    try:
        result = await execute(session, server, tool, body.arguments)
        await session.commit()
    except McpError as exc:
        raise HTTPException(502, str(exc)) from exc
    return result


# ---------------------------------------------------------------------------
# PART 14 / P14-07 -- the *server* half, per tenant.
#
# The routes above make VoxDesk an MCP *client* (it can call other MCP servers).
# This block exposes VoxDesk itself as an MCP server over the same authenticated,
# permission-scoped surface: a JSON-RPC request in, a JSON-RPC response out.
#
# The tenant is taken from the authenticated context and never from the request
# body, and each tool maps to the same RBAC permission the equivalent REST route
# uses. That is the whole point: an MCP client must not get a wider surface than
# the API key it presents.
# ---------------------------------------------------------------------------

from typing import Any as _Any  # noqa: E402

from app.db.models import Agent, Call  # noqa: E402
from app.mcp.server import CallContext, MCPServer, ToolSpec  # noqa: E402

#: The advertised surface. Handlers are bound per request (they need the caller's
#: session and tenant), so this instance is only used for `tools/list`.
_ADVERTISED = MCPServer()


def _tenant_tools(session: AsyncSession, ctx: TenantContext) -> MCPServer:
    """A server bound to this request's session and tenant."""

    async def list_agents(arguments: dict, _context: CallContext) -> dict:
        rows = (
            (await session.execute(select(Agent).where(Agent.tenant_id == ctx.tenant_id)))
            .scalars()
            .all()
        )
        return {
            "agents": [
                {
                    "agent_id": getattr(row, "agent_id", None) or str(row.id),
                    "name": getattr(row, "name", ""),
                    "status": getattr(row, "status", ""),
                }
                for row in rows[:100]
            ]
        }

    async def get_call(arguments: dict, _context: CallContext) -> dict:
        try:
            call_id = uuid.UUID(str(arguments.get("call_id")))
        except (ValueError, TypeError):
            raise ValueError("call_id must be a UUID")
        row = (
            await session.execute(
                select(Call).where(Call.id == call_id, Call.tenant_id == ctx.tenant_id)
            )
        ).scalar_one_or_none()
        if row is None:
            # Deliberately the same message for "does not exist" and "another
            # tenant's call": a tenancy boundary must not be enumerable.
            raise ValueError("no such call")
        return {
            "call_id": str(row.id),
            "status": str(getattr(row.status, "value", row.status)),
            "duration_seconds": float(row.duration_seconds or 0.0),
            "summary": row.summary,
            "intent": row.intent,
        }

    server = MCPServer()
    for spec in _ADVERTISED.tools():
        handler = {"list_agents": list_agents, "get_call": get_call}.get(spec.name)
        server.register(
            ToolSpec(
                spec.name,
                spec.description,
                spec.input_schema,
                handler=handler,
                scopes=spec.scopes,
            )
        )
    return server


class McpRpcRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str
    params: dict = Field(default_factory=dict)


@router.get("/voxdesk/tools")
async def voxdesk_tools(
    ctx: TenantContext = Depends(require_permission(Permission.MCP_READ)),
):
    """What an MCP client would see from `tools/list`."""
    scopes = ("agents:read", "calls:read")
    return {
        "server": {"name": "voxdesk-mcp", "version": "1.0.0"},
        "tools": [spec.as_dict() for spec in _ADVERTISED.tools()],
        "granted_scopes": list(scopes),
    }


@router.post("/voxdesk/rpc")
async def voxdesk_rpc(
    payload: McpRpcRequest,
    ctx: TenantContext = Depends(require_permission(Permission.MCP_READ)),
    session: AsyncSession = Depends(get_session),
) -> _Any:
    """One JSON-RPC message against the tenant's own tools.

    Only the tools this endpoint implements are registered; the rest return
    `isError` with an explicit "not available on this endpoint" message, which is
    better than an unknown-tool error that a client cannot act on.
    """
    server = _tenant_tools(session, ctx)
    context = CallContext(
        tenant_id=ctx.tenant_id,
        scopes=("agents:read", "calls:read"),
        request_id=str(payload.id or ""),
        client="http",
    )
    reply = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": payload.id,
            "method": payload.method,
            "params": payload.params,
        },
        context,
    )
    if reply is None:
        # A notification (no response by spec). Returning an empty result keeps
        # the HTTP contract simple for clients that expect one.
        return {"jsonrpc": "2.0", "id": payload.id, "result": {}}
    return reply
