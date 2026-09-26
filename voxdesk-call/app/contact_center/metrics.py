"""Queue metrics from persisted rows only.

Waiting and assigned counts are SQL counts. Occupancy is the count of agents
whose presence is available and whose heartbeat is fresh. There is no sampled
or invented statistic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.models import AgentPresence
from app.contact_center.presence import is_fresh
from app.contact_center.repository import assigned_count, members, waiting_count


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def snapshot(session: AsyncSession, tenant_id: uuid.UUID, queue_id: uuid.UUID) -> dict[str, int]:
    roster = await members(session, tenant_id, queue_id)
    user_ids = [row.user_id for row in roster if row.enabled]
    available = 0
    if user_ids:
        rows = (
            await session.execute(
                select(AgentPresence).where(
                    AgentPresence.tenant_id == tenant_id,
                    AgentPresence.user_id.in_(user_ids),
                )
            )
        ).scalars().all()
        moment = _now()
        available = sum(1 for row in rows if row.state == "available" and is_fresh(row, now=moment))
    return {
        "waiting": await waiting_count(session, tenant_id, queue_id),
        "assigned": await assigned_count(session, tenant_id, queue_id),
        "available_agents": available,
        "members": len(user_ids),
    }
