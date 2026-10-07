"""Tenant-scoped reads and writes for ACD rows.

Every query names ``tenant_id``. A foreign id is a miss, not a leak.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.exceptions import DuplicateAssignment, QueueUnavailable, StaleState
from app.contact_center.models import (
    AgentPresence,
    AgentSkill,
    Queue,
    QueueEntry,
    QueueMember,
    RoutingAssignment,
    RoutingCursor,
    RoutingDecision,
    Skill,
)
from app.db.models import Environment
from app.tenancy.isolation import NotFound


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def production_environment_id(session: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID:
    found = (
        await session.execute(
            select(Environment.id).where(
                Environment.tenant_id == tenant_id,
                Environment.kind == "production",
            )
        )
    ).scalar_one_or_none()
    if found is None:
        raise NotFound()
    return found


async def get_queue(session: AsyncSession, tenant_id: uuid.UUID, queue_id: uuid.UUID) -> Queue:
    row = await session.get(Queue, queue_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def list_queues(session: AsyncSession, tenant_id: uuid.UUID) -> list[Queue]:
    return list(
        (
            await session.execute(
                select(Queue).where(Queue.tenant_id == tenant_id).order_by(Queue.priority.desc(), Queue.name)
            )
        ).scalars().all()
    )


async def add_queue(session: AsyncSession, row: Queue) -> Queue:
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        raise QueueUnavailable("Queue name already exists in this environment") from exc
    return row


async def members(session: AsyncSession, tenant_id: uuid.UUID, queue_id: uuid.UUID) -> list[QueueMember]:
    return list(
        (
            await session.execute(
                select(QueueMember).where(
                    QueueMember.tenant_id == tenant_id,
                    QueueMember.queue_id == queue_id,
                )
            )
        ).scalars().all()
    )


async def get_skill(session: AsyncSession, tenant_id: uuid.UUID, skill_id: uuid.UUID) -> Skill:
    row = await session.get(Skill, skill_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def skill_by_name(session: AsyncSession, tenant_id: uuid.UUID, name: str) -> Skill | None:
    return (
        await session.execute(select(Skill).where(Skill.tenant_id == tenant_id, Skill.name == name))
    ).scalar_one_or_none()


async def list_skills(session: AsyncSession, tenant_id: uuid.UUID) -> list[Skill]:
    return list(
        (await session.execute(select(Skill).where(Skill.tenant_id == tenant_id).order_by(Skill.name))).scalars().all()
    )


async def skills_for_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> dict[uuid.UUID, Skill]:
    rows = await list_skills(session, tenant_id)
    return {row.id: row for row in rows}


async def agent_skills(session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict[uuid.UUID, AgentSkill]:
    rows = (
        await session.execute(
            select(AgentSkill).where(AgentSkill.tenant_id == tenant_id, AgentSkill.user_id == user_id)
        )
    ).scalars().all()
    return {row.skill_id: row for row in rows}


async def waiting_count(session: AsyncSession, tenant_id: uuid.UUID, queue_id: uuid.UUID) -> int:
    value = (
        await session.execute(
            select(func.count())
            .select_from(QueueEntry)
            .where(
                QueueEntry.tenant_id == tenant_id,
                QueueEntry.queue_id == queue_id,
                QueueEntry.status == "waiting",
            )
        )
    ).scalar_one()
    return int(value or 0)


async def assigned_count(session: AsyncSession, tenant_id: uuid.UUID, queue_id: uuid.UUID) -> int:
    value = (
        await session.execute(
            select(func.count())
            .select_from(QueueEntry)
            .where(
                QueueEntry.tenant_id == tenant_id,
                QueueEntry.queue_id == queue_id,
                QueueEntry.status == "assigned",
            )
        )
    ).scalar_one()
    return int(value or 0)


async def claim_waiting(session: AsyncSession, entry: QueueEntry, *, now: datetime) -> None:
    expected = entry.version
    result = await session.execute(
        update(QueueEntry)
        .where(
            QueueEntry.id == entry.id,
            QueueEntry.tenant_id == entry.tenant_id,
            QueueEntry.status == "waiting",
            QueueEntry.version == expected,
        )
        .values(status="assigned", version=expected + 1, assigned_at=now)
    )
    if result.rowcount != 1:
        raise DuplicateAssignment("Queue item already has an owner")
    await session.refresh(entry)


async def reserve_agent(session: AsyncSession, row: AgentPresence, *, now: datetime) -> None:
    expected = row.version
    filled = row.active_count + 1 >= row.capacity
    if filled and row.capacity <= 1:
        new_state = "ringing"
    elif filled:
        new_state = "busy"
    else:
        new_state = row.state
    result = await session.execute(
        update(AgentPresence)
        .where(
            AgentPresence.id == row.id,
            AgentPresence.tenant_id == row.tenant_id,
            AgentPresence.state.in_(("available", "ringing", "busy")),
            AgentPresence.version == expected,
            AgentPresence.active_count + 1 <= AgentPresence.capacity,
        )
        .values(
            active_count=AgentPresence.active_count + 1,
            state=new_state,
            version=expected + 1,
            state_changed_at=now,
            last_assigned_at=now,
        )
    )
    if result.rowcount != 1:
        raise StaleState("Agent was no longer available")
    await session.refresh(row)


async def insert_assignment(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    entry_id: uuid.UUID,
    queue_id: uuid.UUID,
    user_id: uuid.UUID,
    capacity: int,
) -> RoutingAssignment:
    row = RoutingAssignment(
        tenant_id=tenant_id,
        entry_id=entry_id,
        queue_id=queue_id,
        user_id=user_id,
        status="active",
        entry_lock=str(entry_id),
        agent_lock=str(user_id) if capacity <= 1 else None,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        raise DuplicateAssignment("Active assignment already exists") from exc
    return row


async def active_assignment_for_entry(
    session: AsyncSession, tenant_id: uuid.UUID, entry_id: uuid.UUID
) -> RoutingAssignment | None:
    return (
        await session.execute(
            select(RoutingAssignment).where(
                RoutingAssignment.tenant_id == tenant_id,
                RoutingAssignment.entry_id == entry_id,
                RoutingAssignment.status == "active",
            )
        )
    ).scalar_one_or_none()


async def active_for_user(session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> list[RoutingAssignment]:
    return list(
        (
            await session.execute(
                select(RoutingAssignment).where(
                    RoutingAssignment.tenant_id == tenant_id,
                    RoutingAssignment.user_id == user_id,
                    RoutingAssignment.status == "active",
                )
            )
        ).scalars().all()
    )


async def get_assignment(session: AsyncSession, tenant_id: uuid.UUID, assignment_id: uuid.UUID) -> RoutingAssignment:
    row = await session.get(RoutingAssignment, assignment_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def get_entry(session: AsyncSession, tenant_id: uuid.UUID, entry_id: uuid.UUID) -> QueueEntry:
    row = await session.get(QueueEntry, entry_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def cursor_for(session: AsyncSession, tenant_id: uuid.UUID, queue_id: uuid.UUID) -> str:
    row = await session.get(RoutingCursor, queue_id)
    if row is None or row.tenant_id != tenant_id:
        return ""
    return row.last_user_id


async def save_cursor(session: AsyncSession, tenant_id: uuid.UUID, queue_id: uuid.UUID, user_id: str) -> None:
    row = await session.get(RoutingCursor, queue_id)
    if row is None:
        session.add(RoutingCursor(queue_id=queue_id, tenant_id=tenant_id, last_user_id=user_id))
    else:
        if row.tenant_id != tenant_id:
            raise NotFound()
        row.last_user_id = user_id
        row.updated_at = _now()
    await session.flush()


async def add_decision(session: AsyncSession, row: RoutingDecision) -> RoutingDecision:
    session.add(row)
    await session.flush()
    return row


async def get_decision(session: AsyncSession, tenant_id: uuid.UUID, decision_id: uuid.UUID) -> RoutingDecision:
    row = await session.get(RoutingDecision, decision_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def presence_for(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> AgentPresence | None:
    return (
        await session.execute(
            select(AgentPresence).where(
                AgentPresence.tenant_id == tenant_id,
                AgentPresence.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def active_entry_for_call(
    session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> QueueEntry | None:
    return (
        await session.execute(
            select(QueueEntry).where(
                QueueEntry.tenant_id == tenant_id,
                QueueEntry.call_id == call_id,
                QueueEntry.active_lock == str(call_id),
            )
        )
    ).scalar_one_or_none()


async def waiting_entries(
    session: AsyncSession, tenant_id: uuid.UUID, queue_id: uuid.UUID
) -> list[QueueEntry]:
    return list(
        (
            await session.execute(
                select(QueueEntry)
                .where(
                    QueueEntry.tenant_id == tenant_id,
                    QueueEntry.queue_id == queue_id,
                    QueueEntry.status == "waiting",
                )
                .order_by(QueueEntry.priority.desc(), QueueEntry.enqueued_at.asc(), QueueEntry.id.asc())
            )
        ).scalars().all()
    )
