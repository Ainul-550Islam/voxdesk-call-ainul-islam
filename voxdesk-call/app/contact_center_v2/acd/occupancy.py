"""ACD-facing interval and occupancy accounting primitives."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.occupancy import occupancy_report, record_transition, record_work_mode
from app.db.contact_center_v2_models import AgentStateInterval


async def state_intervals(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[AgentStateInterval]:
    conditions = [AgentStateInterval.tenant_id == tenant_id]
    if user_id is not None:
        conditions.append(AgentStateInterval.user_id == user_id)
    if start is not None:
        conditions.append((AgentStateInterval.ended_at.is_(None)) | (AgentStateInterval.ended_at > start))
    if end is not None:
        conditions.append(AgentStateInterval.started_at < end)
    return list(
        (
            await session.execute(
                select(AgentStateInterval).where(*conditions).order_by(AgentStateInterval.started_at)
            )
        )
        .scalars()
        .all()
    )


__all__ = ["occupancy_report", "record_transition", "record_work_mode", "state_intervals"]
