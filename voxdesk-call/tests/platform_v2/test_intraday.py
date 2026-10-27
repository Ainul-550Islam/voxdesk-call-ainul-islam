"""Intraday: where today stands against plan, and what the rest of it needs.

The board is what a supervisor acts on, so the tests are written from that side:
the scale is shrunk toward 1.0 when the sample is thin (re-planning on two intervals
is how a team chases its own tail), the remaining plan only covers the future, and
the alert level is a colour, not a policy.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserRole
from app.db.platform_v2_models import WfmForecast, WfmIntervalHistory
from app.platform_v2.wfm import intraday
from app.platform_v2.wfm.forecast import ForecastPoint
from app.platform_v2.wfm.staffing import StaffingTarget
from tests.conftest import make_user
from tests.platform_v2.conftest import PlatScope, seed_shift

DAY = date(2026, 11, 9)
ZONE = "UTC"
QUEUE = "support"
INTERVAL = 30


def at(hour: int, minute: int = 0) -> datetime:
    """A UTC instant on :data:`DAY`."""
    return datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=UTC)


def points(volumes: list[float], *, start: datetime | None = None) -> list[ForecastPoint]:
    first = start or at(8)
    return [
        ForecastPoint(
            interval_start=first + timedelta(minutes=INTERVAL * index),
            interval_minutes=INTERVAL,
            expected=volume,
            low=volume * 0.8,
            high=volume * 1.2,
        )
        for index, volume in enumerate(volumes)
    ]


def forecast_row(scope: PlatScope, rows: list[ForecastPoint], *, generated_at=None) -> WfmForecast:
    return WfmForecast(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        queue_key=QUEUE,
        timezone=ZONE,
        interval_minutes=INTERVAL,
        horizon_days=1,
        history_days=28,
        method="seasonal_interval",
        model_version="v1",
        points=[row.as_dict() for row in rows],
        generated_at=generated_at or datetime.now(tz=UTC),
    )


def actual(scope: PlatScope, start: datetime, offered: float, *, gap: bool = False) -> WfmIntervalHistory:
    return WfmIntervalHistory(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        queue_key=QUEUE,
        interval_start=start,
        interval_minutes=INTERVAL,
        timezone=ZONE,
        offered=offered,
        handled=offered,
        abandoned=0,
        is_gap=gap,
    )


# --- observed_scale ------------------------------------------------------------


def test_no_pairs_leaves_the_forecast_alone():
    assert intraday.observed_scale([]) == 1.0


def test_a_forecast_of_zero_is_not_a_denominator():
    assert intraday.observed_scale([(0.0, 5.0)]) == 1.0


def test_a_perfect_day_does_not_move_the_scale():
    assert intraday.observed_scale([(10.0, 10.0), (10.0, 10.0)]) == pytest.approx(1.0)


def test_one_interval_barely_moves_the_scale():
    """One interval observed means the forecast keeps four fifths of the vote."""
    assert intraday.observed_scale([(10.0, 20.0)]) == pytest.approx(1.2)


def test_many_intervals_move_the_scale_most_of_the_way():
    scale = intraday.observed_scale([(10.0, 20.0)] * 12)
    assert scale == pytest.approx(1.0 + 12 / 16, abs=1e-6)
    assert scale > intraday.observed_scale([(10.0, 20.0)])


def test_the_scale_is_clamped_in_both_directions():
    assert intraday.observed_scale([(10.0, 1000.0)] * 50) == intraday.MAX_SCALE
    assert intraday.observed_scale([(10.0, 0.1)] * 50) == intraday.MIN_SCALE


def test_the_shrinkage_weight_is_configurable():
    assert intraday.observed_scale([(10.0, 20.0)], reliable_samples=1) == pytest.approx(1.5)


# --- compare_points ------------------------------------------------------------


def test_an_interval_after_the_cutoff_has_no_variance_yet():
    comparisons = intraday.compare_points(
        points([10.0, 10.0, 10.0]), {at(8): 12.0}, as_of=at(9)
    )
    first, second, third = comparisons
    assert first.actual == 12.0 and first.variance == 2.0
    assert first.variance_ratio == pytest.approx(0.2)
    assert first.passed is True
    assert second.passed is True and second.actual is None and second.variance is None
    assert third.passed is False


def test_a_forecast_of_zero_has_no_ratio_but_still_has_a_variance():
    comparison = intraday.IntervalComparison(at(8), 0.0, 5.0, True)
    assert comparison.variance == 5.0
    assert comparison.variance_ratio is None


def test_comparisons_come_back_in_time_order():
    shuffled = [points([1.0], start=at(9))[0], points([1.0], start=at(8))[0]]
    ordered = intraday.compare_points(shuffled, {}, as_of=at(10))
    assert [item.interval_start for item in ordered] == [at(8), at(9)]


def test_the_interval_payload_rounds_for_the_board():
    comparison = intraday.IntervalComparison(at(8), 10.0, 12.3456, True)
    payload = comparison.as_dict()
    assert payload["actual"] == 12.346
    assert payload["variance_ratio"] == 0.2346
    assert set(payload) == {
        "interval_start",
        "forecast",
        "actual",
        "variance",
        "variance_ratio",
        "passed",
    }


# --- remaining_plan ------------------------------------------------------------


def test_the_remaining_plan_starts_strictly_after_the_cutoff():
    rows = intraday.remaining_plan(
        points([10.0] * 4), as_of=at(8, 30), scale=1.0, target=StaffingTarget()
    )
    # Four points at 08:00/08:30/09:00/09:30: the interval containing the cutoff is
    # already under way, so only the two after it are still plannable.
    assert [row["interval_start"] for row in rows] == [
        at(9).isoformat(),
        at(9, 30).isoformat(),
    ]


def test_the_remaining_plan_is_scaled_by_what_has_been_observed():
    base = intraday.remaining_plan(
        points([10.0] * 2), as_of=at(8), scale=1.0, target=StaffingTarget()
    )
    scaled = intraday.remaining_plan(
        points([10.0] * 2), as_of=at(8), scale=2.0, target=StaffingTarget()
    )
    assert base[0]["forecast"] == pytest.approx(10.0)
    assert scaled[0]["forecast"] == pytest.approx(20.0)
    assert scaled[0]["paid_agents"] >= base[0]["paid_agents"]


def test_a_delta_is_only_computed_when_the_rota_is_known():
    target = StaffingTarget()
    without = intraday.remaining_plan(points([40.0]), as_of=at(7), scale=1.0, target=target)
    assert without[0]["delta"] is None
    with_rota = intraday.remaining_plan(
        points([40.0]), as_of=at(7), scale=1.0, target=target, scheduled_agents=99
    )
    assert with_rota[0]["delta"] == with_rota[0]["paid_agents"] - 99
    assert with_rota[0]["delta"] < 0, "99 people on a quiet interval is over-coverage"


def test_quiet_intervals_need_nobody():
    rows = intraday.remaining_plan(
        points([0.0]), as_of=at(7), scale=1.0, target=StaffingTarget(), scheduled_agents=0
    )
    assert rows[0]["paid_agents"] == 0
    assert rows[0]["delta"] == 0


# --- actuals and the rota ------------------------------------------------------


async def test_actuals_are_read_per_queue_and_skip_gaps(
    db: AsyncSession, plat_scope: PlatScope
):
    db.add_all(
        [
            actual(plat_scope, at(8), 10.0),
            actual(plat_scope, at(8, 30), 0.0, gap=True),
            actual(plat_scope, at(9), 12.0),
        ]
    )
    other = actual(plat_scope, at(9, 30), 99.0)
    other.queue_key = "sales"
    db.add(other)
    await db.commit()
    actuals = await intraday.actuals_for_day(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key=QUEUE,
        start=at(0),
        end=at(0) + timedelta(days=1),
    )
    assert actuals[at(8)] == 10.0
    assert actuals[at(9)] == 12.0
    assert at(8, 30) not in actuals, "a gap bucket is not an actual of zero"
    assert at(9, 30) not in actuals


async def test_actuals_are_bounded_by_the_window(db: AsyncSession, plat_scope: PlatScope):
    db.add_all([actual(plat_scope, at(8), 10.0), actual(plat_scope, at(20), 30.0)])
    await db.commit()
    actuals = await intraday.actuals_for_day(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key=QUEUE,
        start=at(9),
        end=at(19),
    )
    assert actuals == {}


async def test_the_rota_count_only_includes_people_who_are_on(
    db: AsyncSession, plat_scope: PlatScope
):
    agent = await make_user(
        db, plat_scope.tenant, UserRole.AGENT, email=f"intra-{uuid.uuid4().hex[:6]}@example.com"
    )
    await seed_shift(db, plat_scope, agent, starts_at=at(8), hours=4)
    await seed_shift(db, plat_scope, agent, starts_at=at(14), hours=4)
    dropped = await seed_shift(db, plat_scope, agent, starts_at=at(8), hours=4, status="dropped")
    assert dropped.status == "dropped"
    on_shift = await intraday.scheduled_agents_now(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        at=at(9),
    )
    assert on_shift == 1
    later = await intraday.scheduled_agents_now(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        at=at(15),
    )
    assert later == 1


async def test_a_rota_count_of_zero_is_reported_as_zero_not_as_unknown(
    db: AsyncSession, plat_scope: PlatScope
):
    assert (
        await intraday.scheduled_agents_now(
            db,
            tenant_id=plat_scope.tenant.id,
            environment_id=plat_scope.environment.id,
            at=at(3),
        )
        == 0
    )


# --- latest_forecast -----------------------------------------------------------


async def test_the_newest_forecast_covering_the_moment_wins(
    db: AsyncSession, plat_scope: PlatScope
):
    db.add_all(
        [
            forecast_row(
                plat_scope, points([1.0] * 4), generated_at=datetime.now(tz=UTC) - timedelta(days=3)
            ),
            forecast_row(plat_scope, points([9.0] * 4), generated_at=datetime.now(tz=UTC)),
        ]
    )
    await db.commit()
    row = await intraday.latest_forecast(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key=QUEUE,
        at=at(9),
    )
    assert row is not None
    assert row.points[0]["expected"] == 9.0


async def test_a_forecast_whose_horizon_has_passed_is_ignored(
    db: AsyncSession, plat_scope: PlatScope
):
    db.add(forecast_row(plat_scope, points([9.0] * 4)))
    await db.commit()
    row = await intraday.latest_forecast(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key=QUEUE,
        at=at(8) + timedelta(days=3),
    )
    assert row is None


async def test_a_forecast_with_no_points_is_skipped(db: AsyncSession, plat_scope: PlatScope):
    db.add(forecast_row(plat_scope, []))
    await db.commit()
    assert (
        await intraday.latest_forecast(
            db,
            tenant_id=plat_scope.tenant.id,
            environment_id=plat_scope.environment.id,
            queue_key=QUEUE,
            at=at(9),
        )
        is None
    )


# --- snapshot ------------------------------------------------------------------


async def test_without_a_forecast_the_board_is_empty_but_valid(
    db: AsyncSession, plat_scope: PlatScope
):
    snapshot = await intraday.snapshot(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key=QUEUE,
        as_of=at(9),
    )
    payload = snapshot.as_dict()
    assert payload["intervals"] == []
    assert payload["remaining"] == []
    assert payload["scale"] == 1.0
    assert payload["sample_count"] == 0
    assert payload["variance_ratio"] is None


async def test_the_board_compares_today_against_the_forecast(
    db: AsyncSession, plat_scope: PlatScope
):
    db.add(forecast_row(plat_scope, points([10.0] * 6)))
    db.add_all([actual(plat_scope, at(8), 20.0), actual(plat_scope, at(8, 30), 20.0)])
    await db.commit()
    snapshot = await intraday.snapshot(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key=QUEUE,
        as_of=at(8, 30),
    )
    payload = snapshot.as_dict()
    # Only the 08:00 interval has finished at 08:30, so only it is scored.
    assert payload["elapsed_forecast"] == pytest.approx(10.0)
    assert payload["elapsed_actual"] == pytest.approx(20.0)
    assert payload["variance_ratio"] == pytest.approx(1.0)
    assert payload["sample_count"] == 1
    assert payload["scale"] > 1.0, "twice the forecasted volume raises the re-forecast"
    assert payload["remaining"], "the rest of the day is re-planned"


async def test_the_board_only_counts_actuals_from_today(db: AsyncSession, plat_scope: PlatScope):
    db.add(forecast_row(plat_scope, points([10.0] * 4)))
    db.add(actual(plat_scope, at(8) - timedelta(days=1), 500.0))
    await db.commit()
    snapshot = await intraday.snapshot(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key=QUEUE,
        as_of=at(9),
    )
    assert snapshot.as_dict()["elapsed_actual"] == 0.0


async def test_the_board_carries_the_delta_against_the_rota(
    db: AsyncSession, plat_scope: PlatScope
):
    db.add(forecast_row(plat_scope, points([60.0] * 6, start=at(7))))
    agent = await make_user(
        db, plat_scope.tenant, UserRole.AGENT, email=f"intra2-{uuid.uuid4().hex[:6]}@example.com"
    )
    await seed_shift(db, plat_scope, agent, starts_at=at(8), hours=8)
    await db.commit()
    snapshot = await intraday.snapshot(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key=QUEUE,
        as_of=at(7),
    )
    assert snapshot.remaining
    assert all(row["delta"] is not None for row in snapshot.remaining)
    assert snapshot.remaining[0]["delta"] > 0, "one person cannot cover 60 half-hours of demand"


# --- alert_level ---------------------------------------------------------------


def _snapshot(variance_ratio: float | None, *, short: bool) -> intraday.IntradaySnapshot:
    comparisons = (
        []
        if variance_ratio is None
        else [intraday.IntervalComparison(at(8), 10.0, 10.0 * (1 + variance_ratio), True)]
    )
    remaining = [{"interval_start": at(9).isoformat(), "delta": 2 if short else 0}]
    return intraday.IntradaySnapshot(QUEUE, at(9), comparisons, 1.0, 1, remaining)


def test_no_data_is_ok():
    assert intraday.alert_level(_snapshot(None, short=False)) == "ok"


def test_within_tolerance_is_ok():
    assert intraday.alert_level(_snapshot(0.10, short=True)) == "ok"


def test_off_plan_without_a_staffing_gap_is_a_watch():
    assert intraday.alert_level(_snapshot(0.5, short=False)) == "watch"


def test_far_off_plan_with_people_missing_is_an_act():
    assert intraday.alert_level(_snapshot(0.5, short=True)) == "act"


def test_the_tolerance_is_an_argument_not_a_policy():
    assert intraday.alert_level(_snapshot(0.5, short=True), tolerance=0.6) == "ok"
