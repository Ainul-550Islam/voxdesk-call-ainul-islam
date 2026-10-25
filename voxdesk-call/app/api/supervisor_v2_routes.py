"""Supervisor wallboard and interval-accurate agent occupancy reporting."""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.contact_center_v2.acd import realtime_stats_snapshot
from app.contact_center_v2.errors import Validation
from app.contact_center_v2.http import audit, endpoint_errors
from app.contact_center_v2.occupancy import occupancy_report
from app.db.session import get_session

router = APIRouter(prefix="/api/cc2/supervisor", tags=["contact-center-v2-supervisor"])


@router.get("/wallboard")
@endpoint_errors
async def get_wallboard(
    ctx: TenantContext = Depends(require_permission(Permission.SUPERVISOR_READ)),
    session: AsyncSession = Depends(get_session),
):
    result = await realtime_stats_snapshot(session, tenant_id=ctx.tenant_id)
    await audit(session, ctx, event_type="cc2.supervisor.wallboard_read", resource_type="wallboard", detail={"queues": len(result["queues"]), "agents": len(result["agents"])})
    await session.commit()
    return result


@router.get("/occupancy")
@endpoint_errors
async def get_occupancy(
    start: datetime = Query(),
    end: datetime = Query(),
    user_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.SUPERVISOR_READ)),
    session: AsyncSession = Depends(get_session),
):
    if start.tzinfo is None or end.tzinfo is None:
        raise Validation("Occupancy start and end must include timezone offsets")
    result = await occupancy_report(session, tenant_id=ctx.tenant_id, start=start, end=end, user_id=user_id)
    await audit(session, ctx, event_type="cc2.supervisor.occupancy_read", resource_type="agent_occupancy", resource_id=user_id, detail={"window_start": result["window_start"], "window_end": result["window_end"]})
    await session.commit()
    return result


__all__ = ["router"]
