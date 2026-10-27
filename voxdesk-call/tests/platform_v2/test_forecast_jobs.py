"""The WFM job bodies: rollup, forecast generation, scoring, history counts.

These are the functions the scheduler ticks call, so they are also the ones an
operator sees the output of. The tests keep the promises that matter:

* the forecaster never sees a gap bucket (a bucket nobody covered would teach it
  that quiet days are demand);
* storage keeps the shape ``points_from_payload`` can read back, so a forecast is
  re-plannable after a restart;
* a forecast is scored once, and only once something has actually elapsed;
* history older than the retention horizon is removable, and nothing younger is.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserRole
from app.db.platform_v2_models import WfmForecast, WfmForecastAccuracy, WfmIntervalHistory
from app.platform_v2.errors import NotFound
from app.platform_v2.wfm import forecast as forecast_module
from app.platform_v2.settings import get_plat2_settings
from app.platform_v2.wfm import history, jobs
from tests.conftest import make_user
from tests.platform_v2.conftest import PlatScope

#: The grid zone is the deployment setting; nothing here hard-codes a timezone.
ZONE = get_plat2_settings().wfm_timezone
TODAY = datetime.now(tz=UTC).date()


def moment(day_offset: int, hour: int, minute: int = 0) -> datetime:
    """A UTC instant on a local Dhaka day, ``day_offset`` days from today."""
    local = datetime.combine(TODAY + timedelta(days=day_offset), datetime.min.time())
    local = local.replace(hour=hour, minute=minute, tzinfo=history.tenant_zone(ZONE))
    return local.astimezone(UTC)


def bucket(
    db: AsyncSession,
    scope: PlatScope,
    *,
    start: datetime,
    offered: float = 10.0,
    is_gap: bool = False,
    queue_key: str = "",
    minutes: int = 30,
) -> WfmIntervalHistory:
    row = WfmIntervalHistory(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        queue_key=queue_key,
        interval_start=start,
        interval_minutes=minutes,
        timezone=ZONE,
        offered=offered,
        handled=offered,
        abandoned=0,
        is_gap=is_gap,
    )
    db.add(row)
    return row


def seed_day(
    db: AsyncSession,
    scope: PlatScope,
    *,
    day_offset: int,
    offered: float,
    hours: range = range(8, 20),
    queue_key: str = "",
    minutes: int = 30,
) -> list[WfmIntervalHistory]:
    rows = []
    for hour in hours:
        for minute in range(0, 60, minutes):
            rows.append(
                bucket(
                    db,
                    scope,
                    start=moment(day_offset, hour, minute),
                    offered=offered,
                    queue_key=queue_key,
                    minutes=minutes,
                )
            )
    return rows


def seed_week(db: AsyncSession, scope: PlatScope, *, days: int = 21, offered: float = 12.0):
    for offset in range(-days, 0):
        seed_day(db, scope, day_offset=offset, offered=offered)


# --- tenant_zone_name ----------------------------------------------------------


async def test_the_zone_comes_from_settings(db: AsyncSession, plat_scope: PlatScope):
    name = await jobs.tenant_zone_name(db, tenant_id=plat_scope.tenant.id)
    assert name == get_plat2_settings().wfm_timezone, (
        "WFM_TIMEZONE is the single source for the interval grid"
    )


# --- samples_for ---------------------------------------------------------------


async def test_samples_are_pairs_of_slots_and_volume(db: AsyncSession, plat_scope: PlatScope):
    seed_day(db, plat_scope, day_offset=-1, offered=20.0)
    await db.commit()
    samples = await jobs.samples_for(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key="",
        zone_name=ZONE,
        interval_minutes=30,
        lookback_days=7,
    )
    assert samples, "a full previous day of history must produce samples"
    slot, volume = samples[0]
    assert slot.minutes == 30
    assert isinstance(volume, float)
    assert sum(volume for _slot, volume in samples) == pytest.approx(20.0 * 24)


async def test_gaps_are_never_shown_to_the_forecaster(db: AsyncSession, plat_scope: PlatScope):
    seed_day(db, plat_scope, day_offset=-1, offered=20.0, hours=range(8, 9))
    gap = bucket(db, plat_scope, start=moment(-1, 9), offered=0.0, is_gap=True)
    await db.commit()
    assert gap.is_gap
    samples = await jobs.samples_for(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key="",
        zone_name=ZONE,
        interval_minutes=30,
        lookback_days=7,
    )
    total = sum(volume for _slot, volume in samples)
    assert total == pytest.approx(40.0), "only the two 30-minute buckets that have volume"


async def test_a_finer_interval_splits_volume_instead_of_dropping_it(
    db: AsyncSession, plat_scope: PlatScope
):
    """A 30-minute row of 40 calls becomes 20 + 20 at 15 minutes, not 40 + 0."""
    bucket(db, plat_scope, start=moment(-1, 9), offered=40.0, minutes=30)
    await db.commit()
    samples = await jobs.samples_for(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key="",
        zone_name=ZONE,
        interval_minutes=15,
        lookback_days=7,
    )
    by_start = {slot.start: volume for slot, volume in samples}
    assert by_start[moment(-1, 9)] == pytest.approx(20.0)
    assert by_start[moment(-1, 9, 15)] == pytest.approx(20.0)
    assert by_start[moment(-1, 9, 30)] == pytest.approx(0.0)
    assert sum(by_start.values()) == pytest.approx(40.0)


async def test_samples_are_per_queue(db: AsyncSession, plat_scope: PlatScope):
    bucket(db, plat_scope, start=moment(-1, 9), offered=10.0, queue_key="support")
    bucket(db, plat_scope, start=moment(-1, 9), offered=7.0, queue_key="sales")
    await db.commit()
    support = await jobs.samples_for(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key="support",
        zone_name=ZONE,
        interval_minutes=30,
        lookback_days=7,
    )
    assert sum(volume for _slot, volume in support) == pytest.approx(10.0)


async def test_no_history_means_no_samples(db: AsyncSession, plat_scope: PlatScope):
    samples = await jobs.samples_for(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key="",
        zone_name=ZONE,
        interval_minutes=30,
        lookback_days=7,
    )
    assert samples, "the grid is returned whole"
    assert {volume for _slot, volume in samples} == {0.0}, "and every slot is honestly zero"


# --- generate_forecast ---------------------------------------------------------


async def test_a_forecast_is_stored_in_a_shape_it_can_be_read_back_from(
    db: AsyncSession, plat_scope: PlatScope
):
    seed_week(db, plat_scope)
    await db.commit()
    row = await jobs.generate_forecast(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        horizon_days=2,
        interval_minutes=30,
        start_date=TODAY + timedelta(days=1),
    )
    assert row.horizon_days == 2
    assert row.timezone == ZONE
    assert row.interval_minutes == 30
    assert len(row.points) == 2 * 48
    points = forecast_module.points_from_payload(row.points)
    assert len(points) == len(row.points)
    assert points[0].interval_start == datetime.fromisoformat(row.points[0]["interval_start"])
    assert all(point.expected >= 0 for point in points)
    assert all(point.low <= point.expected <= point.high for point in points)


async def test_the_stored_horizon_starts_on_the_requested_local_day(
    db: AsyncSession, plat_scope: PlatScope
):
    seed_week(db, plat_scope)
    await db.commit()
    start = TODAY + timedelta(days=1)
    row = await jobs.generate_forecast(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        horizon_days=1,
        interval_minutes=30,
        start_date=start,
    )
    stamps = [
        datetime.fromisoformat(point["interval_start"]).astimezone(history.tenant_zone(ZONE))
        for point in row.points
    ]
    assert len(stamps) == 48
    assert {stamp.date() for stamp in stamps} == {start}
    assert all(later - earlier == timedelta(minutes=30) for earlier, later in zip(stamps, stamps[1:]))


async def test_a_forecast_without_history_is_refused(db: AsyncSession, plat_scope: PlatScope):
    """The honest failure: the console turns this into "come back in two weeks"."""
    with pytest.raises(Exception) as caught:
        await jobs.generate_forecast(
            db,
            tenant_id=plat_scope.tenant.id,
            environment_id=plat_scope.environment.id,
            horizon_days=1,
            interval_minutes=30,
        )
    assert "istory" in str(caught.value) or "insufficient" in str(caught.value).lower()


async def test_generation_records_who_asked(db: AsyncSession, plat_scope: PlatScope):
    seed_week(db, plat_scope)
    manager = await make_user(
        db, plat_scope.tenant, UserRole.MANAGER, email="fc-author@example.com"
    )
    await db.commit()
    row = await jobs.generate_forecast(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        horizon_days=1,
        interval_minutes=30,
        created_by_user_id=manager.id,
    )
    assert row.created_by_user_id == manager.id
    assert row.method and row.model_version


async def test_the_horizon_is_capped_by_settings(db: AsyncSession, plat_scope: PlatScope):
    seed_week(db, plat_scope)
    await db.commit()
    row = await jobs.generate_forecast(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        interval_minutes=30,
    )
    assert row.horizon_days <= 14


# --- forecast_or_404 -----------------------------------------------------------


async def test_a_forecast_from_another_tenant_is_not_found(
    db: AsyncSession, plat_scope: PlatScope, tenant_b
):
    seed_week(db, plat_scope)
    await db.commit()
    row = await jobs.generate_forecast(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        horizon_days=1,
        interval_minutes=30,
    )
    found = await jobs.forecast_or_404(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        forecast_id=row.id,
    )
    assert found.id == row.id
    with pytest.raises(NotFound):
        await jobs.forecast_or_404(
            db,
            tenant_id=tenant_b.id,
            environment_id=plat_scope.environment.id,
            forecast_id=row.id,
        )


# --- scoring -------------------------------------------------------------------


async def _forecast_covering_yesterday(db: AsyncSession, scope: PlatScope) -> WfmForecast:
    """A stored forecast whose horizon contains yesterday, with flat 10-call halves."""
    start = TODAY - timedelta(days=2)
    points = []
    for day_index in range(3):
        day = start + timedelta(days=day_index)
        for slot in range(48):
            local = datetime.combine(day, datetime.min.time()).replace(
                minute=(slot % 2) * 30, tzinfo=history.tenant_zone(ZONE)
            ).replace(hour=slot // 2)
            points.append(
                forecast_module.ForecastPoint(
                    interval_start=local.astimezone(UTC),
                    interval_minutes=30,
                    expected=10.0,
                    low=8.0,
                    high=12.0,
                )
            )
    row = WfmForecast(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        queue_key="",
        timezone=ZONE,
        interval_minutes=30,
        horizon_days=3,
        history_days=21,
        method="seasonal_interval",
        model_version="v1",
        points=[point.as_dict() for point in points],
        generated_at=datetime.now(tz=UTC) - timedelta(days=2, hours=1),
    )
    db.add(row)
    await db.commit()
    return row


async def test_scoring_measures_the_forecast_against_stored_actuals(
    db: AsyncSession, plat_scope: PlatScope
):
    stored = await _forecast_covering_yesterday(db, plat_scope)
    seed_day(db, plat_scope, day_offset=-1, offered=12.0)
    await db.commit()
    outcome = await jobs.score_due_forecasts(
        db, tenant_id=plat_scope.tenant.id, environment_id=plat_scope.environment.id
    )
    assert [item["forecast_id"] for item in outcome["scored"]] == [str(stored.id)]
    assert outcome["scored"][0]["sample_count"] == 24
    # 10 forecast against 12 actual everywhere: WAPE is |10-12|/12 = 1/6.
    # WAPE lands in a NUMERIC(8, 4) column, so it is rounded on the way out.
    assert outcome["scored"][0]["wape"] == pytest.approx(1 / 6, abs=1e-3)


async def test_a_second_score_run_does_not_duplicate_the_metric(
    db: AsyncSession, plat_scope: PlatScope
):
    stored = await _forecast_covering_yesterday(db, plat_scope)
    seed_day(db, plat_scope, day_offset=-1, offered=12.0)
    await db.commit()
    await jobs.score_due_forecasts(
        db, tenant_id=plat_scope.tenant.id, environment_id=plat_scope.environment.id
    )
    second = await jobs.score_due_forecasts(
        db, tenant_id=plat_scope.tenant.id, environment_id=plat_scope.environment.id
    )
    assert second["scored"] == []
    metrics = (
        (
            await db.execute(
                WfmForecastAccuracy.__table__.select().where(
                    WfmForecastAccuracy.forecast_id == stored.id
                )
            )
        )
        .mappings()
        .all()
    )
    assert len(metrics) == 1


async def test_a_forecast_that_has_not_elapsed_yet_is_skipped_not_scored(
    db: AsyncSession, plat_scope: PlatScope
):
    start = TODAY + timedelta(days=1)
    points = [
        forecast_module.ForecastPoint(
            interval_start=datetime.combine(start, datetime.min.time()).replace(tzinfo=UTC)
            + timedelta(minutes=30 * index),
            interval_minutes=30,
            expected=10.0,
            low=8.0,
            high=12.0,
        ).as_dict()
        for index in range(4)
    ]
    db.add(
        WfmForecast(
            tenant_id=plat_scope.tenant.id,
            environment_id=plat_scope.environment.id,
            queue_key="",
            timezone=ZONE,
            interval_minutes=30,
            horizon_days=1,
            history_days=21,
            points=points,
            generated_at=datetime.now(tz=UTC),
        )
    )
    await db.commit()
    outcome = await jobs.score_due_forecasts(
        db, tenant_id=plat_scope.tenant.id, environment_id=plat_scope.environment.id
    )
    assert outcome["scored"] == []
    assert len(outcome["skipped"]) == 1


async def test_scoring_can_be_limited_to_one_queue(db: AsyncSession, plat_scope: PlatScope):
    stored = await _forecast_covering_yesterday(db, plat_scope)
    await db.commit()
    outcome = await jobs.score_due_forecasts(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        queue_key="nothing-here",
    )
    assert outcome["scored"] == []
    assert stored.queue_key == ""


# --- history_counts ------------------------------------------------------------


async def test_history_counts_reports_how_much_data_there_is(
    db: AsyncSession, plat_scope: PlatScope
):
    seed_day(db, plat_scope, day_offset=-1, offered=5.0, hours=range(8, 10))
    seed_day(db, plat_scope, day_offset=-2, offered=5.0, hours=range(8, 10))
    await db.commit()
    counts = await jobs.history_counts(
        db, tenant_id=plat_scope.tenant.id, environment_id=plat_scope.environment.id
    )
    assert counts["intervals"] == 8
    assert counts["days"] == 2
    assert counts["enough"] is False
    assert counts["required_days"] >= 1
    assert counts["workday_minutes"] == 480
    assert counts["first"] and counts["last"]


async def test_gap_buckets_do_not_count_towards_history(
    db: AsyncSession, plat_scope: PlatScope
):
    seed_day(db, plat_scope, day_offset=-1, offered=5.0, hours=range(8, 9))
    bucket(db, plat_scope, start=moment(-2, 9), offered=0.0, is_gap=True)
    await db.commit()
    counts = await jobs.history_counts(
        db, tenant_id=plat_scope.tenant.id, environment_id=plat_scope.environment.id
    )
    assert counts["intervals"] == 2
    assert counts["days"] == 1, "a gap-only day is not a day of history"


async def test_empty_history_reports_zero_but_is_not_an_error(
    db: AsyncSession, plat_scope: PlatScope
):
    counts = await jobs.history_counts(
        db, tenant_id=plat_scope.tenant.id, environment_id=plat_scope.environment.id
    )
    assert counts == {
        "intervals": 0,
        "days": 0,
        "first": None,
        "last": None,
        "enough": False,
    }


# --- retention -----------------------------------------------------------------


async def test_retention_removes_only_what_is_older_than_the_horizon(
    db: AsyncSession, plat_scope: PlatScope
):
    bucket(db, plat_scope, start=datetime.now(tz=UTC) - timedelta(days=500), offered=1.0)
    bucket(db, plat_scope, start=datetime.now(tz=UTC) - timedelta(days=1), offered=1.0)
    await db.commit()
    removed = await jobs.purge_stale_history(
        db, tenant_id=plat_scope.tenant.id, older_than_days=400
    )
    assert removed == 1
    left = (await db.execute(WfmIntervalHistory.__table__.select())).mappings().all()
    assert len(left) == 1


async def test_retention_has_a_floor_so_a_typo_cannot_wipe_the_history(
    db: AsyncSession, plat_scope: PlatScope
):
    recent = bucket(
        db, plat_scope, start=datetime.now(tz=UTC) - timedelta(days=20), offered=1.0
    )
    await db.commit()
    removed = await jobs.purge_stale_history(
        db, tenant_id=plat_scope.tenant.id, older_than_days=0
    )
    assert removed == 0, "``older_than_days=0`` means the 30-day floor, not everything"
    assert await db.get(WfmIntervalHistory, recent.id) is not None


# --- rollup --------------------------------------------------------------------


async def test_rollup_rebuilds_history_from_telephony_rows(
    db: AsyncSession, plat_scope: PlatScope
):
    outcome = await jobs.rollup(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        days=1,
        end_date=TODAY - timedelta(days=1),
        interval_minutes=30,
    )
    assert outcome["start"] == (TODAY - timedelta(days=1)).isoformat()
    assert outcome["zone"] == ZONE
    assert outcome["interval_minutes"] == 30
    # No queues are configured, so the only result is the environment-wide total —
    # and with no traffic every bucket in it is recorded as a gap, not as zero demand.
    assert outcome["queues"] == 1
    # ``days=1`` is one local day, and there is one gap bucket per half hour.
    assert outcome["written"] == 48
    assert outcome["gaps"] == outcome["written"]


async def test_rollup_is_idempotent(db: AsyncSession, plat_scope: PlatScope):
    kwargs = dict(days=2, end_date=TODAY - timedelta(days=1), interval_minutes=30)
    first = await jobs.rollup(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        **kwargs,
    )
    second = await jobs.rollup(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        **kwargs,
    )
    assert first["written"] == 2 * 48
    assert second["written"] == 0, "the second pass updates the same buckets"
    assert second["updated"] == 2 * 48
