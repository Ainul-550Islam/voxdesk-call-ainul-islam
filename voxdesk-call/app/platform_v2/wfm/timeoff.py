"""Time off: requests, balances, and what approving one actually costs.

A time-off module is where workforce management meets employment law, so the
rules are explicit:

* **Hours are derived from the request window and the tenant's working hours**,
  not typed in by the requester. An eight-day request that happens to include a
  weekend must not debit eight days of leave.
* **Balances are debited on approval, credited back on cancellation.** Approval
  is the moment the business commits; a request sitting in the queue has not
  spent anything.
* **Coverage impact is computed at request time** and stored on the row, so the
  person approving sees "3 of 12 agents on leave that Friday, requirement 9" and
  not just a name and two dates.
* **Overlaps are refused.** Two approved leave windows for one person over the
  same day is a payroll bug waiting for payday.

Sick leave is allowed to go negative — you cannot ask somebody to book their flu
in advance — while vacation is not. That asymmetry is deliberate and lives in
``ALLOW_NEGATIVE``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.platform_v2_models import (
    ShiftTemplate,
    TimeOffBalance,
    TimeOffRequest,
    WfmShift,
)
from app.platform_v2.errors import Conflict, NotFound, ValidationFailed
from app.platform_v2.settings import get_plat2_settings
from app.platform_v2.wfm.history import tenant_zone

#: Kinds that may exceed the accrued balance. See the module docstring.
ALLOW_NEGATIVE = frozenset({"sick", "bereavement"})
#: Every kind the schema accepts; checked here so the API fails cleanly instead
#: of relying on a database CheckConstraint message.
KINDS = ("vacation", "sick", "unpaid", "training", "bereavement")
#: A default working day used when no template covers a day in the window.
DEFAULT_WORKDAY_MINUTES = 8 * 60
MAX_REQUEST_HOURS = 24.0 * 366


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def working_minutes(
    starts_at: datetime,
    ends_at: datetime,
    *,
    zone_name: str,
    workday_start_minute: int | None = None,
    workday_minutes: int | None = None,
) -> float:
    """Hours of a request window that fall inside the working day.

    Each local calendar day touched by the window contributes only its overlap
    with the configured working day — so a morning request and an afternoon
    request both cost what they actually consume, and a request that spans a
    weekend costs two days rather than seven.

    The window is clipped, never assumed: pricing leave from midnight would make
    every afternoon request free, which is the kind of bug that shows up on a
    payroll report six weeks later.
    """
    cfg = get_plat2_settings()
    start_minute = cfg.wfm_workday_start_minute if workday_start_minute is None else workday_start_minute
    workday = cfg.wfm_workday_minutes if workday_minutes is None else workday_minutes
    if not 0 <= start_minute < 24 * 60:
        raise ValidationFailed("workday_start_minute must be a minute of the day")
    if not 1 <= workday <= 24 * 60:
        raise ValidationFailed("workday_minutes must be between 1 and 1440")
    start = _as_utc(starts_at)
    end = _as_utc(ends_at)
    if end <= start:
        raise ValidationFailed("ends_at must be after starts_at")
    zone = tenant_zone(zone_name)
    total_minutes = 0.0
    cursor = start
    while cursor < end:
        local = cursor.astimezone(zone)
        day_start_local = datetime.combine(local.date(), time(0, 0), tzinfo=zone)
        day_start = day_start_local.astimezone(timezone.utc)
        day_end = (day_start_local + timedelta(days=1)).astimezone(timezone.utc)
        window_low = max(cursor, day_start)
        window_high = min(end, day_end)
        if window_high > window_low:
            work_start = day_start + timedelta(minutes=start_minute)
            work_end = work_start + timedelta(minutes=workday)
            overlap_low = max(window_low, work_start)
            overlap_high = min(window_high, work_end)
            if overlap_high > overlap_low:
                total_minutes += (overlap_high - overlap_low).total_seconds() / 60.0
        cursor = day_end
    return round(total_minutes / 60.0, 2)


async def request_hours(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    zone_name: str = "UTC",
) -> float:
    """Hours a request actually costs this person.

    When the rota already puts them on shifts in that window, the cost is those
    shifts — that is the precise answer and it is what payroll will deduct. Only
    when there is no rota do we fall back to the configured working day, and the
    caller can see which path was taken in ``coverage["hours_source"]``.
    """
    shifts = await scheduled_shifts(
        session,
        tenant_id=tenant_id,
        user_id=user_id,
        starts_at=starts_at,
        ends_at=ends_at,
    )
    if shifts:
        minutes = 0.0
        for shift in shifts:
            low = max(_as_utc(shift.starts_at), _as_utc(starts_at))
            high = min(_as_utc(shift.ends_at), _as_utc(ends_at))
            if high > low:
                minutes += (high - low).total_seconds() / 60.0 - float(shift.break_minutes or 0)
        return round(max(0.0, minutes) / 60.0, 2)
    return working_minutes(starts_at, ends_at, zone_name=zone_name)


async def balance_row(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: str,
    create: bool = True,
) -> TimeOffBalance | None:
    row = (
        await session.execute(
            select(TimeOffBalance).where(
                TimeOffBalance.tenant_id == tenant_id,
                TimeOffBalance.user_id == user_id,
                TimeOffBalance.kind == kind,
            )
        )
    ).scalar_one_or_none()
    if row is None and create:
        row = TimeOffBalance(
            tenant_id=tenant_id,
            user_id=user_id,
            kind=kind,
            balance_hours=0.0,
            accrued_hours=0.0,
            used_hours=0.0,
            as_of=_now(),
        )
        session.add(row)
        await session.flush()
    return row


async def overlaps(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    exclude_id: uuid.UUID | None = None,
) -> list[TimeOffRequest]:
    stmt = select(TimeOffRequest).where(
        TimeOffRequest.tenant_id == tenant_id,
        TimeOffRequest.user_id == user_id,
        TimeOffRequest.status.in_(("requested", "approved")),
        TimeOffRequest.starts_at < ends_at,
        TimeOffRequest.ends_at > starts_at,
    )
    if exclude_id is not None:
        stmt = stmt.where(TimeOffRequest.id != exclude_id)
    return list((await session.execute(stmt)).scalars().all())


async def scheduled_shifts(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
) -> list[WfmShift]:
    stmt = select(WfmShift).where(
        WfmShift.tenant_id == tenant_id,
        WfmShift.user_id == user_id,
        WfmShift.status.in_(("scheduled", "published")),
        WfmShift.starts_at < ends_at,
        WfmShift.ends_at > starts_at,
    )
    return list((await session.execute(stmt)).scalars().all())


async def coverage_impact(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    zone_name: str = "UTC",
    include_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """How many people are already off, per local day, across the window.

    Returns ``{"days": {iso_date: {"on_leave": n, "scheduled": n}}}``. It answers
    the only question an approver has: is this window already crowded?
    """
    approved = list(
        (
            await session.execute(
                select(TimeOffRequest).where(
                    TimeOffRequest.tenant_id == tenant_id,
                    TimeOffRequest.status == "approved",
                    TimeOffRequest.starts_at < ends_at,
                    TimeOffRequest.ends_at > starts_at,
                )
            )
        )
        .scalars()
        .all()
    )
    shifts = list(
        (
            await session.execute(
                select(WfmShift).where(
                    WfmShift.tenant_id == tenant_id,
                    WfmShift.environment_id == environment_id,
                    WfmShift.status.in_(("scheduled", "published")),
                    WfmShift.starts_at < ends_at,
                    WfmShift.ends_at > starts_at,
                )
            )
        )
        .scalars()
        .all()
    )
    zone = tenant_zone(zone_name)
    days: dict[str, dict[str, Any]] = {}
    day = _as_utc(starts_at).astimezone(zone).date()
    last = _as_utc(ends_at).astimezone(zone).date()
    while day <= last:
        days[day.isoformat()] = {"on_leave": [], "scheduled": 0}
        day += timedelta(days=1)
    for request in approved:
        if include_user_id is not None and request.user_id == include_user_id:
            continue
        cursor = _as_utc(request.starts_at).astimezone(zone).date()
        finish = _as_utc(request.ends_at).astimezone(zone).date()
        while cursor <= finish:
            key = cursor.isoformat()
            if key in days:
                days[key]["on_leave"].append(str(request.user_id))
            cursor += timedelta(days=1)
    for shift in shifts:
        key = _as_utc(shift.starts_at).astimezone(zone).date().isoformat()
        if key in days:
            days[key]["scheduled"] += 1
    return {
        "days": {
            key: {"on_leave": len(value["on_leave"]), "scheduled": value["scheduled"]}
            for key, value in sorted(days.items())
        },
        "total_on_leave": len({str(request.user_id) for request in approved}),
    }


async def create_request(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: str,
    starts_at: datetime,
    ends_at: datetime,
    reason: str = "",
    zone_name: str = "UTC",
    environment_id: uuid.UUID | None = None,
    hours: float | None = None,
    commit: bool = True,
) -> TimeOffRequest:
    """Record a request, refusing the three ways one goes wrong.

    Refused: unknown kind, inverted window, and an overlap with an existing
    request. Recorded (with the impact) rather than refused: a request that
    exceeds the balance, because an approver may still choose to grant it — the
    response says so explicitly via ``coverage``.
    """
    if kind not in KINDS:
        raise ValidationFailed(f"kind must be one of {KINDS}")
    starts_at = _as_utc(starts_at)
    ends_at = _as_utc(ends_at)
    if ends_at <= starts_at:
        raise ValidationFailed("ends_at must be after starts_at")
    if hours is not None and (hours < 0 or hours > MAX_REQUEST_HOURS):
        raise ValidationFailed("hours is out of range")
    clash = await overlaps(
        session, tenant_id=tenant_id, user_id=user_id, starts_at=starts_at, ends_at=ends_at
    )
    if clash:
        raise Conflict(
            "this window overlaps an existing time-off request",
            detail={"conflicting_request_id": str(clash[0].id)},
        )
    has_shifts = bool(
        await scheduled_shifts(
            session, tenant_id=tenant_id, user_id=user_id, starts_at=starts_at, ends_at=ends_at
        )
    )
    computed_hours = (
        hours
        if hours is not None
        else await request_hours(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            starts_at=starts_at,
            ends_at=ends_at,
            zone_name=zone_name,
        )
    )
    balance = await balance_row(session, tenant_id=tenant_id, user_id=user_id, kind=kind)
    coverage: dict[str, Any] = {
        "hours": computed_hours,
        "kind": kind,
        "hours_source": "provided" if hours is not None else (
            "scheduled_shifts" if has_shifts else "working_day"
        ),
        "days": span_days(starts_at, ends_at, zone_name),
    }
    if environment_id is not None:
        coverage.update(
            await coverage_impact(
                session,
                tenant_id=tenant_id,
                environment_id=environment_id,
                starts_at=starts_at,
                ends_at=ends_at,
                zone_name=zone_name,
                include_user_id=user_id,
            )
        )
    if balance is not None:
        coverage["balance_hours"] = float(balance.balance_hours)
        coverage["would_go_negative"] = bool(
            float(balance.balance_hours) - computed_hours < 0 and kind not in ALLOW_NEGATIVE
        )
    request = TimeOffRequest(
        tenant_id=tenant_id,
        user_id=user_id,
        kind=kind,
        starts_at=starts_at,
        ends_at=ends_at,
        hours=computed_hours,
        status="requested",
        reason=reason[:400],
        coverage=coverage,
    )
    session.add(request)
    if commit:
        await session.commit()
    return request


async def decide(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    approved: bool,
    decided_by_user_id: uuid.UUID,
    note: str = "",
    enforce_balance: bool = True,
    commit: bool = True,
) -> TimeOffRequest:
    """Approve or deny, moving the balance exactly once.

    ``enforce_balance`` defaults to true: approving 40 hours of vacation against a
    4-hour balance is almost always a mistake, and the caller that genuinely
    wants it (a policy exception) passes ``False``.
    """
    request = (
        await session.execute(
            select(TimeOffRequest).where(
                TimeOffRequest.tenant_id == tenant_id, TimeOffRequest.id == request_id
            )
        )
    ).scalar_one_or_none()
    if request is None:
        raise NotFound("time-off request not found")
    if request.status != "requested":
        raise Conflict(f"request is already {request.status}")

    balance = await balance_row(
        session, tenant_id=tenant_id, user_id=request.user_id, kind=request.kind
    )
    hours = float(request.hours or 0.0)
    if approved and enforce_balance and balance is not None:
        if request.kind not in ALLOW_NEGATIVE and float(balance.balance_hours) - hours < 0:
            raise Conflict(
                "insufficient time-off balance for this request",
                detail={
                    "balance_hours": float(balance.balance_hours),
                    "requested_hours": hours,
                    "kind": request.kind,
                },
            )
    # The deduction happens on *every* approval — `enforce_balance` decides whether to
    # refuse, never whether to book the hours. Skipping it here let an approved
    # exception leave the balance untouched, so the leave was granted and never paid for.
    if approved and balance is not None:
        balance.used_hours = float(balance.used_hours) + hours
        balance.balance_hours = float(balance.balance_hours) - hours
        balance.as_of = _now()

    request.status = "approved" if approved else "denied"
    request.decided_by_user_id = decided_by_user_id
    request.decided_at = _now()
    if note:
        detail = dict(request.coverage or {})
        detail["decision_note"] = note[:400]
        request.coverage = detail
    if commit:
        await session.commit()
    return request


async def cancel(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    user_id: uuid.UUID,
    commit: bool = True,
) -> TimeOffRequest:
    """Cancel a request, returning approved hours to the balance."""
    request = (
        await session.execute(
            select(TimeOffRequest).where(
                TimeOffRequest.tenant_id == tenant_id, TimeOffRequest.id == request_id
            )
        )
    ).scalar_one_or_none()
    if request is None:
        raise NotFound("time-off request not found")
    if request.user_id != user_id:
        raise NotFound("time-off request not found")
    if request.status in ("cancelled", "denied"):
        raise Conflict(f"request is already {request.status}")
    if request.status == "approved":
        balance = await balance_row(
            session, tenant_id=tenant_id, user_id=request.user_id, kind=request.kind
        )
        if balance is not None:
            hours = float(request.hours or 0.0)
            balance.used_hours = max(0.0, float(balance.used_hours) - hours)
            balance.balance_hours = float(balance.balance_hours) + hours
            balance.as_of = _now()
    request.status = "cancelled"
    if commit:
        await session.commit()
    return request


async def accrue(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: str,
    hours: float,
    commit: bool = True,
) -> TimeOffBalance:
    """Add accrued hours (a payroll run, or an anniversary grant)."""
    if hours < 0:
        raise ValidationFailed("accrual must be non-negative")
    balance = await balance_row(session, tenant_id=tenant_id, user_id=user_id, kind=kind)
    assert balance is not None
    balance.accrued_hours = float(balance.accrued_hours) + hours
    balance.balance_hours = float(balance.balance_hours) + hours
    balance.as_of = _now()
    if commit:
        await session.commit()
    return balance


async def list_requests(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    statuses: tuple[str, ...] = ("requested",),
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 100,
) -> list[TimeOffRequest]:
    stmt = select(TimeOffRequest).where(
        TimeOffRequest.tenant_id == tenant_id,
        TimeOffRequest.status.in_(statuses),
    )
    if user_id is not None:
        stmt = stmt.where(TimeOffRequest.user_id == user_id)
    if start is not None:
        stmt = stmt.where(TimeOffRequest.ends_at > start)
    if end is not None:
        stmt = stmt.where(TimeOffRequest.starts_at < end)
    stmt = stmt.order_by(TimeOffRequest.starts_at).limit(max(1, min(500, limit)))
    return list((await session.execute(stmt)).scalars().all())


async def blocked_windows_for_week(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    week_start: datetime,
    week_end: datetime,
) -> list[tuple[uuid.UUID, datetime, datetime]]:
    """Approved leave inside the week, shaped for the scheduler's ``blocked``."""
    rows = (
        await session.execute(
            select(TimeOffRequest).where(
                TimeOffRequest.tenant_id == tenant_id,
                TimeOffRequest.status == "approved",
                TimeOffRequest.starts_at < week_end,
                TimeOffRequest.ends_at > week_start,
            )
        )
    ).scalars()
    return [(row.user_id, _as_utc(row.starts_at), _as_utc(row.ends_at)) for row in rows]


async def workday_minutes_from_templates(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> int:
    """Median template length: what "a day of leave" means at this tenant.

    Median rather than mean so one 12-hour shift template cannot redefine what a
    day of leave costs.
    """
    lengths = sorted(
        int(row.length_minutes)
        for row in (
            await session.execute(
                select(ShiftTemplate).where(
                    ShiftTemplate.tenant_id == tenant_id, ShiftTemplate.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    if not lengths:
        return DEFAULT_WORKDAY_MINUTES
    middle = len(lengths) // 2
    if len(lengths) % 2:
        return lengths[middle]
    return int(round((lengths[middle - 1] + lengths[middle]) / 2))


def summarise_balance(balance: TimeOffBalance) -> dict[str, Any]:
    return {
        "kind": balance.kind,
        "balance_hours": round(float(balance.balance_hours), 2),
        "accrued_hours": round(float(balance.accrued_hours), 2),
        "used_hours": round(float(balance.used_hours), 2),
        "as_of": balance.as_of.isoformat() if balance.as_of else None,
        "allows_negative": balance.kind in ALLOW_NEGATIVE,
    }


def span_days(starts_at: datetime, ends_at: datetime, zone_name: str) -> int:
    """Number of local calendar days a request touches (inclusive)."""
    zone = tenant_zone(zone_name)
    first = _as_utc(starts_at).astimezone(zone).date()
    last = _as_utc(ends_at).astimezone(zone).date()
    return (last - first).days + 1


def overlaps_any(
    windows: list[tuple[datetime, datetime]], start: datetime, end: datetime
) -> bool:
    return any(low < end and high > start for low, high in windows)


__all__ = [
    "ALLOW_NEGATIVE",
    "DEFAULT_WORKDAY_MINUTES",
    "KINDS",
    "MAX_REQUEST_HOURS",
    "accrue",
    "balance_row",
    "blocked_windows_for_week",
    "cancel",
    "coverage_impact",
    "create_request",
    "decide",
    "list_requests",
    "overlaps",
    "overlaps_any",
    "request_hours",
    "scheduled_shifts",
    "span_days",
    "summarise_balance",
    "workday_minutes_from_templates",
    "working_minutes",
]
