# File: app/api/tool_registry_routes.py — Missing API: agent-specific tool/function registry reusable functions, schemas, auth bindings, enable/disable
"""
Agent-specific tool/function registry API.
Closes gap 24: reusable functions, schemas, auth bindings, enable/disable.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.db.enterprise_models import AgentTool

router = APIRouter(prefix="/api/agents", tags=["tool-registry"])

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=(), populate_by_name=True)

class ToolCreate(_Strict):
    name: str = Field(min_length=1, max_length=120, pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    description: str = Field(default="", max_length=2000)
    tool_schema: dict = Field(default_factory=dict, alias="schema", description="JSON schema for tool parameters")
    auth_binding: dict = Field(default_factory=dict, description="Auth binding: type, secret_ref, etc.")
    is_enabled: bool = True

class ToolUpdate(_Strict):
    description: Optional[str] = None
    tool_schema: Optional[dict] = Field(default=None, alias="schema")
    auth_binding: Optional[dict] = None
    is_enabled: Optional[bool] = None

class ToolOut(_Strict):
    id: str
    tenant_id: str
    agent_id: str
    name: str
    description: str
    tool_schema: dict = Field(alias="schema")
    auth_binding: dict
    is_enabled: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

def _now() -> datetime:
    return datetime.now(timezone.utc)

def _to_out(row: AgentTool) -> ToolOut:
    d = row.as_dict()
    return ToolOut(
        id=d["id"],
        tenant_id=d["tenant_id"],
        agent_id=d["agent_id"],
        name=d["name"],
        description=d["description"],
        schema=d["schema"],
        auth_binding=d["auth_binding"],
        is_enabled=d["is_enabled"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )

@router.post("/{agent_id}/tools", response_model=ToolOut, status_code=201)
async def create_tool(
    agent_id: str,
    payload: ToolCreate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/agents/{id}/tools — Create reusable function with schema and auth binding."""
    # Check duplicate
    existing = (
        await session.execute(
            select(AgentTool).where(AgentTool.tenant_id == ctx.tenant_id, AgentTool.agent_id == agent_id, AgentTool.name == payload.name)
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="tool with this name already exists for agent")

    # Validate schema is valid JSON schema (basic check)
    if payload.tool_schema:
        if not isinstance(payload.tool_schema, dict):
            raise HTTPException(status_code=422, detail="schema must be object")
        if "type" not in payload.tool_schema and "properties" not in payload.tool_schema:
            # Allow empty schema, but if present should have type or properties
            pass

    tool = AgentTool(
        tenant_id=ctx.tenant_id,
        agent_id=agent_id,
        name=payload.name,
        description=payload.description,
        schema=payload.tool_schema,
        auth_binding=payload.auth_binding,
        is_enabled=payload.is_enabled,
    )
    session.add(tool)
    await session.commit()
    await session.refresh(tool)
    return _to_out(tool)

@router.get("/{agent_id}/tools", response_model=dict)
async def list_tools(
    agent_id: str,
    is_enabled: Optional[bool] = Query(default=None),
    search: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    scope = [AgentTool.tenant_id == ctx.tenant_id, AgentTool.agent_id == agent_id]
    if is_enabled is not None:
        scope.append(AgentTool.is_enabled == is_enabled)
    if search:
        scope.append(AgentTool.name.ilike(f"%{search}%"))

    total = (await session.execute(select(func.count(AgentTool.id)).where(*scope))).scalar() or 0
    rows = (
        await session.execute(
            select(AgentTool).where(*scope).order_by(AgentTool.created_at.desc()).offset(offset).limit(limit)
        )
    ).scalars().all()
    return {"tools": [r.as_dict() for r in rows], "total": int(total), "limit": limit, "offset": offset}

@router.get("/{agent_id}/tools/{tool_id}", response_model=ToolOut)
async def get_tool(
    agent_id: str,
    tool_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(AgentTool, tool_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="tool not found")
    return _to_out(row)

@router.patch("/{agent_id}/tools/{tool_id}", response_model=ToolOut)
async def update_tool(
    agent_id: str,
    tool_id: uuid.UUID,
    payload: ToolUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(AgentTool, tool_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="tool not found")
    if payload.description is not None:
        row.description = payload.description
    if payload.tool_schema is not None:
        row.schema = payload.tool_schema
    if payload.auth_binding is not None:
        row.auth_binding = payload.auth_binding
    if payload.is_enabled is not None:
        row.is_enabled = payload.is_enabled
    row.updated_at = _now()
    await session.commit()
    await session.refresh(row)
    return _to_out(row)

@router.delete("/{agent_id}/tools/{tool_id}")
async def delete_tool(
    agent_id: str,
    tool_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(AgentTool, tool_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="tool not found")
    await session.delete(row)
    await session.commit()
    return {"id": str(tool_id), "deleted": True}

@router.post("/{agent_id}/tools/{tool_id}/enable", response_model=ToolOut)
async def enable_tool(
    agent_id: str,
    tool_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(AgentTool, tool_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="tool not found")
    row.is_enabled = True
    row.updated_at = _now()
    await session.commit()
    await session.refresh(row)
    return _to_out(row)

@router.post("/{agent_id}/tools/{tool_id}/disable", response_model=ToolOut)
async def disable_tool(
    agent_id: str,
    tool_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(AgentTool, tool_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="tool not found")
    row.is_enabled = False
    row.updated_at = _now()
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


class ToolTestRequest(_Strict):
    arguments: dict = Field(default_factory=dict)


@router.post("/{agent_id}/tools/{tool_id}/test")
async def test_tool_execution(
    agent_id: str,
    tool_id: uuid.UUID,
    payload: ToolTestRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """POST /api/agents/{id}/tools/{tool_id}/test — Execute a live test call of a custom HTTP tool (2D)."""
    from app.services.tool_registry_service import test_agent_custom_tool

    row = await session.get(AgentTool, tool_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.agent_id != agent_id:
        raise HTTPException(status_code=404, detail="tool not found")
    return await test_agent_custom_tool(
        session,
        tenant_id=ctx.tenant_id,
        tool=row,
        arguments=payload.arguments,
    )
