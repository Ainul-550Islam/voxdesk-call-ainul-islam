"""Agent presence, after-call-work and interval APIs."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.contact_center.agent_state import ensure
from app.contact_center.service import set_state
from app.contact_center_v2.acd.occupancy import occupancy_report, state_intervals
from app.contact_center_v2.assist.wrapup_service import pending_wrapups, submit_call_wrapup
from app.contact_center_v2.dialer.wrapup_guard import assert_wrapup_complete
from app.contact_center_v2.errors import PermissionDenied, Validation
from app.contact_center_v2.http import audit, can_supervise, endpoint_errors
from app.contact_center_v2.schemas import AgentStateBody, WrapupBody
from app.db.contact_center_v2_models import AgentStateInterval
from app.db.session import get_session

router = APIRouter(prefix="/api/cc2/agent-states", tags=["contact-center-v2-agent-states"])


@router.get("/me")
@endpoint_errors
async def get_my_state(
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await ensure(session, ctx.tenant_id, ctx.user_id)
    interval = (
        await session.execute(
            select(AgentStateInterval).where(
                AgentStateInterval.tenant_id == ctx.tenant_id,
                AgentStateInterval.user_id == ctx.user_id,
                AgentStateInterval.ended_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    source = interval.source_id if interval is not None else ""
    reason_code = source.split(":", 1)[1] if source.startswith("agent_state:") else None
    await audit(session, ctx, event_type="cc2.agent_state.read", resource_type="agent_presence", resource_id=ctx.user_id, detail={"state": row.state})
    await session.commit()
    return {"presence": row.as_dict(), "reason_code": reason_code}


@router.post("/me/state")
@router.post("/me")
@endpoint_errors
async def change_state(
    payload: AgentStateBody,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    if payload.state == "available":
        await assert_wrapup_complete(session, tenant_id=ctx.tenant_id, agent_user_id=ctx.user_id)
    row, outcome = await set_state(
        session,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        target=payload.state,
        actor_id=ctx.user_id,
        supervisor=False,
        capacity=payload.capacity,
    )
    if outcome == "applied" and payload.reason_code:
        interval = (
            await session.execute(
                select(AgentStateInterval).where(
                    AgentStateInterval.tenant_id == ctx.tenant_id,
                    AgentStateInterval.user_id == ctx.user_id,
                    AgentStateInterval.ended_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if interval is not None:
            interval.source_id = f"agent_state:{payload.reason_code}"
            await session.flush()
    await audit(session, ctx, event_type="cc2.agent_state.changed", resource_type="agent_presence", resource_id=ctx.user_id, detail={"state": row.state, "outcome": outcome, "reason_code": payload.reason_code})
    await session.commit()
    return {"presence": row.as_dict(), "outcome": outcome, "reason_code": payload.reason_code if outcome == "applied" else None}


@router.get("/wrapups/pending")
@endpoint_errors
async def get_pending_wrapups(
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = await pending_wrapups(session, tenant_id=ctx.tenant_id, agent_user_id=ctx.user_id)
    await audit(session, ctx, event_type="cc2.wrapup.pending_read", resource_type="wrapup_draft", detail={"count": len(rows)})
    await session.commit()
    return {"wrapups": [{
        "id": str(row.id), "call_id": str(row.call_id), "status": row.status,
        "summary_redacted": row.summary_redacted, "disposition": row.disposition,
        "tags": list(row.tags or []), "crm_status": row.crm_status,
        "deadline_at": row.deadline_at.isoformat(), "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
    } for row in rows]}


@router.post("/wrapups")
@endpoint_errors
async def submit_state_wrapup(
    payload: WrapupBody,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await submit_call_wrapup(
        session, tenant_id=ctx.tenant_id, call_id=payload.call_id,
        agent_user_id=ctx.user_id, disposition=payload.disposition,
        summary_redacted=payload.summary_redacted, tags=payload.tags,
    )
    await audit(session, ctx, event_type="cc2.wrapup.submitted", resource_type="wrapup_draft", resource_id=row.id, environment_id=row.environment_id, detail={"call_id": str(row.call_id), "disposition": row.disposition, "crm_status": row.crm_status})
    await session.commit()
    return {"wrapup": {"id": str(row.id), "call_id": str(row.call_id), "status": row.status, "disposition": row.disposition, "crm_status": row.crm_status, "crm_reference": row.crm_reference, "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None}}


@router.get("/occupancy")
@endpoint_errors
async def get_occupancy(
    start: datetime = Query(...),
    end: datetime = Query(...),
    user_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_READ)),
    session: AsyncSession = Depends(get_session),
):
    if user_id is not None and user_id != ctx.user_id and not can_supervise(ctx):
        raise PermissionDenied("Supervisor permission is required to read another agent's occupancy")
    target = user_id or ctx.user_id
    result = await occupancy_report(session, tenant_id=ctx.tenant_id, start=start, end=end, user_id=target)
    await audit(session, ctx, event_type="cc2.agent_state.occupancy_read", resource_type="agent_occupancy", resource_id=target, detail={"window_start": result["window_start"], "window_end": result["window_end"]})
    await session.commit()
    return result


@router.get("/intervals")
@endpoint_errors
async def get_intervals(
    user_id: uuid.UUID | None = Query(default=None),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_READ)),
    session: AsyncSession = Depends(get_session),
):
    if start is not None and end is not None and start >= end:
        raise Validation("Interval start must precede end")
    if user_id is not None and user_id != ctx.user_id and not can_supervise(ctx):
        raise PermissionDenied("Supervisor permission is required to read another agent's intervals")
    rows = await state_intervals(session, tenant_id=ctx.tenant_id, user_id=user_id or ctx.user_id, start=start, end=end)
    await audit(session, ctx, event_type="cc2.agent_state.intervals_read", resource_type="agent_state_interval", detail={"count": len(rows), "user_id": str(user_id or ctx.user_id)})
    await session.commit()
    return {"intervals": [{
        "id": str(row.id), "user_id": str(row.user_id), "state": row.state,
        "started_at": row.started_at.isoformat(), "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "source_id": row.source_id,
    } for row in rows]}


__all__ = ["router"]
