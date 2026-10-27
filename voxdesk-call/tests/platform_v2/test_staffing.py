"""Staffing: Erlang C per interval, shrinkage, smoothing and the totals that follow.

The assertions are arithmetic, not smoke tests: an interval's productive headcount is
checked against an independent Erlang calculation, and paid headcount against the
shrinkage multiplier.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from app.platform_v2.errors import ValidationFailed
from app.platform_v2.wfm import erlang, staffing
from app.platform_v2.wfm.history import HistorySummary

START = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)


def point(volume: float, *, minutes: int = 15, index: int = 0) -> object:
    from app.platform_v2.wfm.forecast import ForecastPoint

    return ForecastPoint(
        interval_start=START + timedelta(minutes=minutes * index),
        interval_minutes=minutes,
        expected=volume,
        low=volume * 0.8,
        high=volume * 1.2,
    )


def test_the_target_names_the_model_it_is_buying():
    target = staffing.StaffingTarget(service_level=0.9, answer_seconds=15, shrinkage=0.25)
    target.validate()
    body = target.as_dict()
    assert body["model"] == "erlang_c"
    assert body["paid_multiplier"] == pytest.approx(1 / 0.75, rel=1e-4)


@pytest.mark.parametrize(
    "overrides",
    [
        {"service_level": 0.0},
        {"service_level": 1.5},
        {"answer_seconds": -1},
        {"max_occupancy": 0.0},
        {"shrinkage": 1.0},
        {"average_handle_seconds": 0},
        {"model": "erlang_d"},
        {"average_patience_seconds": 0},
        {"minimum_agents": -1},
    ],
)
def test_impossible_targets_are_refused(overrides):
    with pytest.raises(ValidationFailed):
        staffing.StaffingTarget(**overrides).validate()


def test_an_interval_is_staffed_to_the_same_number_erlang_calculates():
    target = staffing.StaffingTarget(service_level=0.8, answer_seconds=20, average_handle_seconds=240)
    requirement = staffing.requirement_for_point(point(40), target)
    reference = erlang.required_agents(
        interval_volume=40,
        average_handle_seconds=240,
        interval_seconds=900,
        target_service_level=0.8,
        target_answer_seconds=20,
        max_occupancy=0.85,
    )
    assert requirement.productive_agents == reference.agents
    assert requirement.offered_load == pytest.approx(reference.offered_load)
    assert requirement.service_level >= 0.8
    assert requirement.occupancy <= 0.85 + 1e-9


def test_paid_headcount_covers_shrinkage_and_never_rounds_down():
    target = staffing.StaffingTarget(shrinkage=0.4)
    requirement = staffing.requirement_for_point(point(60), target)
    assert requirement.paid_agents == math.ceil(requirement.productive_agents / 0.6)
    assert requirement.paid_agents >= requirement.productive_agents
    # 1 productive agent at 40% shrinkage is 1.67 paid people, which is 2 — not 1.
    assert staffing._paid_for(1, 0.4) == 2
    assert staffing._paid_for(0, 0.4) == 0


def test_a_closed_interval_needs_only_the_configured_minimum():
    target = staffing.StaffingTarget(minimum_agents=2, shrinkage=0.5)
    requirement = staffing.requirement_for_point(point(0), target)
    assert requirement.productive_agents == 2
    assert requirement.paid_agents == 4
    assert requirement.service_level == 1.0
    assert requirement.offered_load == 0.0


def test_a_nonzero_interval_never_staffs_zero_productively():
    target = staffing.StaffingTarget(service_level=0.5, answer_seconds=300)
    requirement = staffing.requirement_for_point(point(1), target)
    assert requirement.productive_agents >= staffing.MIN_PRODUCTIVE_AGENTS


def test_the_plan_carries_hours_that_match_its_curve():
    built = [point(40, index=index) for index in range(4)]
    forecast_like = type(
        "F",
        (),
        {"points": built, "timezone": "UTC", "interval_minutes": 15},
    )()
    plan = staffing.plan_from_forecast(forecast_like, staffing.StaffingTarget(), band="expected")
    assert plan.band == "expected"
    expected_paid = sum(item.paid_agents * 15 / 60 for item in plan.requirements)
    assert plan.paid_hours == pytest.approx(expected_paid)
    assert plan.productive_hours <= plan.paid_hours
    assert plan.peak is not None and plan.peak.paid_agents == max(
        item.paid_agents for item in plan.requirements
    )
    body = plan.as_dict(include_intervals=True)
    assert body["intervals"] == 4
    assert body["peak_paid_agents"] == plan.peak.paid_agents
    assert len(body["curve"]) == 4
    assert set(body["target"]) == {
        "service_level",
        "answer_seconds",
        "max_occupancy",
        "shrinkage",
        "average_handle_seconds",
        "model",
        "average_patience_seconds",
        "minimum_agents",
        "paid_multiplier",
    }


def test_staffing_to_a_higher_band_needs_at_least_as_many_people():
    built = [point(40, index=index) for index in range(6)]
    forecast_like = type("F", (), {"points": built, "timezone": "UTC", "interval_minutes": 15})()
    low = staffing.plan_from_forecast(forecast_like, staffing.StaffingTarget(), band="low")
    high = staffing.plan_from_forecast(forecast_like, staffing.StaffingTarget(), band="high")
    assert high.paid_hours >= low.paid_hours
    with pytest.raises(ValidationFailed):
        staffing.plan_from_forecast(forecast_like, staffing.StaffingTarget(), band="p50")


def test_the_daily_rollup_adds_up_to_the_plan():
    built = [
        point(40, index=index, minutes=15) for index in range(4)
    ] + [
        point(20, index=100 + index, minutes=15)  # the next day
        for index in range(4)
    ]
    forecast_like = type("F", (), {"points": built, "timezone": "UTC", "interval_minutes": 15})()
    plan = staffing.plan_from_forecast(forecast_like, staffing.StaffingTarget())
    daily = plan.daily()
    assert len(daily) == 2
    assert sum(day["paid_hours"] for day in daily.values()) == pytest.approx(plan.paid_hours)
    assert sum(day["volume"] for day in daily.values()) == pytest.approx(
        sum(item.volume for item in plan.requirements)
    )


def test_the_distribution_reports_order_statistics_not_a_mean_only():
    requirements = [
        staffing.requirement_for_point(point(volume, index=index), staffing.StaffingTarget())
        for index, volume in enumerate((0, 10, 20, 30, 60, 120))
    ]
    body = staffing.distribution(requirements)
    assert set(body) == {"min", "median", "mean", "max", "p90"}
    assert body["min"] <= body["median"] <= body["max"]
    assert body["p90"] >= body["median"]
    assert staffing.distribution([]) == {"min": 0.0, "median": 0.0, "mean": 0.0, "max": 0.0, "p90": 0.0}


def test_smoothing_is_a_rolling_maximum_so_a_dip_is_never_understaffed():
    requirements = [
        staffing.requirement_for_point(point(volume, index=index), staffing.StaffingTarget())
        for index, volume in enumerate((0, 0, 80, 0, 0, 0, 0, 0))
    ]
    smoothed = staffing.smooth(requirements)
    assert len(smoothed) == len(requirements)
    # Rosters are built from shifts, so the neighbours of a spike are staffed at the
    # spike's level rather than at their own dip.
    assert max(item.paid_agents for item in smoothed) == max(
        item.paid_agents for item in requirements
    )
    assert all(
        later.paid_agents >= earlier.paid_agents
        for earlier, later in zip(smoothed[2:3], smoothed[3:4])
    )


def test_service_level_of_measures_the_rota_against_the_requirement():
    covered = staffing.service_level_of([2, 3, 1], [2, 3, 4])
    assert covered["coverage_ratio"] == pytest.approx(6 / 9)
    assert covered["short_intervals"] == 1
    assert covered["intervals"] == 3
    assert covered["extra_agent_intervals"] == 0

    generous = staffing.service_level_of([5, 5, 5], [2, 3, 4])
    assert generous["coverage_ratio"] == pytest.approx(1.0)
    # (5-2) + (5-3) + (5-4) = 6 surplus agent-intervals.
    assert generous["extra_agent_intervals"] == 6

    with pytest.raises(ValidationFailed):
        staffing.service_level_of([1, 2], [1, 2, 3])


def test_shrinkage_from_history_is_bounded():
    summary = HistorySummary(
        intervals=96,
        offered=1000,
        handled=950,
        abandoned=50,
        abandon_rate=0.05,
        average_handle_seconds=200.0,
        average_speed_answer_seconds=15.0,
        service_level=0.9,
        occupied_seconds=190_000.0,
        paid_seconds=200_000.0,
        shrinkage=0.05,
    )
    value = staffing.shrinkage_from_history(summary)
    assert 0.0 <= value < 1.0
    assert value == pytest.approx(0.05, abs=1e-6)


def test_the_default_target_comes_from_settings(monkeypatch):
    from app.platform_v2 import settings as settings_mod

    monkeypatch.setenv("WFM_SERVICE_LEVEL", "0.85")
    monkeypatch.setenv("WFM_ANSWER_SECONDS", "25")
    settings_mod.reset_plat2_settings_cache()
    try:
        target = staffing.default_target()
        assert target.service_level == pytest.approx(0.85)
        assert target.answer_seconds == pytest.approx(25.0)
        target.validate()
    finally:
        settings_mod.reset_plat2_settings_cache()


def test_horizon_dates_are_local_days():
    days = staffing.horizon_dates(datetime(2026, 11, 1).date(), 3, "Asia/Dhaka")
    assert len(days) == 3
    assert days[0].isoformat() == "2026-11-01"


def test_window_requirements_aligns_the_plan_to_real_clock_time():
    built = [point(40, index=index) for index in range(96)]
    requirements = [
        staffing.requirement_for_point(item, staffing.StaffingTarget()) for item in built
    ]
    start = START + timedelta(hours=8)
    end = START + timedelta(hours=10)
    curve = staffing.window_requirements(requirements, start=start, end=end)
    assert len(curve) == 8, "two hours of 15-minute slots"
    assert all(value == requirements[32].paid_agents for value in curve)
    with pytest.raises(ValidationFailed):
        staffing.window_requirements(requirements, start=end, end=start)
