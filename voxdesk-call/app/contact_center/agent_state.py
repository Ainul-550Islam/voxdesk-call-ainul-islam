"""Agent presence state machine.

``available=true`` is not the model. Every change is a named transition.
Same-state is a duplicate, not a second change. A lost version is stale.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.exceptions import AgentUnavailable, StaleState
from app.contact_center.models import STATES, AgentPresence

TRANSITIONS: dict[str, frozenset[str]] = {
    "offline": frozenset({"available", "disabled"}),
    "available": frozenset({"ringing", "away", "paused", "offline", "disabled"}),
    "ringing": frozenset({"busy", "available", "offline"}),
    "busy": frozenset({"wrap_up", "offline"}),
    "wrap_up": frozenset({"available", "offline", "away"}),
    "away": frozenset({"available", "offline", "disabled"}),
    "paused": frozenset({"available", "offline", "disabled"}),
    "disabled": frozenset({"offline"}),
}

RELEASE_ON = frozenset({"offline", "disabled", "away", "paused"})
WRAP_UP_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def legal(current: str, target: str, *, supervisor: bool = False) -> bool:
    if current not in STATES or target not in STATES:
        return False
    if current == target:
        return True
    if target == "disabled":
        return supervisor
    if current == "disabled":
        return target == "offline"
    return target in TRANSITIONS.get(current, frozenset())


async def ensure(session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> AgentPresence:
    from sqlalchemy import select

    row = (
        await session.execute(
            select(AgentPresence).where(
                AgentPresence.tenant_id == tenant_id,
                AgentPresence.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    row = AgentPresence(tenant_id=tenant_id, user_id=user_id, state="offline")
    session.add(row)
    await session.flush()
    return row


async def transition(
    session: AsyncSession,
    row: AgentPresence,
    target: str,
    *,
    supervisor: bool = False,
    now: datetime | None = None,
) -> str:
    """Apply one transition. Returns ``applied``, ``duplicate`` or raises."""
    if not legal(row.state, target, supervisor=supervisor):
        raise AgentUnavailable(f"Illegal transition {row.state} -> {target}")
    if row.state == target:
        return "duplicate"
    moment = now or _now()
    expected = row.version
    result = await session.execute(
        update(AgentPresence)
        .where(
            AgentPresence.id == row.id,
            AgentPresence.tenant_id == row.tenant_id,
            AgentPresence.version == expected,
        )
        .values(
            state=target,
            version=expected + 1,
            state_changed_at=moment,
            last_seen=moment if target == "available" else row.last_seen,
        )
    )
    if result.rowcount != 1:
        raise StaleState("Agent state changed concurrently")
    await session.refresh(row)
    if target in RELEASE_ON:
        from app.contact_center.service import release_user_work

        await release_user_work(session, row.tenant_id, row.user_id, now=moment)
    return "applied"


def wrap_up_expired(row: AgentPresence, *, now: datetime | None = None) -> bool:
    if row.state != "wrap_up" or row.state_changed_at is None:
        return False
    moment = now or _now()
    changed = row.state_changed_at
    if changed.tzinfo is None:
        changed = changed.replace(tzinfo=timezone.utc)
    return (moment - changed).total_seconds() >= WRAP_UP_SECONDS
