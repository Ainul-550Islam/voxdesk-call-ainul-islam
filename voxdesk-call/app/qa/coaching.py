"""Coaching workflow. It does not discipline an employee."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.qa.exceptions import InvalidTransition, ReviewerNotAuthorized, StaleReview
from app.qa.models import CoachingSignal
from app.tenancy.isolation import NotFound

_TRANSITIONS = {
    "created": {"assigned", "cancelled"},
    "assigned": {"acknowledged", "cancelled"},
    "acknowledged": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


def legal(current: str, target: str) -> bool:
    return target in _TRANSITIONS.get(current, set())


async def create_signal(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    agent_user_id: uuid.UUID,
    category: str,
    recommendation: str,
    priority: str = "normal",
    review_id: uuid.UUID | None = None,
    evidence_id: uuid.UUID | None = None,
) -> CoachingSignal:
    agent = await session.get(User, agent_user_id)
    if agent is None or agent.tenant_id != tenant_id:
        raise NotFound()
    cleaned = (category or "").strip()
    if not cleaned:
        raise InvalidTransition("Coaching category is required")
    if priority not in {"low", "normal", "high"}:
        raise InvalidTransition("Unknown coaching priority")
    row = CoachingSignal(
        tenant_id=tenant_id,
        review_id=review_id,
        call_id=call_id,
        agent_user_id=agent.id,
        category=cleaned[:64],
        priority=priority,
        recommendation=(recommendation or "")[:500],
        evidence_id=evidence_id,
    )
    session.add(row)
    await session.flush()
    return row


async def transition(
    session: AsyncSession,
    signal: CoachingSignal,
    target: str,
    *,
    actor_id: uuid.UUID,
    expected_version: int | None = None,
    assignee_id: uuid.UUID | None = None,
    notes: str | None = None,
    supervisor: bool = False,
) -> tuple[CoachingSignal, str]:
    if signal.status == target:
        return signal, "duplicate"
    if not legal(signal.status, target):
        raise InvalidTransition(f"Cannot move coaching from {signal.status} to {target}")
    if target == "acknowledged" and actor_id != signal.agent_user_id and not supervisor:
        raise ReviewerNotAuthorized("Only the agent or a supervisor can acknowledge coaching")
    if target in {"assigned", "completed", "cancelled"} and not supervisor and actor_id != signal.agent_user_id:
        raise ReviewerNotAuthorized("Coaching change is not authorized")
    version = signal.version if expected_version is None else expected_version
    values: dict = {"status": target, "version": version + 1}
    if assignee_id is not None:
        values["assignee_id"] = assignee_id
    if notes is not None:
        values["notes"] = notes[:2000]
    now = datetime.now(timezone.utc)
    if target == "acknowledged":
        values["acknowledged_at"] = now
    if target == "completed":
        values["completed_at"] = now
    result = await session.execute(
        update(CoachingSignal)
        .where(
            CoachingSignal.id == signal.id,
            CoachingSignal.tenant_id == signal.tenant_id,
            CoachingSignal.version == version,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        raise StaleReview("Coaching version changed")
    await session.flush()
    await session.refresh(signal)
    return signal, "applied"
