"""Adherence: what the rota asked for versus what actually happened.

Contact-centre accountability has two independent measures and conflating them
is why arguments about attendance never end:

* **Adherence** — did the agent follow *their own* schedule? (Started on time,
  took the breaks they were given, stayed to the end.)
* **Conformance** — did the agent work the *required* time? A perfect adherence
  record and a rota that was wrong both show up here.

Both come from ``AgentStateInterval``, the same ledger the history rollup reads,
so the two reports can never disagree about the same minute. Anything that looks
like a breach becomes a ``WfmAdherenceException`` row with a kind, a duration and
a status — dismissible by a manager, never silently dropped, because "the system
said I was late" is a conversation with evidence on both sides.

Tolerances live in the caller: a 2-minute lateness on a softphone is not a
disciplinary event, and hard-coding a threshold here would make this module the
policy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.contact_center_v2_models import AgentStateInterval
from app.db.platform_v2_models import AdherenceException, WfmShift
from app.platform_v2.errors import Conflict, NotFound, ValidationFailed
from app.platform_v2.wfm.history import SHRINKAGE_STATES, WORK_STATES

#: States that count as "at work" for adherence.
ON_QUEUE_STATES = ("available", "ringing", "busy", "hold")
#: States that count as logged in but not productive.
OFF_QUEUE_STATES = ("wrap_up", "away", "paused")
EXCEPTION_KINDS = (
    "late_start",
    "early_leave",
    "unapproved_break",
    "over_break",
    "no_show",
    "off_queue",
)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass
class StateTotals:
    """Minutes per state inside one window — the raw material for every metric."""

    minutes: dict[str, float] = field(default_factory=dict)

    def add(self, state: str, minutes: float) -> None:
        if minutes > 0:
            self.minutes[state] = self.minutes.get(state, 0.0) + minutes

    @property
    def on_queue(self) -> float:
        return sum(self.minutes.get(state, 0.0) for state in ON_QUEUE_STATES)

    @property
    def off_queue(self) -> float:
        return sum(self.minutes.get(state, 0.0) for state in OFF_QUEUE_STATES)

    @property
    def logged_in(self) -> float:
        return self.on_queue + self.off_queue

    @property
    def worked(self) -> float:
        """Minutes in a *productive* state: on-queue plus wrap-up.

        A granted break is logged-in time but not worked time, so counting it here
        would let a shift that was half spent on break report full adherence.
        """
        return sum(
            self.minutes.get(state, 0.0) for state in set(WORK_STATES) | set(ON_QUEUE_STATES)
        )

    def state(self, name: str) -> float:
        return self.minutes.get(name, 0.0)

    def as_dict(self) -> dict[str, float]:
        return {name: round(value, 2) for name, value in sorted(self.minutes.items())}


@dataclass(frozen=True)
class ShiftAdherence:
    shift_id: uuid.UUID
    user_id: uuid.UUID
    scheduled_minutes: float
    worked_minutes: float
    on_queue_minutes: float
    break_minutes: float
    adherence: float
    conformance: float
    first_state_at: datetime | None
    last_state_at: datetime | None
    states: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "shift_id": str(self.shift_id),
            "user_id": str(self.user_id),
            "scheduled_minutes": round(self.scheduled_minutes, 2),
            "worked_minutes": round(self.worked_minutes, 2),
            "on_queue_minutes": round(self.on_queue_minutes, 2),
            "break_minutes": round(self.break_minutes, 2),
            "adherence": round(self.adherence, 4),
            "conformance": round(self.conformance, 4),
            "first_state_at": self.first_state_at.isoformat() if self.first_state_at else None,
            "last_state_at": self.last_state_at.isoformat() if self.last_state_at else None,
            "states": {name: round(value, 2) for name, value in self.states.items()},
        }


def _clip(interval: AgentStateInterval, low: datetime, high: datetime) -> float:
    opened = _as_utc(interval.started_at)
    closed = _as_utc(interval.ended_at) if interval.ended_at else high
    overlap = min(closed, high) - max(opened, low)
    return max(0.0, overlap.total_seconds() / 60.0)


def totals_for(
    intervals: list[AgentStateInterval], *, start: datetime, end: datetime
) -> StateTotals:
    totals = StateTotals()
    for interval in intervals:
        totals.add(interval.state, _clip(interval, start, end))
    return totals


def evaluate_shift(
    shift: WfmShift,
    intervals: list[AgentStateInterval],
    *,
    interval_minutes: int = 15,
) -> ShiftAdherence:
    """Compare one shift with the agent's state ledger inside that window.

    ``adherence`` is worked minutes over required minutes *capped at 1.0* — an
    agent who stayed late did not adhere better than one who was on time, they
    conformance-overachieved, and the two metrics keep that distinction.
    ``conformance`` is the same ratio uncapped, which is where over-staying shows.
    Required minutes are the shift length minus the break the rota granted, and a
    state that was still running at shift end is credited to its own end.
    """
    start = _as_utc(shift.starts_at)
    end = _as_utc(shift.ends_at)
    scheduled_minutes = max(0.0, (end - start).total_seconds() / 60.0)
    planned_break = float(shift.break_minutes or 0)
    # Scheduled working time excludes the break the rota granted.
    required_minutes = max(0.0, scheduled_minutes - planned_break)

    # A state that was still running when the shift ended is counted to its own end:
    # otherwise "worked minutes" is clipped at the shift boundary and an agent who
    # stayed two hours late is indistinguishable from one who left on time.
    overrun_end = end
    for item in intervals:
        opened = _as_utc(item.started_at)
        closed = _as_utc(item.ended_at) if item.ended_at else end
        if start <= opened < end and closed > overrun_end:
            overrun_end = closed
    totals = totals_for(intervals, start=start, end=overrun_end)
    worked = totals.worked
    on_queue = totals.on_queue
    break_taken = totals.state("away") + totals.state("paused")

    adherence = 1.0 if required_minutes == 0 else min(1.0, worked / required_minutes)
    conformance = 1.0 if required_minutes == 0 else worked / required_minutes

    relevant = [item for item in intervals if _clip(item, start, end) > 0]
    first_state_at = min((_as_utc(item.started_at) for item in relevant), default=None)
    last_state_at = max(
        (_as_utc(item.ended_at) if item.ended_at else end for item in relevant), default=None
    )
    return ShiftAdherence(
        shift_id=shift.id,
        user_id=shift.user_id,
        scheduled_minutes=scheduled_minutes,
        worked_minutes=worked,
        on_queue_minutes=on_queue,
        break_minutes=break_taken,
        adherence=adherence,
        conformance=conformance,
        first_state_at=first_state_at,
        last_state_at=last_state_at,
        states=totals.as_dict(),
    )


@dataclass(frozen=True)
class DetectedException:
    kind: str
    minutes: float
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "minutes": round(self.minutes, 2), "detail": self.detail}


def _logged_minutes(adherence: ShiftAdherence) -> float:
    """Logged-in minutes from the stored state breakdown.

    ``worked_minutes`` excludes breaks, so a no-show check that used it would call an
    agent who logged in and never left the break state a no-show.
    """
    return sum(
        adherence.states.get(state, 0.0) for state in set(ON_QUEUE_STATES) | set(OFF_QUEUE_STATES)
    )


def detect_exceptions(
    shift: WfmShift,
    adherence: ShiftAdherence,
    *,
    late_tolerance_minutes: float = 5.0,
    early_tolerance_minutes: float = 5.0,
    break_tolerance_minutes: float = 10.0,
    off_queue_tolerance_minutes: float = 20.0,
) -> list[DetectedException]:
    """Classify the deviations a supervisor would otherwise have to notice by eye.

    Tolerances are arguments, not constants: what counts as late depends on the
    channel, the country and the employment contract, and a hard-coded five
    minutes would eventually be wrong for somebody.
    """
    start = _as_utc(shift.starts_at)
    end = _as_utc(shift.ends_at)
    found: list[DetectedException] = []
    if _logged_minutes(adherence) <= 0:
        found.append(
            DetectedException("no_show", adherence.scheduled_minutes, "no state recorded")
        )
        return found
    if adherence.first_state_at is None:
        return found
    first = adherence.first_state_at
    if first > start + timedelta(minutes=late_tolerance_minutes):
        late = (first - start).total_seconds() / 60.0
        found.append(
            DetectedException("late_start", late, f"first state {round(late, 1)}m after start")
        )
    last = adherence.last_state_at or end
    if last < end - timedelta(minutes=early_tolerance_minutes):
        early = (end - last).total_seconds() / 60.0
        found.append(
            DetectedException("early_leave", early, f"last state {round(early, 1)}m before end")
        )
    planned = float(shift.break_minutes or 0)
    if adherence.break_minutes > planned + break_tolerance_minutes:
        over = adherence.break_minutes - planned
        found.append(
            DetectedException("over_break", over, f"{round(over, 1)}m more break than planned")
        )
    if planned <= 0 and adherence.break_minutes > off_queue_tolerance_minutes:
        found.append(
            DetectedException(
                "unapproved_break",
                adherence.break_minutes,
                "no break was scheduled for this shift",
            )
        )
    off_queue = adherence.break_minutes
    productive_window = adherence.worked_minutes
    if productive_window > 0 and off_queue / productive_window > 0.35:
        found.append(
            DetectedException(
                "off_queue",
                off_queue,
                f"{round(off_queue / productive_window * 100, 1)}% of logged time off queue",
            )
        )
    return found


async def shifts_between(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    start: datetime,
    end: datetime,
    user_id: uuid.UUID | None = None,
) -> list[WfmShift]:
    stmt = select(WfmShift).where(
        WfmShift.tenant_id == tenant_id,
        WfmShift.environment_id == environment_id,
        WfmShift.status.in_(("scheduled", "published", "swapped")),
        WfmShift.starts_at < end,
        WfmShift.ends_at > start,
    )
    if user_id is not None:
        stmt = stmt.where(WfmShift.user_id == user_id)
    stmt = stmt.order_by(WfmShift.starts_at)
    return list((await session.execute(stmt)).scalars().all())


async def states_between(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[AgentStateInterval]:
    rows = (
        await session.execute(
            select(AgentStateInterval).where(
                AgentStateInterval.tenant_id == tenant_id,
                AgentStateInterval.environment_id == environment_id,
                AgentStateInterval.started_at < end,
                (AgentStateInterval.ended_at.is_(None)) | (AgentStateInterval.ended_at > start),
            )
        )
    ).scalars()
    return list(rows)


@dataclass
class AdherenceReport:
    start: datetime
    end: datetime
    shifts: list[ShiftAdherence]
    exceptions: list[DetectedException]
    unplanned_minutes: float = 0.0

    @property
    def mean_adherence(self) -> float:
        scored = [item.adherence for item in self.shifts if item.scheduled_minutes > 0]
        return sum(scored) / len(scored) if scored else 0.0

    @property
    def mean_conformance(self) -> float:
        scored = [item.conformance for item in self.shifts if item.scheduled_minutes > 0]
        return sum(scored) / len(scored) if scored else 0.0

    def by_user(self) -> dict[str, dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = {}
        for item in self.shifts:
            bucket = buckets.setdefault(
                str(item.user_id),
                {"shifts": 0, "scheduled_minutes": 0.0, "worked_minutes": 0.0},
            )
            bucket["shifts"] += 1
            bucket["scheduled_minutes"] += item.scheduled_minutes
            bucket["worked_minutes"] += item.worked_minutes
        for bucket in buckets.values():
            scheduled = bucket["scheduled_minutes"]
            bucket["adherence"] = (
                round(min(1.0, bucket["worked_minutes"] / scheduled), 4) if scheduled else 0.0
            )
            bucket["scheduled_minutes"] = round(bucket["scheduled_minutes"], 2)
            bucket["worked_minutes"] = round(bucket["worked_minutes"], 2)
        return buckets

    def as_dict(self, *, include_shifts: bool = True) -> dict[str, Any]:
        body: dict[str, Any] = {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "shifts": len(self.shifts),
            "mean_adherence": round(self.mean_adherence, 4),
            "mean_conformance": round(self.mean_conformance, 4),
            "unplanned_minutes": round(self.unplanned_minutes, 2),
            "exceptions": [item.as_dict() for item in self.exceptions],
            "by_user": self.by_user(),
        }
        if include_shifts:
            body["detail"] = [item.as_dict() for item in self.shifts]
        return body


def build_report(
    shifts: list[WfmShift],
    states: list[AgentStateInterval],
    *,
    start: datetime,
    end: datetime,
    tolerances: dict[str, float] | None = None,
) -> AdherenceReport:
    """Evaluate a window of rota against a window of states."""
    by_user: dict[uuid.UUID, list[AgentStateInterval]] = {}
    for interval in states:
        by_user.setdefault(interval.user_id, []).append(interval)
    options = tolerances or {}
    results: list[ShiftAdherence] = []
    exceptions: list[DetectedException] = []
    for shift in shifts:
        adherence = evaluate_shift(shift, by_user.get(shift.user_id, []))
        results.append(adherence)
        exceptions.extend(detect_exceptions(shift, adherence, **options))

    # Time worked inside the window with no shift covering it: the rota's blind
    # spot, and the reason conformance and adherence are reported separately.
    covered = [(max(_as_utc(shift.starts_at), start), min(_as_utc(shift.ends_at), end)) for shift in shifts]
    unplanned = 0.0
    totals = totals_for(states, start=start, end=end)
    planned_minutes = sum(max(0.0, (high - low).total_seconds() / 60.0) for low, high in covered)
    unplanned = max(0.0, totals.logged_in - planned_minutes)
    return AdherenceReport(
        start=start, end=end, shifts=results, exceptions=exceptions, unplanned_minutes=unplanned
    )


async def persist_exceptions(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    shift: WfmShift,
    detected: list[DetectedException],
    day: datetime,
    commit: bool = True,
) -> list[AdherenceException]:
    """Store detected exceptions, skipping ones already recorded for the shift.

    Idempotent per (shift, kind): the adherence sweep runs hourly and must not
    create a fresh accusation every time it sees the same breach.
    """
    if not detected:
        return []
    existing = {
        (row.kind)
        for row in (
            await session.execute(
                select(AdherenceException).where(
                    AdherenceException.tenant_id == tenant_id,
                    AdherenceException.shift_id == shift.id,
                )
            )
        )
        .scalars()
        .all()
    }
    created: list[AdherenceException] = []
    for item in detected:
        if item.kind in existing:
            continue
        if item.kind not in EXCEPTION_KINDS:  # pragma: no cover - guarded by callers
            raise ValidationFailed(f"unknown exception kind {item.kind!r}")
        row = AdherenceException(
            tenant_id=tenant_id,
            environment_id=environment_id,
            user_id=shift.user_id,
            shift_id=shift.id,
            day=_as_utc(day).date(),
            kind=item.kind,
            # The column is an integer count of minutes; rounding at the boundary keeps
            # SQLite and PostgreSQL storing the same value instead of one rounding for us.
            minutes=int(round(item.minutes)),
            detail={"text": item.detail},
            status="open",
        )
        session.add(row)
        created.append(row)
    if commit:
        await session.commit()
    return created


async def list_exceptions(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    statuses: tuple[str, ...] = ("open",),
    user_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[AdherenceException]:
    stmt = select(AdherenceException).where(
        AdherenceException.tenant_id == tenant_id,
        AdherenceException.status.in_(statuses),
    )
    if user_id is not None:
        stmt = stmt.where(AdherenceException.user_id == user_id)
    stmt = stmt.order_by(AdherenceException.day.desc()).limit(max(1, min(500, limit)))
    return list((await session.execute(stmt)).scalars().all())


async def decide_exception(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    exception_id: uuid.UUID,
    approved: bool,
    decided_by_user_id: uuid.UUID,
    note: str = "",
    commit: bool = True,
) -> AdherenceException:
    """Approve (excused) or deny a detected exception."""
    row = (
        await session.execute(
            select(AdherenceException).where(
                AdherenceException.tenant_id == tenant_id,
                AdherenceException.id == exception_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("adherence exception not found")
    if row.status != "open":
        raise Conflict(f"exception is already {row.status}")
    row.status = "approved" if approved else "denied"
    row.decided_by_user_id = decided_by_user_id
    row.decided_at = datetime.now(timezone.utc)
    detail = dict(row.detail or {})
    if note:
        detail["decision_note"] = note[:400]
    row.detail = detail
    if commit:
        await session.commit()
    return row


def shrinkage_states() -> tuple[str, ...]:
    """States counted as paid-but-not-productive, for the shrinkage arithmetic."""
    return tuple(sorted(set(SHRINKAGE_STATES) | set(OFF_QUEUE_STATES)))


def working_states() -> tuple[str, ...]:
    return tuple(sorted(set(WORK_STATES) | set(ON_QUEUE_STATES)))


__all__ = [
    "AdherenceReport",
    "DetectedException",
    "EXCEPTION_KINDS",
    "OFF_QUEUE_STATES",
    "ON_QUEUE_STATES",
    "ShiftAdherence",
    "StateTotals",
    "build_report",
    "decide_exception",
    "detect_exceptions",
    "evaluate_shift",
    "list_exceptions",
    "persist_exceptions",
    "shifts_between",
    "shrinkage_states",
    "states_between",
    "totals_for",
    "working_states",
]
