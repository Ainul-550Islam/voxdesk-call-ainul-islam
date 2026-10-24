"""Breach-clock reminders delivered through the existing notification adapters.

There is no second notification system here. Reminders go to the tenant's
owners through :func:`app.notifications.delivery.dispatch` on the ``in_app``
channel, and the outcome of every dispatch is recorded on the clock row and in
the audit log. If the tenant has no owner account, the reminder is recorded as
``undeliverable`` with that reason — an operator can act on that, whereas a
silent success would hide it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.db.models import User, UserRole

#: Channel used for deadline reminders. In-app is the one channel that cannot
#: fail because a third-party provider is down.
REMINDER_CHANNEL = "in_app"


async def _owner_ids(session: AsyncSession, tenant_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (
        await session.execute(
            select(User.id)
            .where(
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
                User.role.in_((UserRole.OWNER, UserRole.ADMIN)),
            )
            .order_by(User.created_at)
            .limit(20)
        )
    ).scalars()
    return list(rows)


async def send_reminder(
    session: AsyncSession,
    *,
    clock: Any,
    kind: str,
    message: str,
) -> dict[str, Any]:
    """Dispatch one reminder and record what actually happened."""
    from app.notifications.delivery import dispatch

    recipients = await _owner_ids(session, clock.tenant_id)
    outcomes: list[dict[str, str]] = []
    if not recipients:
        outcomes.append(
            {"recipient": "", "outcome": "undeliverable", "reason": "no active owner or admin"}
        )
    for user_id in recipients:
        result = await dispatch(REMINDER_CHANNEL, recipient=str(user_id), body=message)
        outcomes.append(
            {
                "recipient": str(user_id),
                "outcome": result.outcome,
                "category": result.category,
            }
        )

    proof = dict(clock.notification_proof or {})
    reminders = list(proof.get("reminders") or [])
    reminders.append(
        {
            "kind": kind,
            "sent_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "message": message[:500],
            "outcomes": outcomes,
        }
    )
    proof["reminders"] = reminders[-20:]
    clock.notification_proof = proof

    await record_event(
        session,
        tenant_id=clock.tenant_id,
        event_type="breach_clock_reminder",
        resource_type="breach_clock",
        resource_id=clock.id,
        detail={
            "kind": kind,
            "recipients": len(recipients),
            "delivered": sum(1 for item in outcomes if item["outcome"] == "accepted"),
        },
    )
    return {"kind": kind, "recipients": len(recipients), "outcomes": outcomes}


__all__ = ["REMINDER_CHANNEL", "send_reminder"]
