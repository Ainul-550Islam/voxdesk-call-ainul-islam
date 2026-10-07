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
