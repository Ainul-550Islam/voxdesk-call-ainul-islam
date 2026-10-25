"""Skills/priority ACD policy, softphone routing, overflow, callbacks, and state."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.contact_center_v2.acd import (
    configure_priority,
    policy_payload,
    route_queue,
    queue_wait_snapshot,
    realtime_stats_snapshot,
    set_agent_skill,
    set_queue_requirements,
    sweep_overflow,
    upsert_policy,
    upsert_skill,
    stream_wallboard,
)
from app.contact_center_v2.acd.callbacks import (
    cancel_callback,
    list_callbacks,
    reschedule_callback,
    schedule_callback,
)
from app.contact_center_v2.http import audit, endpoint_errors
from app.contact_center_v2.repository import owned
from app.contact_center_v2.schemas import (
    AcdPolicyBody, AgentSkillBody, CallbackAction, CallbackBody, CallbackRescheduleBody,
    PriorityBody, QueueSkillsBody, RouteNextBody, SkillBody,
)
from app.contact_center_v2.softphone import call_payload
from app.db.contact_center_v2_models import AcdQueuePolicy
from app.db.session import get_session

router = APIRouter(prefix="/api/cc2/acd", tags=["contact-center-v2-acd"])


@router.put("/queues/{queue_id}/policy")
@endpoint_errors
async def put_policy(
    queue_id: uuid.UUID,
    payload: AcdPolicyBody,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    queue, _ = await upsert_policy(session, tenant_id=ctx.tenant_id, queue_id=queue_id, payload=payload.model_dump())
    extension = (await session.execute(select(AcdQueuePolicy).where(AcdQueuePolicy.tenant_id == ctx.tenant_id, AcdQueuePolicy.queue_id == queue_id))).scalar_one()
    await audit(session, ctx, event_type="cc2.acd.policy_updated", resource_type="acd_queue", resource_id=queue.id, detail={"strategy": queue.strategy, "overflow_kind": extension.overflow_kind})
    await session.commit()
    return {"policy": policy_payload(queue, extension)}


@router.get("/queues/{queue_id}/policy")
@endpoint_errors
async def get_policy(
    queue_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_READ)),
    session: AsyncSession = Depends(get_session),
):
    from app.contact_center.models import Queue
    queue = await owned(session, Queue, queue_id, ctx.tenant_id)
    extension = (await session.execute(select(AcdQueuePolicy).where(AcdQueuePolicy.tenant_id == ctx.tenant_id, AcdQueuePolicy.queue_id == queue_id))).scalar_one_or_none()
    await audit(session, ctx, event_type="cc2.acd.policy_read", resource_type="acd_queue", resource_id=queue.id)
    await session.commit()
    return {"policy": policy_payload(queue, extension)}


@router.get("/queues/{queue_id}/ewt")
@endpoint_errors
async def get_queue_ewt(
    queue_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_READ)),
    session: AsyncSession = Depends(get_session),
):
    result = await queue_wait_snapshot(session, tenant_id=ctx.tenant_id, queue_id=queue_id)
    await audit(session, ctx, event_type="cc2.acd.ewt_read", resource_type="acd_queue", resource_id=queue_id, detail={"waiting": result["waiting"], "estimate_method": result["estimate_method"]})
    await session.commit()
    return result


@router.post("/queues/{queue_id}/route")
@endpoint_errors
async def route(
    queue_id: uuid.UUID,
    payload: RouteNextBody,
    ctx: TenantContext = Depends(require_permission(Permission.ROUTING_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    result, softphone = await route_queue(session, tenant_id=ctx.tenant_id, queue_id=queue_id, actor_id=ctx.user_id, call_id=payload.call_id, priority=payload.priority)
    await audit(session, ctx, event_type="cc2.acd.routed", resource_type="acd_queue_entry", resource_id=result.entry.id, detail={"outcome": result.outcome, "decision_id": str(result.decision.id)})
    await session.commit()
    return {"routing": result.as_dict(), "softphone_call": await call_payload(session, softphone) if softphone else None}


@router.post("/queues/{queue_id}/overflow/sweep")
@endpoint_errors
async def overflow_sweep(
    queue_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    result = await sweep_overflow(session, tenant_id=ctx.tenant_id, queue_id=queue_id)
    await audit(session, ctx, event_type="cc2.acd.overflow_swept", resource_type="acd_queue", resource_id=queue_id, detail=result)
    await session.commit()
    return result


@router.post("/skills", status_code=201)
@endpoint_errors
async def create_skill(
    payload: SkillBody,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await upsert_skill(session, tenant_id=ctx.tenant_id, name=payload.name, enabled=payload.enabled)
    await audit(session, ctx, event_type="cc2.acd.skill_upserted", resource_type="skill", resource_id=row.id, detail={"name": row.name, "enabled": row.enabled})
    await session.commit()
    return {"skill": row.as_dict()}


@router.put("/agents/{user_id}/skills")
@endpoint_errors
async def put_agent_skill(
    user_id: uuid.UUID,
    payload: AgentSkillBody,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await set_agent_skill(session, tenant_id=ctx.tenant_id, user_id=user_id, skill_id=payload.skill_id, proficiency=payload.proficiency, weight=payload.weight, enabled=payload.enabled)
    await audit(session, ctx, event_type="cc2.acd.agent_skill_updated", resource_type="agent_skill", resource_id=row.id, detail={"user_id": str(user_id), "skill_id": str(payload.skill_id), "proficiency": row.proficiency})
    await session.commit()
    return {"agent_skill": row.as_dict()}


@router.put("/queues/{queue_id}/skills")
@endpoint_errors
async def put_queue_skills(
    queue_id: uuid.UUID,
    payload: QueueSkillsBody,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    rows = await set_queue_requirements(session, tenant_id=ctx.tenant_id, queue_id=queue_id, requirements=[item.model_dump() for item in payload.requirements])
    await audit(session, ctx, event_type="cc2.acd.queue_skills_updated", resource_type="acd_queue", resource_id=queue_id, detail={"requirements": len(rows)})
    await session.commit()
    return {"requirements": [{"skill_id": str(row.skill_id), "min_proficiency": row.min_proficiency, "required": row.required, "weight": row.weight} for row in rows]}


@router.put("/queues/{queue_id}/priority")
@endpoint_errors
async def put_priority(
    queue_id: uuid.UUID,
    payload: PriorityBody,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await configure_priority(session, tenant_id=ctx.tenant_id, queue_id=queue_id, **payload.model_dump())
    await audit(session, ctx, event_type="cc2.acd.priority_updated", resource_type="acd_queue", resource_id=queue_id, detail={"priority": row.priority, "strategy": row.strategy})
    await session.commit()
    return {"priority": {"queue_id": str(row.queue_id), "priority": row.priority, "strategy": row.strategy, "max_wait_seconds": row.max_wait_seconds, "fallback_kind": row.fallback_kind, "overflow_chain": list(row.overflow_chain or []), "ewt_config": dict(row.ewt_config or {})}}


@router.get("/wallboard")
@endpoint_errors
async def get_acd_wallboard(
    ctx: TenantContext = Depends(require_permission(Permission.SUPERVISOR_READ)),
    session: AsyncSession = Depends(get_session),
):
    result = await realtime_stats_snapshot(session, tenant_id=ctx.tenant_id)
    await audit(session, ctx, event_type="cc2.acd.wallboard_read", resource_type="wallboard", detail={"queues": len(result["queues"]), "agents": len(result["agents"])})
    await session.commit()
    return result


@router.get("/wallboard/stream")
@endpoint_errors
async def stream_acd_wallboard(
    ctx: TenantContext = Depends(require_permission(Permission.SUPERVISOR_READ)),
    session: AsyncSession = Depends(get_session),
):
    await audit(session, ctx, event_type="cc2.acd.wallboard_stream", resource_type="wallboard", detail={"transport": "sse"})
    await session.commit()
    return StreamingResponse(
        stream_wallboard(tenant_id=ctx.tenant_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/callbacks", status_code=201)
@endpoint_errors
async def create_callback(
    payload: CallbackBody,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row, created = await schedule_callback(session, tenant_id=ctx.tenant_id, queue_entry_id=payload.queue_entry_id, requested_by=ctx.user_id, scheduled_at=payload.scheduled_at, timezone_name=payload.timezone, note=payload.note)
    await audit(session, ctx, event_type="cc2.callback.scheduled", resource_type="callback_request", resource_id=row.id, detail={"created": created, "scheduled_at": row.scheduled_at.isoformat()})
    await session.commit()
    return {"callback": row.as_dict(), "created": created}


@router.get("/callbacks")
@endpoint_errors
async def callbacks(
    status: str | None = Query(default=None),
    mine: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = await list_callbacks(
        session,
        tenant_id=ctx.tenant_id,
        status=status,
        agent_user_id=ctx.user_id if mine else None,
        limit=limit,
    )
    await audit(session, ctx, event_type="cc2.callback.list", resource_type="callback_request", detail={"count": len(rows)})
    await session.commit()
    return {"callbacks": [row.as_dict() for row in rows]}


@router.post("/callbacks/{callback_id}/reschedule")
@endpoint_errors
async def reschedule(
    callback_id: uuid.UUID,
    payload: CallbackRescheduleBody,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row, changed = await reschedule_callback(session, tenant_id=ctx.tenant_id, callback_id=callback_id, scheduled_at=payload.scheduled_at, timezone_name=payload.timezone)
    await audit(session, ctx, event_type="cc2.callback.rescheduled", resource_type="callback_request", resource_id=row.id, detail={"changed": changed, "scheduled_at": row.scheduled_at.isoformat(), "timezone": row.timezone})
    await session.commit()
    return {"callback": row.as_dict(), "changed": changed}


@router.post("/callbacks/{callback_id}/cancel")
@endpoint_errors
async def cancel(
    callback_id: uuid.UUID,
    payload: CallbackAction,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await cancel_callback(session, tenant_id=ctx.tenant_id, callback_id=callback_id)
    await audit(session, ctx, event_type="cc2.callback.cancelled", resource_type="callback_request", resource_id=row.id, detail={"reason": payload.reason})
    await session.commit()
    return {"callback": row.as_dict()}


__all__ = ["router"]
