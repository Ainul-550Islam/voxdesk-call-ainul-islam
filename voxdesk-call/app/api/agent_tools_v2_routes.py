"""REST API routes for per-agent integration tool enablement and dry-run validation."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.integrations_v2_routes import PERM_INT_ADMIN, get_int2_http_client
from app.audit.service import record_enterprise_audit
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.integrations_v2_models import ToolEnablement
from app.db.session import get_session
from app.integrations_v2.agent_tools.executor import AgentToolExecutor
from app.integrations_v2.agent_tools.mcp_bridge import list_mcp_v2_tools
from app.integrations_v2.agent_tools.tool_defs import (
    list_agent_tool_specs,
    resolve_tool_spec,
)
from app.integrations_v2.core.connection_store import get_tenant_connection
from app.integrations_v2.core.errors import IntegrationV2Error

router = APIRouter(tags=["agent-tools-v2"])


class ToolEnablementUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(default="", max_length=120)
    tool_name: str = Field(min_length=3, max_length=80)
    connection_id: uuid.UUID
    enabled: bool = True
    max_calls_per_conversation: int = Field(default=3, ge=1, le=20)
    timeout_ms: int = Field(default=3000, ge=250, le=3000)


class DryRunToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    arguments: dict[str, Any] = Field(default_factory=dict)
    agent_id: str = Field(default="", max_length=120)


class ExecuteAgentToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str = Field(min_length=3, max_length=80)
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str = Field(default="", max_length=120)
    agent_id: str = Field(default="", max_length=120)


@router.get("/api/v1/agent-tools/enablements")
@router.get("/api/v1/integrations/v2/agent-tools/catalog")
async def get_agent_tool_enablements_route(
    agent_id: str = Query(default="", max_length=120),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(ToolEnablement).where(ToolEnablement.tenant_id == ctx.tenant.id)
    if agent_id:
        stmt = stmt.where(ToolEnablement.agent_id.in_(("", agent_id)))
    rows = list((await db.execute(stmt)).scalars().all())
    mcp_enabled = await list_mcp_v2_tools(db, tenant_id=ctx.tenant.id, agent_id=agent_id)
    return {
        "specs": list_agent_tool_specs(),
        "enablements": [
            {
                "id": str(r.id),
                "agent_id": r.agent_id,
                "tool_name": r.tool_name,
                "connection_id": str(r.connection_id),
                "enabled": r.enabled,
                "max_calls_per_conversation": r.max_calls_per_conversation,
                "timeout_ms": r.timeout_ms,
            }
            for r in rows
        ],
        "active_mcp_tools": mcp_enabled,
    }


@router.put("/api/v1/agent-tools/enablements")
@router.put("/api/v1/integrations/v2/agent-tools/enablements")
async def upsert_tool_enablement_route(
    req: ToolEnablementUpsertRequest,
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    spec = resolve_tool_spec(req.tool_name)
    if spec is None:
        raise HTTPException(status_code=422, detail=f"Unknown tool_name {req.tool_name!r}")

    try:
        conn_row = await get_tenant_connection(
            db, tenant_id=ctx.tenant.id, connection_id=req.connection_id
        )
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc

    if conn_row.category != spec.category:
        raise HTTPException(
            status_code=422,
            detail=f"Tool {req.tool_name} requires a {spec.category!r} connection, got {conn_row.category!r}",
        )

    existing = (
        await db.execute(
            select(ToolEnablement).where(
                ToolEnablement.tenant_id == ctx.tenant.id,
                ToolEnablement.agent_id == req.agent_id,
                ToolEnablement.tool_name == req.tool_name,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        row = ToolEnablement(
            tenant_id=ctx.tenant.id,
            agent_id=req.agent_id,
            tool_name=req.tool_name,
            connection_id=req.connection_id,
            enabled=req.enabled,
            max_calls_per_conversation=req.max_calls_per_conversation,
            timeout_ms=req.timeout_ms,
        )
        db.add(row)
    else:
        existing.connection_id = req.connection_id
        existing.enabled = req.enabled
        existing.max_calls_per_conversation = req.max_calls_per_conversation
        existing.timeout_ms = req.timeout_ms
        row = existing
    await db.flush()

    await record_enterprise_audit(
        db,
        tenant_id=ctx.tenant.id,
        user_id=ctx.user.id,
        action="integrations_v2.agent_tool.enablement_updated",
        resource_type="int2_tool_enablement",
        resource_id=req.tool_name,
        detail={
            "agent_id": req.agent_id,
            "enabled": req.enabled,
            "connection_id": str(req.connection_id),
        },
    )
    await db.commit()
    return {
        "enablement": {
            "id": str(row.id),
            "agent_id": row.agent_id,
            "tool_name": row.tool_name,
            "connection_id": str(row.connection_id),
            "enabled": row.enabled,
            "max_calls_per_conversation": row.max_calls_per_conversation,
            "timeout_ms": row.timeout_ms,
        }
    }


@router.post("/api/v1/agent-tools/tools/{tool_name}/dry-run")
@router.post("/api/v1/integrations/v2/agent-tools/tools/{tool_name}/dry-run")
async def dry_run_agent_tool_route(
    tool_name: str,
    req: DryRunToolRequest,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Validate tool arguments, RBAC, and tenant enablement without calling any external vendor."""
    executor = AgentToolExecutor(db, http_client=None)
    result = await executor.dry_run(
        tenant_id=ctx.tenant.id,
        tool_name=tool_name,
        arguments=req.arguments,
        agent_id=req.agent_id,
        actor_role=ctx.role,
    )
    await record_enterprise_audit(
        db,
        tenant_id=ctx.tenant.id,
        user_id=ctx.user.id,
        action="integrations_v2.agent_tool.dry_run",
        resource_type="int2_tool",
        resource_id=tool_name,
        detail={"valid": result["valid"]},
    )
    await db.commit()
    return result


@router.post("/api/v1/integrations/v2/agent-tools/execute")
async def execute_agent_tool_route(
    req: ExecuteAgentToolRequest,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    executor = AgentToolExecutor(db, http_client=http_client)
    res = await executor.execute(
        tenant_id=ctx.tenant.id,
        tool_name=req.tool_name,
        arguments=req.arguments,
        call_id=req.call_id,
        agent_id=req.agent_id,
        actor_user_id=ctx.user.id,
        actor_role=ctx.role,
    )
    await db.commit()
    return res.to_dict()
