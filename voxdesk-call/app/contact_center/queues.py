"""Queue configuration.

Overflow lives in the queue's JSON policy. Creating a member does not mark
the agent available. Capacity, when set, is the presence row's integer, not
a second assignment owner.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.agent_state import ensure
from app.contact_center.exceptions import InvalidSkill, QueueUnavailable
from app.contact_center.models import STRATEGIES, Queue, QueueMember
from app.contact_center.overflow import normalize_policy
from app.contact_center.policies import user_eligible
from app.contact_center.repository import add_queue, get_queue, members, production_environment_id


async def create_queue(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    environment_id: uuid.UUID | None = None,
    priority: int = 0,
    strategy: str = "least_loaded",
    required_skills: list[str] | None = None,
    max_concurrency: int = 10,
    overflow_policy: dict | str | None = None,
    description: str = "",
) -> Queue:
    label = name.strip()
    if not label:
        raise QueueUnavailable("Queue name is required")
    if int(priority) < 0:
        raise QueueUnavailable("Queue priority must be >= 0")
    if int(max_concurrency) < 1:
        raise QueueUnavailable("max_concurrency must be >= 1")
    chosen = (strategy or "least_loaded").strip().lower()
    if chosen not in STRATEGIES:
        raise QueueUnavailable(f"Unknown routing strategy {strategy}")
    if environment_id is None:
        env = await production_environment_id(session, tenant_id)
    else:
        from sqlalchemy import select

        from app.db.models import Environment
        from app.tenancy.isolation import NotFound

        found = (
            await session.execute(
                select(Environment).where(
                    Environment.id == environment_id,
                    Environment.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if found is None:
            raise NotFound()
        env = found.id
    row = Queue(
        tenant_id=tenant_id,
        environment_id=env,
        name=label,
        description=description.strip()[:300],
        priority=int(priority),
        max_concurrency=int(max_concurrency),
        strategy=chosen,
        required_skills=[item.strip().lower() for item in (required_skills or []) if item.strip()],
        overflow_policy=normalize_policy(overflow_policy),
    )
    return await add_queue(session, row)


async def update_queue(session: AsyncSession, row: Queue, changes: dict) -> Queue:
    if "strategy" in changes and changes["strategy"] not in STRATEGIES:
        raise QueueUnavailable(f"Unknown routing strategy {changes['strategy']}")
    if "overflow_policy" in changes:
        changes = dict(changes)
        changes["overflow_policy"] = normalize_policy(changes["overflow_policy"])
    if "priority" in changes and int(changes["priority"]) < 0:
        raise QueueUnavailable("Queue priority must be >= 0")
    if "max_concurrency" in changes and int(changes["max_concurrency"]) < 1:
        raise QueueUnavailable("max_concurrency must be >= 1")
    allowed = {
        "name",
        "description",
        "enabled",
        "priority",
        "max_concurrency",
        "strategy",
        "required_skills",
        "overflow_policy",
    }
    if "required_skills" in changes:
        changes["required_skills"] = [
            str(item).strip().lower() for item in changes["required_skills"] if str(item).strip()
        ]
    for key, value in changes.items():
        if key in allowed:
            setattr(row, key, value)
    from datetime import datetime, timezone

    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return row


async def add_member(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_id: uuid.UUID,
    user_id: uuid.UUID,
    priority: int = 0,
    capacity: int | None = None,
) -> QueueMember:
    await get_queue(session, tenant_id, queue_id)
    reason = await user_eligible(session, tenant_id, user_id)
    if reason:
        raise InvalidSkill(reason)
    existing = {row.user_id: row for row in await members(session, tenant_id, queue_id)}
    if user_id in existing:
        row = existing[user_id]
        row.enabled = True
        row.priority = int(priority)
    else:
        row = QueueMember(
            tenant_id=tenant_id,
            queue_id=queue_id,
            user_id=user_id,
            priority=int(priority),
        )
        session.add(row)
    if capacity is not None:
        if int(capacity) < 1:
            raise QueueUnavailable("capacity must be >= 1")
        presence = await ensure(session, tenant_id, user_id)
        if presence.active_count > int(capacity):
            raise QueueUnavailable("capacity is below active assignments")
        presence.capacity = int(capacity)
    await session.flush()
    return row


async def disable_member(
    session: AsyncSession, tenant_id: uuid.UUID, queue_id: uuid.UUID, user_id: uuid.UUID
) -> QueueMember:
    found = {row.user_id: row for row in await members(session, tenant_id, queue_id)}
    row = found.get(user_id)
    if row is None:
        from app.tenancy.isolation import NotFound

        raise NotFound()
    row.enabled = False
    await session.flush()
    return row
