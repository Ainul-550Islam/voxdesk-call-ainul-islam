"""Forecasting: does the model learn the pattern it was shown?

Every test builds a history with a *known* shape (a weekday/weekend ratio and a
09:00 spike) and asserts the fit reproduces it. A forecast test that only checks
"it returned numbers" cannot tell a working model from a constant.
"""

from __future__ import annotations

import statistics
from datetime import UTC, date, datetime, timedelta

import pytest

from app.platform_v2.errors import InsufficientHistory, ValidationFailed
from app.platform_v2.wfm import forecast, history

ZONE = "Asia/Dhaka"

#: A 24-hour queue: weekdays are twice the weekend, and 09:00 local is the spike.
#: The ratios are deliberately inside the model's [0.2, 5.0] factor clamp, so a test
#: about the *model* is never really a test about the clamp.
WEEKDAY_FACTOR = 2.0
WEEKEND_FACTOR = 0.8
SPIKE_MULTIPLIER = 1.5
SPIKE_HOUR = 9
BASE_VOLUME = 20.0

START = date(2026, 9, 1)


def make_samples(
    days: int = 28,
    *,
    interval_minutes: int = 30,
    zone_name: str = ZONE,
    open_hours: tuple[int, ...] = tuple(range(24)),
):
    """Deterministic ``(slot, volume)`` history with the documented shape."""
    grid = history.interval_grid(zone_name, START, START + timedelta(days=days - 1), interval_minutes)
    zone = history.tenant_zone(zone_name)
    samples = []
    for slot in grid:
        local = slot.start.astimezone(zone)
        if local.hour not in open_hours:
            volume = 0.0
        else:
            day_factor = WEEKEND_FACTOR if local.weekday() >= 5 else WEEKDAY_FACTOR
            spike = SPIKE_MULTIPLIER if local.hour == SPIKE_HOUR else 1.0
            volume = BASE_VOLUME * day_factor * spike
        samples.append((slot, volume))
    return samples


def closed_night_samples(days: int = 28):
    """The same queue, but shut between 19:00 and 07:00 local."""
    return make_samples(days, open_hours=tuple(range(7, 19)))


def test_the_fit_learns_the_weekday_weekend_ratio():
    profile = forecast.fit_profile(make_samples(), zone_name=ZONE)
    weekday = statistics.fmean(
        float(profile["weekday_factor"][key]) for key in ("0", "1", "2", "3", "4")
    )
    weekend = statistics.fmean(float(profile["weekday_factor"][key]) for key in ("5", "6"))
    assert profile["model"] == "multiplicative"
    # Factors are ratios to the overall interval mean, so the *ratio between them*
    # is what recovers the shape that was generated.
    assert weekday / weekend == pytest.approx(WEEKDAY_FACTOR / WEEKEND_FACTOR, rel=0.02)
    assert weekday > 1.0 > weekend


def test_a_closed_hour_has_a_zero_factor_and_is_not_clamped_up():
    profile = forecast.fit_profile(closed_night_samples(), zone_name=ZONE)
    # Clamping a closed slot to the 0.2 floor would staff a queue that shuts at 19:00.
    assert profile["slot_factor"]["03:00"] == 0.0
    assert profile["slot_factor"]["23:00"] == 0.0
    # ... while an open hour keeps a real factor.
    assert profile["slot_factor"]["09:00"] > profile["slot_factor"]["12:00"] > 0.0


def test_the_spike_is_learned_in_local_time_not_utc():
    """Keying the profile by UTC hour would put Dhaka's 09:00 spike at 03:00."""
    profile = forecast.fit_profile(make_samples(), zone_name=ZONE)
    factors = {key: float(value) for key, value in profile["slot_factor"].items()}
    assert max(factors, key=factors.get) == f"{SPIKE_HOUR:02d}:00"
    assert factors[f"{SPIKE_HOUR:02d}:00"] / factors["10:00"] == pytest.approx(
        SPIKE_MULTIPLIER, rel=1e-6
    )


def test_a_forecast_covers_the_horizon_on_the_local_grid():
    samples = make_samples()
    result = forecast.forecast_intervals(
        samples,
        zone_name=ZONE,
        start_date=START + timedelta(days=28),
        horizon_days=7,
        interval_minutes=30,
    )
    assert result.horizon_days == 7
    assert result.method == forecast.METHOD_SEASONAL
    zone = history.tenant_zone(ZONE)
    days = {point.interval_start.astimezone(zone).date() for point in result.points}
    assert days == {START + timedelta(days=28 + offset) for offset in range(7)}
    # 30-minute intervals over 24 hours.
    assert len(result.points) == 7 * 48
    assert all(point.interval_minutes == 30 for point in result.points)


def test_the_point_grid_follows_the_requested_interval_not_the_history():
    samples = make_samples(interval_minutes=30)
    result = forecast.forecast_intervals(
        samples,
        zone_name=ZONE,
        start_date=START + timedelta(days=28),
        horizon_days=1,
        interval_minutes=15,
    )
    # Four points an hour, because that is what was asked for. The history's 30
    # minute granularity is resampled upstream by `jobs.samples_for`, never by
    # relabelling a 30-minute bucket as a 15-minute one.
    assert len(result.points) == 96
    assert all(point.interval_minutes == 15 for point in result.points)
    # A local slot the history never covered is closed demand, not a guess.
    quarter = [point for point in result.points if point.interval_start.astimezone(history.tenant_zone(ZONE)).minute == 15]
    assert all(point.expected == 0.0 for point in quarter)


def test_the_forecast_reproduces_the_history_it_was_fitted_to():
    samples = make_samples()
    profile = forecast.fit_profile(samples, zone_name=ZONE)
    zone = history.tenant_zone(ZONE)
    history_by_slot = {}
    for slot, volume in samples:
        local = slot.start.astimezone(zone)
        history_by_slot[(local.weekday(), local.hour, local.minute)] = volume

    result = forecast.forecast_intervals(
        samples,
        zone_name=ZONE,
        start_date=START + timedelta(days=28),
        horizon_days=7,
        interval_minutes=30,
        profile=profile,
    )
    errors = []
    for point in result.points:
        local = point.interval_start.astimezone(zone)
        actual = history_by_slot[(local.weekday(), local.hour, local.minute)]
        errors.append(abs(point.expected - actual))
    # A multiplicative model on noise-free data should be almost exact.
    assert max(errors) < 1.0, f"largest absolute error was {max(errors):.3f}"


def test_the_band_widens_with_the_horizon_and_never_inverts():
    result = forecast.forecast_intervals(
        make_samples(), zone_name=ZONE, start_date=START + timedelta(days=28), horizon_days=14
    )
    by_day: dict[date, list[forecast.ForecastPoint]] = {}
    zone = history.tenant_zone(ZONE)
    for point in result.points:
        by_day.setdefault(point.interval_start.astimezone(zone).date(), []).append(point)

    widths = [
        statistics.fmean((p.high - p.low) / p.expected for p in points if p.expected > 0)
        for _day, points in sorted(by_day.items())
    ]
    assert all(later >= earlier for earlier, later in zip(widths, widths[1:]))
    assert all(point.low <= point.expected <= point.high for point in result.points)


def test_a_day_override_multiplies_only_that_day():
    samples = make_samples()
    target_day = START + timedelta(days=28)
    plain = forecast.forecast_intervals(
        samples, zone_name=ZONE, start_date=target_day, horizon_days=2
    )
    boosted = forecast.forecast_intervals(
        samples,
        zone_name=ZONE,
        start_date=target_day,
        horizon_days=2,
        overrides={target_day.isoformat(): {"factor": 1.5, "label": "campaign"}},
    )
    zone = history.tenant_zone(ZONE)

    def total(result, day):
        return sum(
            point.expected
            for point in result.points
            if point.interval_start.astimezone(zone).date() == day
        )

    assert total(boosted, target_day) == pytest.approx(total(plain, target_day) * 1.5, rel=1e-6)
    assert total(boosted, target_day + timedelta(days=1)) == pytest.approx(
        total(plain, target_day + timedelta(days=1)), rel=1e-6
    )
    assert boosted.overrides[target_day.isoformat()]["label"] == "campaign"


def test_thin_history_produces_an_additive_model_with_a_wider_band():
    thin = make_samples(days=7)
    profile = forecast.fit_profile(thin, zone_name=ZONE)
    assert profile["model"] == "additive"
    result = forecast.forecast_intervals(
        thin, zone_name=ZONE, start_date=START + timedelta(days=7), horizon_days=1
    )
    assert result.method == forecast.METHOD_ADDITIVE
    # Additive drops the weekday factor entirely: with one week there is nothing to
    # compare a Tuesday against.
    point = next(p for p in result.points if p.expected > 0)
    assert point.low < point.expected < point.high


def test_two_days_of_history_is_refused_rather_than_fitted():
    with pytest.raises(InsufficientHistory) as caught:
        forecast.forecast_intervals(
            make_samples(days=2), zone_name=ZONE, start_date=START, horizon_days=1
        )
    assert caught.value.detail["minimum"] == forecast.MIN_DAYS_ADDITIVE


def test_no_history_at_all_is_insufficient_not_zero():
    with pytest.raises(InsufficientHistory):
        forecast.fit_profile([], zone_name=ZONE)


def test_an_all_zero_history_is_insufficient_not_a_zero_forecast():
    grid = history.interval_grid(ZONE, START, START + timedelta(days=3), 30)
    samples = [(slot, 0.0) for slot in grid]
    with pytest.raises(InsufficientHistory):
        forecast.fit_profile(samples, zone_name=ZONE)


def test_horizon_and_interval_bounds_are_enforced():
    samples = make_samples()
    with pytest.raises(ValidationFailed):
        forecast.forecast_intervals(samples, zone_name=ZONE, start_date=START, horizon_days=0)
    with pytest.raises(ValidationFailed):
        forecast.forecast_intervals(samples, zone_name=ZONE, start_date=START, horizon_days=400)
    with pytest.raises(ValidationFailed) as caught:
        forecast.forecast_intervals(
            samples, zone_name=ZONE, start_date=START, horizon_days=7, interval_minutes=7
        )
    assert caught.value.detail["interval_minutes"] == 7


def test_an_unknown_timezone_is_refused_not_defaulted_to_utc():
    with pytest.raises(ValidationFailed):
        forecast.fit_profile(make_samples(), zone_name="Mars/Olympus")


def test_the_stored_payload_round_trips():
    result = forecast.forecast_intervals(
        make_samples(), zone_name=ZONE, start_date=START + timedelta(days=28), horizon_days=2
    )
    payload = [point.as_dict() for point in result.points]
    restored = forecast.points_from_payload(payload)
    assert len(restored) == len(result.points)
    assert restored[0].interval_start == result.points[0].interval_start
    assert restored[0].expected == pytest.approx(result.points[0].expected)
    assert restored[-1].interval_start.tzinfo is not None


def test_the_summary_reports_totals_that_match_the_points():
    result = forecast.forecast_intervals(
        make_samples(), zone_name=ZONE, start_date=START + timedelta(days=28), horizon_days=3
    )
    summary = forecast.summarise(result)
    assert summary["intervals"] == len(result.points)
    assert summary["volume"] == pytest.approx(
        sum(point.expected for point in result.points), rel=1e-6
    )
    assert summary["peak_volume"] == pytest.approx(
        max(point.expected for point in result.points), rel=1e-6
    )
    # The band is reported as [low total, high total], never as a single number.
    low, high = summary["band"]
    assert low <= summary["volume"] <= high
    assert set(summary["daily"]) == {
        (START + timedelta(days=28 + offset)).isoformat() for offset in range(3)
    }


def test_a_poisson_band_is_never_negative():
    low, high = forecast.poisson_band(0.0)
    assert low == 0.0 and high >= 0.0
    low, high = forecast.poisson_band(40.0)
    assert 0 < low < 40 < high


def test_interval_key_is_the_local_clock_time():
    moment = datetime(2026, 11, 3, 3, 30, tzinfo=UTC)  # 09:30 in Dhaka, a Tuesday
    assert forecast.interval_key(moment, zone_name=ZONE) == (1, 9 * 60 + 30)
    # Without a zone the moment's own clock is used — not a shifted UTC one.
    assert forecast.interval_key(moment) == (1, 3 * 60 + 30)
