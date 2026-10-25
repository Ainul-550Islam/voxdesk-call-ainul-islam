"""Queue priority configuration; wait math lives in :mod:`.estimator`."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.models import Queue
from app.contact_center_v2.errors import Validation
from app.contact_center_v2.repository import owned, utcnow
from app.db.contact_center_v2_models import QueuePriorityConfig

from .estimator import estimate_wait_seconds, queue_wait_snapshot


async def configure_priority(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_id: uuid.UUID,
    priority: int = 0,
    strategy: str = "longest_idle",
    max_wait_seconds: int = 300,
    fallback_kind: str = "callback",
    overflow_chain: list[dict[str, Any]] | None = None,
    ewt_config: dict[str, Any] | None = None,
) -> QueuePriorityConfig:
    queue = await owned(session, Queue, queue_id, tenant_id, for_update=True)
    if priority < 0 or max_wait_seconds < 1 or max_wait_seconds > 86_400:
        raise Validation("Invalid queue priority or maximum wait")
    if fallback_kind not in {"ai_agent", "voicemail", "callback", "queue"}:
        raise Validation("Unknown queue fallback")
    if strategy not in {"least_loaded", "round_robin", "longest_idle", "skill_first", "priority_first"}:
        raise Validation("Unknown ACD strategy")
    from app.contact_center_v2.acd.router import OVERFLOW_ACTIONS
    from app.telephony import phone

    chain = list(overflow_chain or [])[:10]
    if len(list(overflow_chain or [])) > 10:
        raise Validation("Overflow chains are limited to ten steps")
    for step in chain:
        kind = str(step.get("kind") or "")
        after = step.get("after_seconds", 0)
        waiting = step.get("waiting", 0)
        if (
            kind not in OVERFLOW_ACTIONS
            or not isinstance(after, int)
            or not isinstance(waiting, int)
            or after < 0
            or waiting < 0
            or not (after or waiting)
        ):
            raise Validation("Each overflow-chain step needs a valid kind and positive threshold")
        if kind == "queue":
            try:
                uuid.UUID(str(step.get("queue_id") or step.get("overflow_queue_id") or ""))
            except ValueError as exc:
                raise Validation("Queue overflow steps require a target queue UUID") from exc
        if kind == "escalate":
            step["escalation_number"] = phone.normalize(str(step.get("escalation_number") or ""))
    row = (
        await session.execute(
            select(QueuePriorityConfig).where(
                QueuePriorityConfig.tenant_id == tenant_id,
                QueuePriorityConfig.queue_id == queue_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = QueuePriorityConfig(tenant_id=tenant_id, environment_id=queue.environment_id, queue_id=queue_id)
        session.add(row)
    row.priority = int(priority)
    row.strategy = strategy
    row.max_wait_seconds = int(max_wait_seconds)
    row.fallback_kind = fallback_kind
    row.overflow_chain = chain
    row.ewt_config = dict(ewt_config or {})
    row.updated_at = utcnow()
    queue.priority = int(priority)
    queue.strategy = strategy
    await session.flush()
    return row


async def priority_for_queue(
    session: AsyncSession, *, tenant_id: uuid.UUID, queue_id: uuid.UUID
) -> QueuePriorityConfig | None:
    return (
        await session.execute(
            select(QueuePriorityConfig).where(
                QueuePriorityConfig.tenant_id == tenant_id,
                QueuePriorityConfig.queue_id == queue_id,
            )
        )
    ).scalar_one_or_none()


__all__ = ["configure_priority", "estimate_wait_seconds", "priority_for_queue", "queue_wait_snapshot"]
