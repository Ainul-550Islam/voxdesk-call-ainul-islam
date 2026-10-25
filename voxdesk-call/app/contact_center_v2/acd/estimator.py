"""Bounded, data-sourced queue wait estimation with explicit uncertainty."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.models import Queue, QueueEntry
from app.contact_center_v2.occupancy import available_agent_ids
from app.contact_center_v2.repository import as_utc, owned, utcnow
from app.db.contact_center_v2_models import QueuePriorityConfig


def estimate_wait_seconds(
    *,
    waiting: int,
    active_calls: int,
    available_agents: int,
    median_talk_seconds: Decimal | float,
    max_wait_seconds: int,
) -> int:
    """Return a bounded estimate from observed queue/call values, not a promise."""
    if waiting <= 0 or available_agents > 0:
        return 0
    service = max(1.0, float(median_talk_seconds))
    agents = max(1, int(active_calls))
    estimate = int((waiting * service) / agents)
    return min(max(0, int(max_wait_seconds)), max(0, estimate))


async def queue_wait_snapshot(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_id: uuid.UUID,
    now: datetime | None = None,
) -> dict[str, Any]:
    queue = await owned(session, Queue, queue_id, tenant_id)
    moment = now or utcnow()
    entries = (
        (
            await session.execute(
                select(QueueEntry).where(
                    QueueEntry.tenant_id == tenant_id,
                    QueueEntry.queue_id == queue_id,
                    QueueEntry.status.in_(("waiting", "assigned")),
                )
            )
        )
        .scalars()
        .all()
    )
    config = (
        await session.execute(
            select(QueuePriorityConfig).where(
                QueuePriorityConfig.tenant_id == tenant_id,
                QueuePriorityConfig.queue_id == queue_id,
            )
        )
    ).scalar_one_or_none()
    waiting = [entry for entry in entries if entry.status == "waiting"]
    oldest = min((as_utc(entry.enqueued_at) for entry in waiting), default=None)
    median = Decimal(str((config.ewt_config or {}).get("median_talk_seconds", 180))) if config else Decimal("180")
    available = await available_agent_ids(
        session,
        tenant_id,
        now=moment,
        require_softphone=True,
        environment_id=queue.environment_id,
    )
    estimate = estimate_wait_seconds(
        waiting=len(waiting),
        active_calls=len(entries) - len(waiting),
        available_agents=len(available),
        median_talk_seconds=median,
        max_wait_seconds=config.max_wait_seconds if config else 300,
    )
    return {
        "queue_id": str(queue.id),
        "waiting": len(waiting),
        "assigned": len(entries) - len(waiting),
        "oldest_wait_seconds": max(0, int((moment - oldest).total_seconds())) if oldest else 0,
        "estimated_wait_seconds": estimate,
        "estimate_method": "observed_queue_talk_time_v1",
        "priority": config.priority if config else queue.priority,
    }


__all__ = ["estimate_wait_seconds", "queue_wait_snapshot"]
