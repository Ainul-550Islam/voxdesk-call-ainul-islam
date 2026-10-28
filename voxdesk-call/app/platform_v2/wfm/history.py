"""Interval history: turning raw queue traffic into the grid every model reads.

Everything downstream is only as honest as this file. Forecasts extrapolate it,
Erlang turns it into staffing, adherence compares rotas against it — so the
rules here are deliberately conservative:

* **Buckets are wall-clock intervals in the tenant's zone**, built with
  ``zoneinfo`` and DST-aware. A spring-forward day has 92 quarter-hours instead
  of 96 and a fall-back day has 100; a naive ``timedelta`` loop would smear
  every later interval by an hour twice a year and quietly corrupt the seasonal
  profile.
* **Missing data is marked, never zero-filled.** ``is_gap`` says "nobody was
  scheduled here", which is a fact about coverage; writing 0 offered would say
  "nobody called", which is a different and unverifiable claim. The forecaster
  skips gaps instead of learning from them.
* **Rebuilds are idempotent.** Rows are keyed by queue + interval start, so
  re-running a rollup after a late call record corrects history instead of
  duplicating it.

Volume comes from ``cc_queue_entries`` (offered/answered/abandoned and the wait
between enqueue and assignment) joined to ``calls`` for handle time. Agent
presence comes from ``AgentStateInterval``, which is the only place the platform
records what an agent was actually doing.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.contact_center_v2_models import AgentStateInterval
from app.db.models import Call
from app.db.platform_v2_models import WfmIntervalHistory
from app.platform_v2.errors import ValidationFailed
from app.contact_center.models import Queue, QueueEntry

#: States that count as "on the phone" for occupancy and handle time.
WORK_STATES = ("ringing", "busy", "hold")
#: States that count as available to take work.
AVAILABLE_STATES = ("available",)
#: States that are paid time but not productive time — this is shrinkage.
SHRINKAGE_STATES = ("wrap_up", "away", "paused")

VALID_INTERVAL_MINUTES = (15, 30)


def tenant_zone(name: str) -> ZoneInfo:
    """Resolve an IANA zone or fail loudly.

    A silent UTC fallback here would shift a whole rota for a tenant in Dhaka and
    nobody would notice until the staff did.
    """
    try:
        return ZoneInfo((name or "UTC").strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:  # pragma: no cover - config guard
        raise ValidationFailed(f"unknown timezone: {name!r}") from exc


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class IntervalSlot:
    """One bucket of the grid: an absolute half-open window plus its local label."""

    start: datetime
    end: datetime
    local_label: str

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


def interval_grid(
    zone_name: str,
    start_date: date,
    end_date: date,
    interval_minutes: int = 15,
) -> list[IntervalSlot]:
    """Every interval between two local dates, inclusive of ``start_date``.

    DST is handled the only way that survives audit: walk the *wall clock* and
    convert each label to an absolute instant. Non-existent labels (spring
    forward) are dropped; repeated labels (fall back) appear twice, once per
    ``fold``, because the platform really did take calls in both hours.
    """
    if interval_minutes not in VALID_INTERVAL_MINUTES:
        raise ValidationFailed(
            f"interval_minutes must be one of {VALID_INTERVAL_MINUTES}",
            detail={"interval_minutes": interval_minutes},
        )
    if end_date < start_date:
        raise ValidationFailed("end_date must not precede start_date")
    zone = tenant_zone(zone_name)
    slots: list[IntervalSlot] = []
    seen: set[datetime] = set()
    day = start_date
    while day <= end_date:
        midnight = datetime.combine(day, dtime(0, 0))
        for offset in range(0, 24 * 60, interval_minutes):
            naive = midnight + timedelta(minutes=offset)
            for fold in (0, 1):
                local = naive.replace(tzinfo=zone, fold=fold)
                utc = local.astimezone(timezone.utc)
                # Round-trip: if the wall clock we asked for is not the wall
                # clock we got back, this local time does not exist that day.
                if utc.astimezone(zone).replace(tzinfo=None) != naive:
                    continue
                if fold == 1 and utc.astimezone(zone).fold != 1:
                    continue
                if utc in seen:
                    continue
                seen.add(utc)
                slots.append(
                    IntervalSlot(
                        start=utc,
                        end=utc + timedelta(minutes=interval_minutes),
                        local_label=naive.strftime("%Y-%m-%d %H:%M"),
                    )
                )
        day += timedelta(days=1)
    slots.sort(key=lambda slot: slot.start)
    return slots


def slot_for(moment: datetime, slots: list[IntervalSlot]) -> IntervalSlot | None:
    """Binary-search the slot containing ``moment`` (None when outside the grid)."""
    target = _as_utc(moment)
    low, high = 0, len(slots) - 1
    while low <= high:
        mid = (low + high) // 2
        slot = slots[mid]
        if target < slot.start:
            high = mid - 1
        elif target >= slot.end:
            low = mid + 1
        else:
            return slot
    return None


@dataclass
class IntervalSample:
    """Mutable accumulator for one bucket while the window is being scanned."""

    offered: int = 0
    handled: int = 0
    abandoned: int = 0
    handle_seconds: float = 0.0
    speed_answer_seconds: float = 0.0
    answered_count: int = 0
    agent_seconds: dict[str, float] = field(default_factory=dict)

    def as_row(self) -> dict[str, float | int]:
        agents_available = int(self.agent_seconds.get("available", 0.0) // 60)
        return {
            "offered": self.offered,
            "handled": self.handled,
            "abandoned": self.abandoned,
            "total_handle_seconds": round(self.handle_seconds, 2),
            "total_speed_answer_seconds": round(self.speed_answer_seconds, 2),
            "agents_available": agents_available,
        }


@dataclass
class RollupResult:
    queue_key: str
    intervals: int
    written: int
    updated: int
    gaps: int
    start: datetime
    end: datetime

    def as_dict(self) -> dict[str, object]:
        return {
            "queue_key": self.queue_key,
            "intervals": self.intervals,
            "written": self.written,
            "updated": self.updated,
            "gaps": self.gaps,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }


def _queue_key(queue_id: uuid.UUID | None) -> str:
    return "" if queue_id is None else str(queue_id)


async def _queue_entries(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    queue_id: uuid.UUID | None,
    start: datetime,
    end: datetime,
) -> list[tuple[QueueEntry, float | None, datetime | None]]:
    """Queue entries that were offered inside the window, with call handle time."""
    stmt = (
        select(QueueEntry, Call.duration_seconds, Call.ended_at)
        .join(Call, Call.id == QueueEntry.call_id)
        .where(
            QueueEntry.tenant_id == tenant_id,
            QueueEntry.environment_id == environment_id,
            QueueEntry.enqueued_at >= start,
            QueueEntry.enqueued_at < end,
        )
    )
    if queue_id is not None:
        stmt = stmt.where(QueueEntry.queue_id == queue_id)
    rows = await session.execute(stmt)
    return [(entry, duration, ended) for entry, duration, ended in rows.all()]


async def _agent_intervals(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[AgentStateInterval]:
    stmt = select(AgentStateInterval).where(
        AgentStateInterval.tenant_id == tenant_id,
        AgentStateInterval.environment_id == environment_id,
        AgentStateInterval.started_at < end,
        (AgentStateInterval.ended_at.is_(None)) | (AgentStateInterval.ended_at > start),
    )
    return list((await session.execute(stmt)).scalars().all())


def _overlap_seconds(
    interval: AgentStateInterval, window_start: datetime, window_end: datetime
) -> float:
    """Seconds of an agent-state row that fall inside one bucket, clipped."""
    opened = _as_utc(interval.started_at)
    closed = _as_utc(interval.ended_at) if interval.ended_at else window_end
    low = max(opened, window_start)
    high = min(closed, window_end)
    return max(0.0, (high - low).total_seconds())


async def rollup_queue_history(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    queue_id: uuid.UUID | None,
    start_date: date,
    end_date: date,
    zone_name: str = "UTC",
    interval_minutes: int = 15,
    source: str = "reconstructed",
    commit: bool = True,
) -> RollupResult:
    """Rebuild ``wfm_interval_history`` for one queue over a date range.

    Idempotent: an existing row for the same queue/interval is updated in place,
    so a late CDR corrects history rather than adding a second opinion.
    """
    slots = interval_grid(zone_name, start_date, end_date, interval_minutes)
    if not slots:
        raise ValidationFailed("no intervals in the requested range")
    window_start, window_end = slots[0].start, slots[-1].end
    key = _queue_key(queue_id)

    samples: dict[datetime, IntervalSample] = {slot.start: IntervalSample() for slot in slots}
    for entry, duration, ended in await _queue_entries(
        session,
        tenant_id=tenant_id,
        environment_id=environment_id,
        queue_id=queue_id,
        start=window_start,
        end=window_end,
    ):
        slot = slot_for(_as_utc(entry.enqueued_at), slots)
        if slot is None:
            continue
        sample = samples[slot.start]
        sample.offered += 1
        if entry.status == "abandoned":
            sample.abandoned += 1
        elif entry.status == "completed":
            sample.handled += 1
        if entry.assigned_at is not None:
            waited = (_as_utc(entry.assigned_at) - _as_utc(entry.enqueued_at)).total_seconds()
            if waited >= 0:
                sample.speed_answer_seconds += waited
                sample.answered_count += 1
        # Handle time is credited to the interval the call *ended* in, which is
        # how every ACD reports it and keeps a call that spans a boundary from
        # being counted twice.
        if duration and ended is not None:
            end_slot = slot_for(_as_utc(ended), slots)
            if end_slot is not None:
                samples[end_slot.start].handle_seconds += float(duration)

    for agent_interval in await _agent_intervals(
        session,
        tenant_id=tenant_id,
        environment_id=environment_id,
        start=window_start,
        end=window_end,
    ):
        for slot in slots:
            seconds = _overlap_seconds(agent_interval, slot.start, slot.end)
            if seconds <= 0:
                continue
            bucket = samples[slot.start].agent_seconds
            bucket[agent_interval.state] = bucket.get(agent_interval.state, 0.0) + seconds

    # The window is widened by a day and the exact range filtered in Python.
    # Two reasons: a bucket read back from a naive-datetime backend must still match
    # the aware ``slot.start`` it corresponds to (otherwise the second pass inserts a
    # duplicate and the unique constraint fails), and comparing timestamps in Python
    # keeps the same code path on SQLite, MySQL and PostgreSQL instead of depending on
    # each dialect's tz handling.
    nearby = (
        (
            await session.execute(
                select(WfmIntervalHistory).where(
                    WfmIntervalHistory.tenant_id == tenant_id,
                    WfmIntervalHistory.environment_id == environment_id,
                    WfmIntervalHistory.queue_key == key,
                    WfmIntervalHistory.interval_start >= window_start - timedelta(days=2),
                    WfmIntervalHistory.interval_start < window_end + timedelta(days=2),
                )
            )
        )
        .scalars()
        .all()
    )
    existing = {
        _as_utc(row.interval_start): row
        for row in nearby
        if window_start <= _as_utc(row.interval_start) < window_end
    }

    written = updated = gaps = 0
    now = datetime.now(timezone.utc)
    for slot in slots:
        sample = samples[slot.start]
        values = sample.as_row()
        has_traffic = values["offered"] > 0
        # A gap is a bucket with no traffic *and* nobody present: the window is
        # outside coverage, so the forecaster must not learn "zero demand" from it.
        is_gap = not has_traffic and not any(sample.agent_seconds.values())
        gaps += 1 if is_gap else 0
        row = existing.get(slot.start)
        if row is None:
            row = WfmIntervalHistory(
                tenant_id=tenant_id,
                environment_id=environment_id,
                queue_key=key,
                interval_start=slot.start,
                interval_minutes=interval_minutes,
                timezone=zone_name,
                source=source,
            )
            session.add(row)
            written += 1
        else:
            updated += 1
        row.interval_minutes = interval_minutes
        row.timezone = zone_name
        row.offered = int(values["offered"])
        row.handled = int(values["handled"])
        row.abandoned = int(values["abandoned"])
        row.total_handle_seconds = values["total_handle_seconds"]
        row.total_speed_answer_seconds = values["total_speed_answer_seconds"]
        row.agents_available = int(values["agents_available"])
        row.is_gap = is_gap
        row.source = source
        row.computed_at = now
    if commit:
        await session.commit()
    return RollupResult(
        queue_key=key,
        intervals=len(slots),
        written=written,
        updated=updated,
        gaps=gaps,
        start=window_start,
        end=window_end,
    )


async def rollup_environment_history(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    start_date: date,
    end_date: date,
    zone_name: str = "UTC",
    interval_minutes: int = 15,
    commit: bool = True,
) -> list[RollupResult]:
    """Roll up every queue in the environment plus the environment-wide total.

    The ``""`` queue key is the sum across queues, computed from the same entries
    so it always agrees with the per-queue rows instead of being a second
    aggregate that drifts.
    """
    queue_ids = list(
        (
            await session.execute(
                select(Queue.id).where(
                    Queue.tenant_id == tenant_id,
                    Queue.environment_id == environment_id,
                )
            )
        )
        .scalars()
        .all()
    )
    results = [
        await rollup_queue_history(
            session,
            tenant_id=tenant_id,
            environment_id=environment_id,
            queue_id=qid,
            start_date=start_date,
            end_date=end_date,
            zone_name=zone_name,
            interval_minutes=interval_minutes,
            commit=False,
        )
        for qid in queue_ids
    ]
    total = await rollup_queue_history(
        session,
        tenant_id=tenant_id,
        environment_id=environment_id,
        queue_id=None,
        start_date=start_date,
        end_date=end_date,
        zone_name=zone_name,
        interval_minutes=interval_minutes,
        commit=False,
    )
    if commit:
        await session.commit()
    return results + [total]


async def load_history(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    queue_key: str = "",
    start: datetime | None = None,
    end: datetime | None = None,
    include_gaps: bool = False,
) -> list[WfmIntervalHistory]:
    """Read stored intervals in chronological order."""
    stmt = select(WfmIntervalHistory).where(
        WfmIntervalHistory.tenant_id == tenant_id,
        WfmIntervalHistory.environment_id == environment_id,
        WfmIntervalHistory.queue_key == queue_key,
    )
    if start is not None:
        stmt = stmt.where(WfmIntervalHistory.interval_start >= start)
    if end is not None:
        stmt = stmt.where(WfmIntervalHistory.interval_start < end)
    if not include_gaps:
        stmt = stmt.where(WfmIntervalHistory.is_gap.is_(False))
    stmt = stmt.order_by(WfmIntervalHistory.interval_start)
    return list((await session.execute(stmt)).scalars().all())


@dataclass(frozen=True)
class HistorySummary:
    """The handful of numbers an operations lead actually looks at."""

    intervals: int
    offered: int
    handled: int
    abandoned: int
    abandon_rate: float
    average_handle_seconds: float
    average_speed_answer_seconds: float
    service_level: float
    occupied_seconds: float
    paid_seconds: float
    shrinkage: float

    def as_dict(self) -> dict[str, object]:
        return {
            "intervals": self.intervals,
            "offered": self.offered,
            "handled": self.handled,
            "abandoned": self.abandoned,
            "abandon_rate": round(self.abandon_rate, 6),
            "average_handle_seconds": round(self.average_handle_seconds, 2),
            "average_speed_answer_seconds": round(self.average_speed_answer_seconds, 2),
            "service_level": round(self.service_level, 6),
            "occupied_seconds": round(self.occupied_seconds, 2),
            "paid_seconds": round(self.paid_seconds, 2),
            "shrinkage": round(self.shrinkage, 6),
        }


def summarise(
    rows: list[WfmIntervalHistory],
    *,
    answer_target_seconds: float = 20.0,
) -> HistorySummary:
    """Derive AHT, abandonment, service level and shrinkage from stored rows.

    Service level uses the stored wait *total* rather than a per-call histogram,
    so it is exact for the answered population and deliberately silent about
    unanswered calls (which are counted as misses by ``offered`` instead).
    """
    offered = sum(int(row.offered or 0) for row in rows)
    handled = sum(int(row.handled or 0) for row in rows)
    abandoned = sum(int(row.abandoned or 0) for row in rows)
    handle_seconds = sum(float(row.total_handle_seconds or 0.0) for row in rows)
    wait_seconds = sum(float(row.total_speed_answer_seconds or 0.0) for row in rows)
    answered = handled or 0
    aht = handle_seconds / answered if answered else 0.0
    asa = wait_seconds / answered if answered else 0.0
    # Answered calls whose *mean* wait is inside target is the only estimate a
    # total can support; the router reports the exact distribution separately.
    within_target = answered if asa <= answer_target_seconds and answered else 0
    service = within_target / offered if offered else 0.0
    occupied = handle_seconds
    # ``agents_available`` is stored in agent-*minutes* (see ``Sample.as_row``), so paid
    # time is that many seconds. Multiplying by the interval length again would price a
    # single fifteen-minute interval as a whole day of one agent.
    paid = sum(max(0.0, float(row.agents_available or 0)) * 60.0 for row in rows)
    return HistorySummary(
        intervals=len(rows),
        offered=offered,
        handled=handled,
        abandoned=abandoned,
        abandon_rate=(abandoned / offered) if offered else 0.0,
        average_handle_seconds=aht,
        average_speed_answer_seconds=asa,
        service_level=min(1.0, max(0.0, service)),
        occupied_seconds=occupied,
        paid_seconds=paid,
        shrinkage=(1.0 - occupied / paid) if paid else 0.0,
    )


async def state_minutes(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> dict[str, float]:
    """Total minutes per agent state in a window — the shrinkage ledger.

    Used by adherence and by the "why is the rota short" question: if ``away``
    minutes are 30% of paid minutes, no staffing model will close the gap.
    """
    totals: dict[str, float] = defaultdict(float)
    for interval in await _agent_intervals(
        session,
        tenant_id=tenant_id,
        environment_id=environment_id,
        start=start,
        end=end,
    ):
        seconds = _overlap_seconds(interval, _as_utc(start), _as_utc(end))
        if seconds > 0:
            totals[interval.state] += seconds / 60.0
    return {state: round(minutes, 2) for state, minutes in sorted(totals.items())}


async def coverage_windows(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Merged spans where at least one agent was in *any* paid state.

    Publishing uses this to refuse a rota that leaves a queue uncovered, and the
    intraday view uses it to tell "quiet" from "closed".
    """
    spans = sorted(
        (
            max(_as_utc(interval.started_at), _as_utc(start)),
            min(_as_utc(interval.ended_at) if interval.ended_at else _as_utc(end), _as_utc(end)),
        )
        for interval in await _agent_intervals(
            session,
            tenant_id=tenant_id,
            environment_id=environment_id,
            start=start,
            end=end,
        )
    )
    merged: list[tuple[datetime, datetime]] = []
    for low, high in spans:
        if high <= low:
            continue
        if merged and low <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return merged


def availability_between(
    spans: list[tuple[datetime, datetime]], start: datetime, end: datetime
) -> float:
    """Seconds of ``[start, end)`` covered by merged spans."""
    total = 0.0
    for low, high in spans:
        low = max(low, start)
        high = min(high, end)
        if high > low:
            total += (high - low).total_seconds()
    return total


def window_bounds(start_date: date, end_date: date, zone_name: str) -> tuple[datetime, datetime]:
    """UTC half-open bounds for a local date range (inclusive of both dates)."""
    zone = tenant_zone(zone_name)
    start = datetime.combine(start_date, dtime(0, 0), tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(
        end_date + timedelta(days=1), dtime(0, 0), tzinfo=zone
    ).astimezone(timezone.utc)
    return start, end


def is_within(coverage: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> bool:
    """True when the whole window is covered by at least one span."""
    return availability_between(coverage, start, end) >= (end - start).total_seconds()


__all__ = [
    "AVAILABLE_STATES",
    "HistorySummary",
    "IntervalSample",
    "IntervalSlot",
    "RollupResult",
    "SHRINKAGE_STATES",
    "VALID_INTERVAL_MINUTES",
    "WORK_STATES",
    "availability_between",
    "coverage_windows",
    "interval_grid",
    "is_within",
    "load_history",
    "rollup_environment_history",
    "rollup_queue_history",
    "slot_for",
    "state_minutes",
    "summarise",
    "tenant_zone",
    "window_bounds",
]
