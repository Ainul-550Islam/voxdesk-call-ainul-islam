"""Interval history: the grid, the rollup, gaps, and the summary an ops lead reads.

The rollup is where every downstream number comes from, so the tests build real rows in
the tables it reads (``cc_queues``, ``cc_queue_entries``, ``calls``,
``cc2_agent_state_periods``) and then assert on the stored intervals. Nothing is mocked.

Two properties matter more than the arithmetic:

* **A gap is not a zero.** A bucket with no traffic and nobody logged in is outside
  coverage; letting the forecaster learn "we always took zero calls at 04:00" is how a
  model ends up under-staffing a night shift that never existed.
* **Re-running is safe.** The rollup upserts, so a late CDR corrects history instead of
  adding a second opinion that double-counts.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.models import Queue, QueueEntry
from app.db.contact_center_v2_models import AgentStateInterval
from app.db.models import Call, UserRole
from app.db.platform_v2_models import WfmIntervalHistory
from app.platform_v2.errors import ValidationFailed
from app.platform_v2.wfm import history
from tests.conftest import make_user
from tests.platform_v2.conftest import PlatScope

DHAKA = "Asia/Dhaka"


def utc(value: datetime) -> datetime:
    """SQLite drops the offset on read; Postgres keeps it. Compare in UTC either way."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
LONDON = "Europe/London"
DAY = date(2026, 10, 5)


# --- the grid ------------------------------------------------------------------


def test_the_grid_follows_the_local_day_not_utc():
    slots = history.interval_grid(DHAKA, DAY, DAY, 15)
    assert len(slots) == 96
    # Dhaka is UTC+6, so the local 00:00 bucket starts at 18:00Z the day before.
    assert slots[0].start == datetime(2026, 10, 4, 18, 0, tzinfo=UTC)
    assert slots[0].local_label == "2026-10-05 00:00"
    assert slots[-1].end == datetime(2026, 10, 5, 18, 0, tzinfo=UTC)


def test_a_spring_forward_day_is_short_and_a_fall_back_day_is_long():
    """A wall-clock walk is the only grid that survives an audit."""
    spring = history.interval_grid(LONDON, date(2026, 3, 29), date(2026, 3, 29), 15)
    autumn = history.interval_grid(LONDON, date(2026, 10, 25), date(2026, 10, 25), 15)
    assert len(spring) == 23 * 4, "the 01:00-02:00 wall clock does not exist"
    assert len(autumn) == 25 * 4, "the 01:00-02:00 wall clock happens twice"
    assert len({slot.start for slot in autumn}) == len(autumn), "no duplicate instants"
    assert len({slot.local_label for slot in autumn}) < len(autumn), (
        "the repeated wall clock must appear twice, under one label"
    )


def test_the_grid_is_contiguous():
    slots = history.interval_grid(DHAKA, DAY, DAY + timedelta(days=1), 30)
    for first, second in zip(slots, slots[1:]):
        assert first.end == second.start
    assert len(slots) == 2 * 48


@pytest.mark.parametrize(
    "minutes",
    [0, 10, 20, 60],
    ids=["zero", "ten", "twenty", "sixty"],
)
def test_an_unsupported_interval_is_refused(minutes):
    with pytest.raises(ValidationFailed):
        history.interval_grid("UTC", DAY, DAY, minutes)


def test_a_reversed_range_is_refused():
    with pytest.raises(ValidationFailed):
        history.interval_grid("UTC", DAY, DAY - timedelta(days=1))


def test_slot_for_finds_the_containing_bucket_and_nothing_outside_it():
    slots = history.interval_grid("UTC", DAY, DAY, 15)
    inside = slots[5].start + timedelta(minutes=7)
    assert history.slot_for(inside, slots) is slots[5]
    assert history.slot_for(slots[-1].end, slots) is None
    assert history.slot_for(slots[0].start - timedelta(seconds=1), slots) is None


# --- fixtures ------------------------------------------------------------------


async def _queue(db: AsyncSession, scope: PlatScope, name: str = "Support") -> Queue:
    row = Queue(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        name=name,
    )
    db.add(row)
    await db.commit()
    return row


async def _entry(
    db: AsyncSession,
    scope: PlatScope,
    queue: Queue,
    *,
    enqueued_at: datetime,
    status: str = "completed",
    assigned_delay_seconds: int | None = 12,
    duration_seconds: float | None = 180.0,
) -> QueueEntry:
    call = Call(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        call_sid=f"CA-{uuid.uuid4().hex[:12]}",
        from_number="+15550000001",
        to_number="+15550000002",
        status="completed",
        started_at=enqueued_at,
        ended_at=enqueued_at + timedelta(seconds=duration_seconds or 0),
        duration_seconds=duration_seconds or 0.0,
        direction="inbound",
    )
    db.add(call)
    await db.flush()
    entry = QueueEntry(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        queue_id=queue.id,
        call_id=call.id,
        status=status,
        enqueued_at=enqueued_at,
        assigned_at=(
            enqueued_at + timedelta(seconds=assigned_delay_seconds)
            if assigned_delay_seconds is not None
            else None
        ),
        outcome=status,
    )
    db.add(entry)
    await db.commit()
    return entry


async def _state(
    db: AsyncSession,
    scope: PlatScope,
    user_id: uuid.UUID,
    *,
    state: str,
    started_at: datetime,
    ended_at: datetime | None,
) -> AgentStateInterval:
    row = AgentStateInterval(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        user_id=user_id,
        state=state,
        started_at=started_at,
        ended_at=ended_at,
        source_id="test",
    )
    db.add(row)
    await db.commit()
    return row


# --- the rollup ----------------------------------------------------------------


async def test_the_rollup_counts_offered_handled_and_abandoned(db: AsyncSession, plat_scope: PlatScope):
    queue = await _queue(db, plat_scope)
    base = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    await _entry(db, plat_scope, queue, enqueued_at=base)
    await _entry(db, plat_scope, queue, enqueued_at=base + timedelta(minutes=2))
    await _entry(
        db,
        plat_scope,
        queue,
        enqueued_at=base + timedelta(minutes=4),
        status="abandoned",
        assigned_delay_seconds=None,
        duration_seconds=None,
    )

    result = await history.rollup_queue_history(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_id=queue.id,
        start_date=DAY,
        end_date=DAY,
        zone_name="UTC",
        interval_minutes=15,
    )
    assert result.intervals == 96
    assert result.written == 96
    rows = await history.load_history(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key=str(queue.id),
    )
    busiest = max(rows, key=lambda row: row.offered)
    assert utc(busiest.interval_start) == base
    assert (busiest.offered, busiest.handled, busiest.abandoned) == (3, 2, 1)


async def test_handle_time_is_credited_to_the_interval_the_call_ended_in(
    db: AsyncSession, plat_scope: PlatScope
):
    queue = await _queue(db, plat_scope)
    # Enqueued at 08:59:30, ends at 09:02:30 — the 09:00 bucket earns the handle time.
    started = datetime(2026, 10, 5, 8, 59, 30, tzinfo=UTC)
    await _entry(
        db,
        plat_scope,
        queue,
        enqueued_at=started,
        duration_seconds=180.0,
    )
    await history.rollup_queue_history(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_id=queue.id,
        start_date=DAY,
        end_date=DAY,
        zone_name="UTC",
        interval_minutes=15,
    )
    # The 09:00 bucket has no traffic of its own and nobody logged in, so it is a gap
    # and only appears when gaps are asked for — which is the point of the next test.
    rows = await history.load_history(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key=str(queue.id),
        include_gaps=True,
    )
    by_start = {utc(row.interval_start): row for row in rows}
    assert by_start[datetime(2026, 10, 5, 8, 45, tzinfo=UTC)].offered == 1
    assert by_start[datetime(2026, 10, 5, 8, 45, tzinfo=UTC)].total_handle_seconds == 0.0
    assert by_start[datetime(2026, 10, 5, 9, 0, tzinfo=UTC)].total_handle_seconds == 180.0


async def test_a_quiet_bucket_with_nobody_logged_in_is_a_gap(db: AsyncSession, plat_scope: PlatScope):
    queue = await _queue(db, plat_scope)
    await _entry(db, plat_scope, queue, enqueued_at=datetime(2026, 10, 5, 9, 1, tzinfo=UTC))
    staff = await make_user(db, plat_scope.tenant, UserRole.AGENT, email="agent-gap@example.com")
    # Availability only in the 09:00 bucket; the rest of the day is uncovered.
    await _state(
        db,
        plat_scope,
        staff.id,
        state="available",
        started_at=datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
        ended_at=datetime(2026, 10, 5, 9, 15, tzinfo=UTC),
    )
    result = await history.rollup_queue_history(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_id=queue.id,
        start_date=DAY,
        end_date=DAY,
        zone_name="UTC",
        interval_minutes=15,
    )
    assert result.gaps == 95, "every bucket except the staffed one is uncovered"

    with_gaps = await history.load_history(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key=str(queue.id),
        include_gaps=True,
    )
    assert len(with_gaps) == 96
    staffed = [row for row in with_gaps if not row.is_gap]
    assert len(staffed) == 1
    assert staffed[0].agents_available == 15, "agent-*minutes*, not a headcount"


async def test_a_dropped_call_in_a_covered_bucket_is_not_a_gap(db: AsyncSession, plat_scope: PlatScope):
    """Zero demand while agents were present is data: the queue was open and quiet."""
    queue = await _queue(db, plat_scope)
    staff = await make_user(db, plat_scope.tenant, UserRole.AGENT, email="agent-quiet@example.com")
    await _state(
        db,
        plat_scope,
        staff.id,
        state="available",
        started_at=datetime(2026, 10, 5, 11, 0, tzinfo=UTC),
        ended_at=datetime(2026, 10, 5, 11, 15, tzinfo=UTC),
    )
    await history.rollup_queue_history(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_id=queue.id,
        start_date=DAY,
        end_date=DAY,
        zone_name="UTC",
        interval_minutes=15,
    )
    row = (
        await db.execute(
            select(WfmIntervalHistory).where(
                WfmIntervalHistory.queue_key == str(queue.id),
                WfmIntervalHistory.interval_start == datetime(2026, 10, 5, 11, 0, tzinfo=UTC),
            )
        )
    ).scalar_one()
    assert row.is_gap is False
    assert row.offered == 0


async def test_running_the_rollup_twice_updates_instead_of_duplicating(
    db: AsyncSession, plat_scope: PlatScope
):
    queue = await _queue(db, plat_scope)
    at = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    await _entry(db, plat_scope, queue, enqueued_at=at, assigned_delay_seconds=None)
    first = await history.rollup_queue_history(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_id=queue.id,
        start_date=DAY,
        end_date=DAY,
        zone_name="UTC",
        interval_minutes=15,
    )
    # A late CDR arrives: the same bucket must be corrected, not added to.
    await _entry(db, plat_scope, queue, enqueued_at=at + timedelta(minutes=1), assigned_delay_seconds=None)
    second = await history.rollup_queue_history(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_id=queue.id,
        start_date=DAY,
        end_date=DAY,
        zone_name="UTC",
        interval_minutes=15,
    )
    assert first.written == 96
    assert second.written == 0 and second.updated == 96
    total = (
        await db.execute(
            select(func.count())
            .select_from(WfmIntervalHistory)
            .where(WfmIntervalHistory.queue_key == str(queue.id))
        )
    ).scalar_one()
    assert total == 96
    row = (
        await db.execute(
            select(WfmIntervalHistory).where(
                WfmIntervalHistory.queue_key == str(queue.id),
                WfmIntervalHistory.interval_start == at,
            )
        )
    ).scalar_one()
    assert row.offered == 2


async def test_the_environment_rollup_totals_every_queue(db: AsyncSession, plat_scope: PlatScope):
    support = await _queue(db, plat_scope, "Support")
    sales = await _queue(db, plat_scope, "Sales")
    at = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    await _entry(db, plat_scope, support, enqueued_at=at)
    await _entry(db, plat_scope, sales, enqueued_at=at)
    await _entry(db, plat_scope, sales, enqueued_at=at + timedelta(minutes=1))

    results = await history.rollup_environment_history(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        start_date=DAY,
        end_date=DAY,
        zone_name="UTC",
        interval_minutes=15,
    )
    keys = {result.queue_key for result in results}
    assert keys == {str(support.id), str(sales.id), ""}
    total = next(result for result in results if result.queue_key == "")
    assert total.intervals == 96

    rows = await history.load_history(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key="",
    )
    by_start = {utc(row.interval_start): row for row in rows}
    assert by_start[at].offered == 3, "the total row agrees with the per-queue rows"


# --- the summary ---------------------------------------------------------------


def _row(**kwargs) -> WfmIntervalHistory:
    defaults = {
        "tenant_id": uuid.uuid4(),
        "environment_id": uuid.uuid4(),
        "queue_key": "",
        "interval_start": datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
        "interval_minutes": 15,
        "timezone": "UTC",
        "offered": 10,
        "handled": 8,
        "abandoned": 2,
        "total_handle_seconds": 8 * 200.0,
        "total_speed_answer_seconds": 8 * 10.0,
        "agents_available": 15,
        "is_gap": False,
        "source": "reconstructed",
    }
    defaults.update(kwargs)
    return WfmIntervalHistory(**defaults)


def test_the_summary_derives_aht_abandonment_service_level_and_shrinkage():
    summary = history.summarise([_row(), _row(interval_start=datetime(2026, 10, 5, 9, 15, tzinfo=UTC))])
    assert summary.intervals == 2
    assert summary.offered == 20
    assert summary.handled == 16
    assert summary.abandon_rate == pytest.approx(0.2)
    assert summary.average_handle_seconds == pytest.approx(200.0)
    assert summary.average_speed_answer_seconds == pytest.approx(10.0)
    assert summary.service_level == pytest.approx(0.8), "16 answered of 20 offered"
    # 8 calls x 200 s = 1600 s occupied; 30 agent-minutes = 1800 s paid.
    assert summary.occupied_seconds == pytest.approx(3200.0)
    assert summary.paid_seconds == pytest.approx(1800.0)
    assert summary.shrinkage == pytest.approx(1 - 3200.0 / 1800.0)


def test_a_slow_answer_pushes_service_level_to_zero_not_to_a_guess():
    slow = _row(total_speed_answer_seconds=8 * 120.0)
    summary = history.summarise([slow], answer_target_seconds=20.0)
    assert summary.average_speed_answer_seconds == pytest.approx(120.0)
    assert summary.service_level == 0.0


def test_an_empty_history_summarises_to_zero_rather_than_dividing_by_zero():
    summary = history.summarise([])
    assert summary.intervals == 0
    assert summary.offered == 0
    assert summary.average_handle_seconds == 0.0
    assert summary.service_level == 0.0
    assert summary.shrinkage == 0.0


def test_the_summary_refuses_to_exceed_a_hundred_percent_service_level():
    """Paid time can be missing; a service level above 1.0 cannot happen."""
    summary = history.summarise([_row(handled=10, abandoned=0)], answer_target_seconds=600.0)
    assert summary.service_level == pytest.approx(1.0)


# --- availability --------------------------------------------------------------


async def test_coverage_windows_merge_touching_spans(db: AsyncSession, plat_scope: PlatScope):
    staff = await make_user(db, plat_scope.tenant, UserRole.AGENT, email="agent-cover@example.com")
    start = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    await _state(db, plat_scope, staff.id, state="available", started_at=start, ended_at=start + timedelta(minutes=30))
    await _state(
        db,
        plat_scope,
        staff.id,
        state="busy",
        started_at=start + timedelta(minutes=30),
        ended_at=start + timedelta(minutes=60),
    )
    await _state(
        db,
        plat_scope,
        staff.id,
        state="available",
        started_at=start + timedelta(hours=3),
        ended_at=start + timedelta(hours=4),
    )
    spans = await history.coverage_windows(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        start=start,
        end=start + timedelta(hours=6),
    )
    assert spans == [
        (start, start + timedelta(minutes=60)),
        (start + timedelta(hours=3), start + timedelta(hours=4)),
    ]
    assert history.availability_between(spans, start, start + timedelta(hours=6)) == 7200.0
    assert history.is_within(spans, start, start + timedelta(minutes=60)) is True
    assert history.is_within(spans, start, start + timedelta(hours=2)) is False


async def test_an_open_state_interval_runs_to_the_end_of_the_window(
    db: AsyncSession, plat_scope: PlatScope
):
    staff = await make_user(db, plat_scope.tenant, UserRole.AGENT, email="agent-open@example.com")
    start = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    await _state(db, plat_scope, staff.id, state="available", started_at=start, ended_at=None)
    minutes = await history.state_minutes(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        start=start,
        end=start + timedelta(minutes=45),
    )
    assert minutes == {"available": 45.0}


async def test_state_minutes_are_clipped_to_the_window(db: AsyncSession, plat_scope: PlatScope):
    staff = await make_user(db, plat_scope.tenant, UserRole.AGENT, email="agent-clip@example.com")
    await _state(
        db,
        plat_scope,
        staff.id,
        state="busy",
        started_at=datetime(2026, 10, 5, 8, 0, tzinfo=UTC),
        ended_at=datetime(2026, 10, 5, 10, 0, tzinfo=UTC),
    )
    minutes = await history.state_minutes(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        start=datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
        end=datetime(2026, 10, 5, 9, 30, tzinfo=UTC),
    )
    assert minutes == {"busy": 30.0}


def test_window_bounds_are_the_local_day_in_utc():
    start, end = history.window_bounds(DAY, DAY, DHAKA)
    zone = ZoneInfo(DHAKA)
    assert start == datetime(2026, 10, 4, 18, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 5, 18, 0, tzinfo=UTC)
    assert start.astimezone(zone).time() == time(0, 0)
    assert end.astimezone(zone).time() == time(0, 0)


def test_an_unknown_timezone_is_refused():
    with pytest.raises(ValidationFailed):
        history.tenant_zone("Mars/Olympus_Mons")
