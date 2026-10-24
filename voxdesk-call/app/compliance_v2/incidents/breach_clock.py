"""Statutory notification deadlines, per incident, tenant and jurisdiction.

The clock starts when the incident is *detected* — not when it is triaged, and
not when someone gets around to opening a ticket.

Deadlines implemented here:

* ``eu_gdpr`` — 72 hours from awareness to notify the supervisory authority
  (GDPR Art. 33(1));
* ``us_hipaa`` — 60 days from discovery for individual notification
  (45 CFR 164.404(b));
* ``us_state`` — the shortest common state breach-notification window, 30 days,
  used unless a tenant's jurisdiction says otherwise;
* ``uk_gdpr`` — 72 hours (UK GDPR Art. 33).

The clock is a *deadline calculation plus a reminder/escalation loop*, not a
notification service. ``notify`` records the proof an operator supplies (who was
notified, when, by what channel, the receipt reference) — VoxDesk does not claim
to have filed anything on a tenant's behalf.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.db.compliance_v2_models import BreachClock

JURISDICTION_GDPR = "eu_gdpr"
JURISDICTION_UK_GDPR = "uk_gdpr"
JURISDICTION_HIPAA = "us_hipaa"
JURISDICTION_US_STATE = "us_state"

#: Jurisdiction -> (obligation text, deadline duration).
DEADLINES: dict[str, tuple[str, dt.timedelta]] = {
    JURISDICTION_GDPR: ("notify supervisory authority (GDPR Art. 33)", dt.timedelta(hours=72)),
    JURISDICTION_UK_GDPR: ("notify ICO (UK GDPR Art. 33)", dt.timedelta(hours=72)),
    JURISDICTION_HIPAA: ("notify affected individuals (45 CFR 164.404)", dt.timedelta(days=60)),
    JURISDICTION_US_STATE: ("notify affected residents (state law)", dt.timedelta(days=30)),
}

STATUS_OPEN = "open"
STATUS_NOTIFIED = "notified"
STATUS_OVERDUE = "overdue"
STATUS_CLOSED = "closed"

#: Reminders before the deadline, as fractions of the total window.
REMINDER_FRACTIONS = (0.5, 0.75, 0.9)


class BreachClockError(RuntimeError):
    code = "breach_clock_error"


def _utcnow(moment: dt.datetime | None = None) -> dt.datetime:
    value = moment or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def deadline_for(jurisdiction: str, detected_at: dt.datetime) -> dt.datetime:
    """The notification deadline for one jurisdiction."""
    entry = DEADLINES.get((jurisdiction or "").strip().lower())
    if entry is None:
        raise BreachClockError(f"unknown jurisdiction {jurisdiction!r}")
    return _utcnow(detected_at) + entry[1]


def obligation_for(jurisdiction: str) -> str:
    entry = DEADLINES.get((jurisdiction or "").strip().lower())
    if entry is None:
        raise BreachClockError(f"unknown jurisdiction {jurisdiction!r}")
    return entry[0]


async def start_clock(
    session: AsyncSession,
    *,
    incident_id: uuid.UUID,
    tenant_id: uuid.UUID,
    jurisdiction: str,
    detected_at: dt.datetime,
) -> BreachClock:
    """Open (or return) the clock for one incident/tenant/jurisdiction."""
    key = (jurisdiction or "").strip().lower()
    existing = await session.scalar(
        select(BreachClock).where(
            BreachClock.incident_id == incident_id,
            BreachClock.tenant_id == tenant_id,
            BreachClock.jurisdiction == key,
        )
    )
    if existing is not None:
        return existing
    deadline = deadline_for(key, detected_at)
    row = BreachClock(
        tenant_id=tenant_id,
        incident_id=incident_id,
        jurisdiction=key,
        obligation=obligation_for(key),
        started_at=_utcnow(detected_at),
        deadline_at=deadline,
        status=STATUS_OPEN,
    )
    session.add(row)
    await session.flush()
    await record_event(
        session,
        tenant_id=tenant_id,
        event_type="breach_clock_started",
        resource_type="incident",
        resource_id=incident_id,
        detail={"jurisdiction": key, "deadline_at": deadline.isoformat()},
    )
    return row


async def start_clocks_for_incident(
    session: AsyncSession,
    *,
    incident_id: uuid.UUID,
    tenant_id: uuid.UUID,
    jurisdictions: list[str],
    detected_at: dt.datetime,
) -> list[BreachClock]:
    return [
        await start_clock(
            session,
            incident_id=incident_id,
            tenant_id=tenant_id,
            jurisdiction=jurisdiction,
            detected_at=detected_at,
        )
        for jurisdiction in jurisdictions
    ]


async def record_notification(
    session: AsyncSession,
    *,
    clock_id: uuid.UUID,
    tenant_id: uuid.UUID,
    notified_at: dt.datetime,
    channel: str,
    recipient: str,
    proof_reference: str,
    actor_user_id: uuid.UUID | None,
) -> BreachClock:
    """Record notification proof supplied by an operator."""
    row = await session.scalar(
        select(BreachClock).where(BreachClock.id == clock_id, BreachClock.tenant_id == tenant_id)
    )
    if row is None:
        raise BreachClockError("breach clock not found")
    if row.status == STATUS_CLOSED:
        raise BreachClockError("this clock is already closed")
    if not (proof_reference or "").strip():
        raise BreachClockError("a notification needs a receipt or reference")
    moment = _utcnow(notified_at)
    row.status = STATUS_NOTIFIED
    row.notified_at = moment
    row.notification_proof = {
        "channel": (channel or "").strip()[:32],
        "recipient": (recipient or "").strip()[:200],
        "reference": (proof_reference or "").strip()[:200],
        "recorded_by": str(actor_user_id) if actor_user_id else "",
        "recorded_at": _utcnow().isoformat(),
        "within_deadline": moment <= _aware(row.deadline_at),
    }
    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        event_type="breach_notification_recorded",
        resource_type="breach_clock",
        resource_id=row.id,
        detail={
            "jurisdiction": row.jurisdiction,
            "within_deadline": row.notification_proof["within_deadline"],
        },
    )
    await session.flush()
    return row


def status_for(row: BreachClock, *, now: dt.datetime | None = None) -> str:
    """Effective status: an untreated open clock past its deadline is overdue."""
    if row.status in (STATUS_NOTIFIED, STATUS_CLOSED):
        return row.status
    deadline = _aware(row.deadline_at)
    if deadline is not None and deadline < _utcnow(now):
        return STATUS_OVERDUE
    return STATUS_OPEN


async def tick(
    session: AsyncSession, *, now: dt.datetime | None = None, limit: int = 200
) -> dict:
    """Send due reminders and escalate overdue clocks. Idempotent per window."""
    from app.compliance_v2.incidents import reminders

    moment = _utcnow(now)
    rows = list(
        (
            await session.execute(
                select(BreachClock)
                .where(BreachClock.status.in_((STATUS_OPEN, STATUS_OVERDUE)))
                .order_by(BreachClock.deadline_at)
                .limit(max(1, limit))
            )
        ).scalars()
    )
    reminded = 0
    escalated = 0
    for row in rows:
        deadline = _aware(row.deadline_at) or moment
        started = _aware(row.started_at) or moment
        total = max(1.0, (deadline - started).total_seconds())
        remaining = (deadline - moment).total_seconds()
        elapsed_fraction = 1 - (remaining / total)

        due_fraction = None
        for fraction in REMINDER_FRACTIONS:
            if elapsed_fraction >= fraction:
                due_fraction = fraction
        if due_fraction is not None and int(row.reminder_count or 0) < _index_of(due_fraction):
            await reminders.send_reminder(
                session,
                clock=row,
                kind="deadline_approaching",
                message=(
                    f"Notification deadline for {row.jurisdiction} is "
                    f"{max(0, int(remaining // 3600))}h away"
                ),
            )
            row.reminder_count = _index_of(due_fraction)
            row.last_reminder_at = moment
            reminded += 1

        if remaining < 0:
            row.status = STATUS_OVERDUE
            if row.escalated_at is None:
                await reminders.send_reminder(
                    session,
                    clock=row,
                    kind="deadline_overdue",
                    message=(
                        f"Notification deadline for {row.jurisdiction} passed "
                        f"{-int(remaining // 3600)}h ago without a recorded notification"
                    ),
                )
                row.escalated_at = moment
                await record_event(
                    session,
                    tenant_id=row.tenant_id,
                    event_type="breach_clock_overdue",
                    resource_type="breach_clock",
                    resource_id=row.id,
                    detail={"jurisdiction": row.jurisdiction},
                )
                escalated += 1
    await session.flush()
    return {"checked": len(rows), "reminded": reminded, "escalated": escalated}


def _index_of(fraction: float) -> int:
    """1-based index of a reminder fraction (so 'sent 2 of 3' is comparable)."""
    return REMINDER_FRACTIONS.index(fraction) + 1


def as_dict(row: BreachClock, *, now: dt.datetime | None = None) -> dict:
    deadline = _aware(row.deadline_at)
    moment = _utcnow(now)
    remaining_hours = ((deadline - moment).total_seconds() / 3600) if deadline else None
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "incident_id": str(row.incident_id),
        "jurisdiction": row.jurisdiction,
        "obligation": row.obligation,
        "status": status_for(row, now=moment),
        "started_at": _aware(row.started_at).isoformat() if row.started_at else "",
        "deadline_at": deadline.isoformat() if deadline else "",
        "remaining_hours": round(remaining_hours, 2) if remaining_hours is not None else None,
        "reminder_count": row.reminder_count,
        "notified_at": _aware(row.notified_at).isoformat() if row.notified_at else "",
        "escalated_at": _aware(row.escalated_at).isoformat() if row.escalated_at else "",
        "notification_proof": dict(row.notification_proof or {}),
    }


__all__ = [
    "DEADLINES",
    "JURISDICTION_GDPR",
    "JURISDICTION_HIPAA",
    "JURISDICTION_UK_GDPR",
    "JURISDICTION_US_STATE",
    "REMINDER_FRACTIONS",
    "STATUS_CLOSED",
    "STATUS_NOTIFIED",
    "STATUS_OPEN",
    "STATUS_OVERDUE",
    "BreachClockError",
    "as_dict",
    "deadline_for",
    "obligation_for",
    "record_notification",
    "start_clock",
    "start_clocks_for_incident",
    "status_for",
    "tick",
]
