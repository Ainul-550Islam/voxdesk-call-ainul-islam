"""Heartbeats and stale agents.

An old ``last_seen`` is not availability. A heartbeat refreshes a live state.
It does not move ``offline`` to ``available``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.agent_state import ensure, transition, wrap_up_expired
from app.contact_center.models import AgentPresence

STALE_SECONDS = 90
HEARTBEAT_SECONDS = 30
_LIVE = frozenset({"available", "ringing", "busy", "wrap_up", "away", "paused"})


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_fresh(row: AgentPresence, *, now: datetime | None = None) -> bool:
    if row.last_seen is None:
        return False
    moment = now or _now()
    return (moment - _as_utc(row.last_seen)).total_seconds() <= STALE_SECONDS


async def heartbeat(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> AgentPresence:
    row = await ensure(session, tenant_id, user_id)
    if row.state not in _LIVE:
        return row
    moment = now or _now()
    expected = row.version
    result = await session.execute(
        update(AgentPresence)
        .where(
            AgentPresence.id == row.id,
            AgentPresence.tenant_id == tenant_id,
            AgentPresence.version == expected,
        )
        .values(last_seen=moment, version=expected + 1)
    )
    if result.rowcount != 1:
        await session.refresh(row)
        return row
    await session.refresh(row)
    return row


async def sweep(session: AsyncSession, tenant_id: uuid.UUID, *, now: datetime | None = None) -> int:
    """Move stale available agents offline and expired wrap-up back to available."""
    moment = now or _now()
    cutoff = moment - timedelta(seconds=STALE_SECONDS)
    rows = (
        await session.execute(select(AgentPresence).where(AgentPresence.tenant_id == tenant_id))
    ).scalars().all()
    changed = 0
    for row in rows:
        if row.state == "available" and (row.last_seen is None or _as_utc(row.last_seen) < cutoff):
            await transition(session, row, "offline", now=moment)
            changed += 1
        elif wrap_up_expired(row, now=moment) and row.active_count == 0:
            await transition(session, row, "available", now=moment)
            changed += 1
    return changed
