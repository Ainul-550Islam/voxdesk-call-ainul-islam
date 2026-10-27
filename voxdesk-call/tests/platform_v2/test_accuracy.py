"""Forecast accuracy: measured from stored actuals, never asserted.

The point of this module is that a number on a dashboard is a *measurement*. So the
tests pin down: exact interval matching (no interpolated volume), the "nothing was
measured" case being distinct from a perfect score, and a score being recorded once
per window rather than overwritten by a later, flattering view.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.platform_v2_models import WfmForecast, WfmForecastAccuracy, WfmIntervalHistory
from app.platform_v2.errors import Conflict, ValidationFailed
from app.platform_v2.wfm import accuracy
from app.platform_v2.wfm.forecast import ForecastPoint
from tests.platform_v2.conftest import PlatScope

START = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
INTERVAL = 15


def points(volumes: list[float], *, start: datetime = START) -> list[ForecastPoint]:
    return [
        ForecastPoint(
            interval_start=start + timedelta(minutes=INTERVAL * index),
            interval_minutes=INTERVAL,
            expected=volume,
            low=volume * 0.8,
            high=volume * 1.2,
        )
        for index, volume in enumerate(volumes)
    ]


def actuals(volumes: list[float], *, start: datetime = START, gap: bool = False) -> list[WfmIntervalHistory]:
    return [
        WfmIntervalHistory(
            tenant_id=uuid.uuid4(),
            environment_id=uuid.uuid4(),
            queue_key="",
            interval_start=start + timedelta(minutes=INTERVAL * index),
            interval_minutes=INTERVAL,
            timezone="UTC",
            offered=volume,
            handled=volume,
            abandoned=0,
            is_gap=gap,
        )
        for index, volume in enumerate(volumes)
    ]


# --- pairing -------------------------------------------------------------------


def test_points_are_matched_on_the_exact_interval_start():
    pairs = accuracy.pair_points(points([10, 20]), actuals([12, 18]))
    assert [(item.expected, item.actual) for item in pairs] == [(10.0, 12.0), (20.0, 18.0)]


def test_a_point_with_no_actual_is_dropped_not_counted_as_zero():
    pairs = accuracy.pair_points(points([10, 20]), actuals([12]))
    assert len(pairs) == 1
    assert pairs[0].actual == 12.0


def test_gap_buckets_are_not_actuals():
    assert accuracy.pair_points(points([10]), actuals([0], gap=True)) == []


def test_pairs_come_back_in_time_order():
    pairs = accuracy.pair_points(points([10, 20]), list(reversed(actuals([12, 18]))))
    assert [item.interval_start for item in pairs] == [START, START + timedelta(minutes=INTERVAL)]


def test_a_naive_actual_timestamp_still_matches():
    """SQLite hands back naive datetimes; matching must not silently find nothing."""
    naive = actuals([12])
    naive[0].interval_start = naive[0].interval_start.replace(tzinfo=None)
    pairs = accuracy.pair_points(points([10]), naive)
    assert len(pairs) == 1


# --- the metrics ---------------------------------------------------------------


def test_an_exact_forecast_scores_zero_error():
    metrics = accuracy.evaluate(points([10, 20, 30]), actuals([10, 20, 30]))
    assert metrics.wape == 0.0
    assert metrics.mape == 0.0
    assert metrics.bias == 0.0
    assert metrics.scored is True


def test_wape_is_the_absolute_error_over_total_actual():
    metrics = accuracy.evaluate(points([10, 20, 30]), actuals([12, 18, 33]))
    assert metrics.total_actual == pytest.approx(63.0)
    assert metrics.total_forecast == pytest.approx(60.0)
    assert metrics.wape == pytest.approx((2 + 2 + 3) / 63)


def test_bias_is_signed_so_over_and_under_forecasting_differ():
    high = accuracy.evaluate(points([30, 30]), actuals([20, 20]))
    low = accuracy.evaluate(points([10, 10]), actuals([20, 20]))
    assert high.bias > 0, "forecasting above actual is positive"
    assert low.bias < 0
    assert high.wape == pytest.approx(low.wape), "WAPE is unsigned"


def test_metrics_are_fractions_not_percentages():
    metrics = accuracy.evaluate(points([110]), actuals([100]))
    assert metrics.wape == pytest.approx(0.1)
    assert metrics.mape == pytest.approx(0.1)


def test_a_zero_actual_is_left_out_of_mape_but_still_counted_in_wape():
    metrics = accuracy.evaluate(points([5, 10]), actuals([0, 10]))
    assert metrics.sample_count == 2
    assert metrics.mape == pytest.approx(0.0), "a zero actual has no percentage error"
    assert metrics.wape == pytest.approx(5 / 10)


def test_an_empty_window_is_unmeasured_not_perfect():
    metrics = accuracy.evaluate(points([10]), actuals([]))
    assert metrics.sample_count == 0
    assert metrics.scored is False
    assert metrics.wape == 0.0
    assert metrics.as_dict()["scored"] is False


def test_the_payload_rounds_and_names_what_it_measured():
    payload = accuracy.evaluate(points([10, 20]), actuals([12, 18])).as_dict()
    assert payload["wape"] == pytest.approx(0.1333, abs=1e-4)
    assert payload["sample_count"] == 2
    assert payload["total_actual"] == pytest.approx(30.0)
    assert "per_interval" not in payload


def test_the_per_interval_detail_is_available_on_request():
    payload = accuracy.evaluate(points([10]), actuals([12])).as_dict(include_intervals=True)
    interval = payload["per_interval"][0]
    assert interval["error"] == pytest.approx(-2.0)
    assert interval["percentage_error"] == pytest.approx(0.1667, abs=1e-4)


def test_the_blend_weight_falls_as_the_error_grows():
    good = accuracy.compute_metrics(accuracy.pair_points(points([100]), actuals([100])))
    bad = accuracy.compute_metrics(accuracy.pair_points(points([100]), actuals([20])))
    assert accuracy.blend_weight(good) > accuracy.blend_weight(bad)


# --- trends --------------------------------------------------------------------


def _metric_row(wape: float, computed_at: datetime) -> WfmForecastAccuracy:
    return WfmForecastAccuracy(
        tenant_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        queue_key="",
        forecast_id=uuid.uuid4(),
        evaluated_from=START,
        evaluated_to=START + timedelta(hours=1),
        wape=wape,
        mape=wape,
        bias=0.0,
        mae=1.0,
        sample_count=4,
        per_interval={},
        computed_at=computed_at,
    )


def test_an_empty_trend_is_unknown_not_zero():
    trend = accuracy.trend([])
    assert trend.samples == 0
    assert trend.direction == "unknown"


def test_the_trend_compares_the_latest_score_with_the_one_before():
    rows = [
        _metric_row(0.30, datetime.now(tz=UTC) - timedelta(days=2)),
        _metric_row(0.10, datetime.now(tz=UTC) - timedelta(days=1)),
    ]
    trend = accuracy.trend(rows)
    assert trend.samples == 2
    assert trend.last_wape == pytest.approx(0.10)
    assert trend.direction == "improving"


def test_a_worse_score_is_reported_as_worsening():
    rows = [
        _metric_row(0.10, datetime.now(tz=UTC) - timedelta(days=2)),
        _metric_row(0.40, datetime.now(tz=UTC) - timedelta(days=1)),
    ]
    assert accuracy.trend(rows).direction == "worsening"


def test_a_single_score_has_no_direction_to_report():
    """One number cannot move; ``flat`` says so without inventing a comparison."""
    trend = accuracy.trend([_metric_row(0.2, datetime.now(tz=UTC))])
    assert trend.samples == 1
    assert trend.direction == "flat"


# --- scoring a stored forecast -------------------------------------------------


def _stored_forecast(scope: PlatScope, rows: list[ForecastPoint]) -> WfmForecast:
    return WfmForecast(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        queue_key="",
        timezone="UTC",
        interval_minutes=INTERVAL,
        horizon_days=1,
        history_days=28,
        points=[row.as_dict() for row in rows],
    )


def _stored_actual(scope: PlatScope, start: datetime, offered: float) -> WfmIntervalHistory:
    return WfmIntervalHistory(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        queue_key="",
        interval_start=start,
        interval_minutes=INTERVAL,
        timezone="UTC",
        offered=offered,
        handled=offered,
        abandoned=0,
        is_gap=False,
    )


async def test_scoring_stores_the_measured_numbers(db: AsyncSession, plat_scope: PlatScope):
    past = datetime.now(tz=UTC) - timedelta(days=1)
    forecast = _stored_forecast(plat_scope, points([10, 10, 10, 10], start=past))
    db.add(forecast)
    for offset in range(4):
        db.add(_stored_actual(plat_scope, past + timedelta(minutes=INTERVAL * offset), 12))
    await db.commit()
    row = await accuracy.evaluate_forecast(db, forecast)
    assert row.sample_count == 4
    # Every interval is 10 forecast against 12 actual, so the ratio is exact.
    assert float(row.wape) == pytest.approx(2 / 12, abs=1e-4)
    assert float(row.bias) == pytest.approx(-2 / 12, abs=1e-4)
    assert row.per_interval["metrics"]["sample_count"] == 4


async def test_a_forecast_that_has_not_elapsed_is_refused(
    db: AsyncSession, plat_scope: PlatScope
):
    future = datetime.now(tz=UTC) + timedelta(days=3)
    forecast = _stored_forecast(plat_scope, points([10], start=future))
    db.add(forecast)
    await db.commit()
    with pytest.raises(ValidationFailed):
        await accuracy.evaluate_forecast(db, forecast)


async def test_an_elapsed_window_with_no_volume_records_nothing(
    db: AsyncSession, plat_scope: PlatScope
):
    """A zero-error row here would read as a perfect forecast later."""
    past = datetime.now(tz=UTC) - timedelta(days=1)
    forecast = _stored_forecast(plat_scope, points([10, 10], start=past))
    db.add(forecast)
    await db.commit()
    with pytest.raises(Conflict) as caught:
        await accuracy.evaluate_forecast(db, forecast)
    assert caught.value.detail["scored_intervals"] == 0


async def test_a_forecast_without_points_cannot_be_scored(
    db: AsyncSession, plat_scope: PlatScope
):
    forecast = _stored_forecast(plat_scope, [])
    db.add(forecast)
    await db.commit()
    with pytest.raises(ValidationFailed):
        await accuracy.evaluate_forecast(db, forecast)


async def test_rescoring_the_same_window_updates_rather_than_duplicates(
    db: AsyncSession, plat_scope: PlatScope
):
    past = datetime.now(tz=UTC) - timedelta(days=1)
    forecast = _stored_forecast(plat_scope, points([10, 10], start=past))
    db.add(forecast)
    for offset in range(2):
        db.add(_stored_actual(plat_scope, past + timedelta(minutes=INTERVAL * offset), 12))
    await db.commit()
    first = await accuracy.evaluate_forecast(db, forecast)
    second = await accuracy.evaluate_forecast(db, forecast)
    assert first.id == second.id
    rows = (await db.execute(WfmForecastAccuracy.__table__.select())).mappings().all()
    assert len(rows) == 1


async def test_the_latest_scores_come_back_newest_first(
    db: AsyncSession, plat_scope: PlatScope
):
    past = datetime.now(tz=UTC) - timedelta(days=2)
    for index in range(3):
        forecast = _stored_forecast(
            plat_scope, points([10, 10], start=past + timedelta(hours=index))
        )
        db.add(forecast)
        for offset in range(2):
            db.add(
                _stored_actual(
                    plat_scope,
                    past + timedelta(hours=index, minutes=INTERVAL * offset),
                    12,
                )
            )
    await db.commit()
    stored = (
        await db.execute(
            WfmForecast.__table__.select().order_by(WfmForecast.points)
        )
    ).mappings().all()
    for candidate in (
        await db.execute(WfmForecast.__table__.select())
    ).mappings().all():
        row = await db.get(WfmForecast, candidate["id"])
        await accuracy.evaluate_forecast(db, row)
    latest = await accuracy.latest_accuracy(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key="",
        limit=2,
    )
    assert len(latest) == 2
    assert latest[0].computed_at >= latest[1].computed_at
    assert stored  # the forecasts were really persisted
