"""Agent export / import API (PART 12, P12-24).

`GET .../export` returns a `voxdesk.agent/1` document with secrets replaced by references.
`POST .../import` is a **dry run unless `?apply=true`** — an import that silently wrote agents would
be the single most dangerous endpoint in the product.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.models import Agent
from app.db.session import get_session
from app.services.agent_portability_service import (
    IMPORT_MODES,
    PortabilityError,
    export_agent,
    import_agent,
)

router = APIRouter(tags=["agent-portability"])


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundle: dict[str, Any]
    mode: str = "create"
    target_agent_id: uuid.UUID | None = None
    supplied_secrets: dict[str, str] = {}


@router.get("/api/agents/{agent_id}/export")
async def export_agent_bundle(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    agent = (
        await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == ctx.tenant_id)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")

    config = dict(agent.current_draft_config or {})
    flow = config.pop("flow_graph", None) or {}
    return export_agent(
        agent_id=str(agent.id),
        config=config,
        flow=flow,
        exported_by=str(getattr(ctx, "user_id", "") or ""),
    )


@router.post("/api/agents/import")
async def import_agent_bundle(
    payload: ImportRequest,
    apply: bool = Query(False, description="false = plan only"),
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.mode not in IMPORT_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"mode must be one of {list(IMPORT_MODES)}",
        )

    existing_config = None
    if payload.mode == "merge":
        if payload.target_agent_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="merge needs target_agent_id",
            )
        target = (
            await session.execute(
                select(Agent).where(
                    Agent.id == payload.target_agent_id, Agent.tenant_id == ctx.tenant_id
                )
            )
        ).scalar_one_or_none()
        if target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
        existing_config = dict(target.current_draft_config or {})

    try:
        plan = import_agent(
            payload.bundle,
            mode=payload.mode,
            existing_config=existing_config,
            supplied_secrets=payload.supplied_secrets,
            dry_run=not apply,
        )
    except PortabilityError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if not apply or plan.unresolved_secrets:
        return plan.as_dict()

    bundle_config = dict((payload.bundle.get("agent") or {}).get("config") or {})
    if payload.mode == "create":
        agent = Agent(
            tenant_id=ctx.tenant_id,
            name=plan.name,
            current_draft_config=bundle_config,
        )
        session.add(agent)
        await session.commit()
        return {**plan.as_dict(), "created_agent_id": str(agent.id)}

    target.current_draft_config = {**(existing_config or {}), **bundle_config}
    await session.commit()
    return {**plan.as_dict(), "updated_agent_id": str(payload.target_agent_id)}
