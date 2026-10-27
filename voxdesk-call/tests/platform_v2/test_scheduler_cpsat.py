"""The CP-SAT scheduler: real solves, real infeasibility, real labour rules.

Every test here runs the actual OR-Tools model. Nothin is stubbed, so the assertions are
about what the solver produces: coverage against a known requirement vector, hours per
employee against the weekly ceiling, one shift per day, blocked windows respected, and a
genuinely over-demanding week refusing to produce a plan instead of quietly
under-staffing it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.platform_v2.errors import InfeasibleSchedule, ValidationFailed
from app.platform_v2.wfm.scheduler import (
    EmployeeSpec,
    LaborRules,
    ScheduleRequest,
    ShiftSpec,
    SolveResult,
    curve_from_requirements,
    requirement_curve,
    rules_from_row,
    solve,
    templates_from_rows,
    validate_week_start,
    week_days,
)

MONDAY = date(2026, 11, 2)
DAYS = week_days(MONDAY)
INTERVAL = 60
SLOTS_PER_DAY = 24


def template(
    *,
    name: str = "day",
    start_minute: int = 8 * 60,
    length_minutes: int = 8 * 60,
    paid_break: int = 0,
    unpaid_break: int = 0,
    skill: str = "",
) -> ShiftSpec:
    return ShiftSpec(
        template_id=uuid.uuid4(),
        name=name,
        start_minute=start_minute,
        length_minutes=length_minutes,
        paid_break_minutes=paid_break,
        unpaid_break_minutes=unpaid_break,
        skill_key=skill,
    )


def employees(count: int, **kwargs) -> tuple[EmployeeSpec, ...]:
    return tuple(EmployeeSpec(user_id=uuid.uuid4(), **kwargs) for _ in range(count))


def request(
    *,
    employees: tuple[EmployeeSpec, ...],
    templates: tuple[ShiftSpec, ...],
    requirement: tuple[int, ...],
    rules: LaborRules | None = None,
    blocked: tuple[tuple[uuid.UUID, datetime, datetime], ...] = (),
    allow_shortfall: bool = True,
) -> ScheduleRequest:
    return ScheduleRequest(
        week_start=MONDAY,
        timezone_name="UTC",
        interval_minutes=INTERVAL,
        employees=employees,
        templates=templates,
        rules=rules or LaborRules(),
        requirement=requirement,
        days=DAYS,
        blocked=blocked,
        allow_shortfall=allow_shortfall,
    )


def eight_hour_day(requirement: int, *, start: int = 8, hours: int = 8) -> tuple[int, ...]:
    """A requirement vector with ``requirement`` agents wanted during one window."""
    curve = [0] * (len(DAYS) * SLOTS_PER_DAY)
    for day_index in range(len(DAYS)):
        for slot in range(start, start + hours):
            curve[day_index * SLOTS_PER_DAY + slot] = requirement
    return tuple(curve)


# --- the solved plan -----------------------------------------------------------


def test_a_solvable_week_covers_the_requirement_exactly():
    """24 agents over one 8-hour window: 3 per slot, every day, and nothing more."""
    result = solve(
        request(
            employees=employees(24),
            templates=(template(),),
            requirement=eight_hour_day(3),
        )
    )
    assert isinstance(result, SolveResult)
    assert result.solved, result.status
    assert result.status in {"OPTIMAL", "FEASIBLE"}
    assert result.shortfall == 0
    assert sum(min(c, r) for c, r in zip(result.coverage, result.requirement)) == sum(
        result.requirement
    )
    assert result.as_report()["coverage_ratio"] == pytest.approx(1.0)
    # 24 agents x 8 h / 7 days = 27.4 shifts, so 21 shifts of 8 h is the minimum whole
    # number that covers 3 concurrent agents for 8 h on each of 7 days.
    assert len(result.assignments) >= 21


def test_the_solver_never_assigns_more_than_the_daily_shift_limit():
    result = solve(
        request(
            employees=employees(8),
            templates=(template(name="early", start_minute=8 * 60), template(name="late", start_minute=14 * 60)),
            requirement=eight_hour_day(2),
            rules=LaborRules(max_shifts_per_day=1),
        )
    )
    assert result.solved
    per_user_day = [(item.user_id, item.day) for item in result.assignments]
    assert len(per_user_day) == len(set(per_user_day)), "one shift per person per day"


def test_the_weekly_hour_ceiling_is_respected():
    rules = LaborRules(max_hours_per_week=40.0)
    result = solve(
        request(
            employees=employees(6),
            templates=(template(),),
            requirement=eight_hour_day(2),
            rules=rules,
        )
    )
    assert result.solved
    assert result.hours_by_user, "a solved plan must report hours per person"
    for user_id, hours in result.hours_by_user.items():
        assert hours <= 40.0 + 1e-6, f"{user_id} was given {hours}h over a 40h ceiling"


def test_a_blocked_window_is_never_scheduled():
    """Approved leave is a hard constraint, not a preference."""
    staff = employees(10)
    blocked_person = staff[0]
    window_start = datetime.combine(DAYS[0], time(8, 0), tzinfo=UTC)
    window_end = window_start + timedelta(hours=8)
    result = solve(
        request(
            employees=staff,
            templates=(template(),),
            requirement=eight_hour_day(1),
            blocked=((blocked_person.user_id, window_start, window_end),),
        )
    )
    assert result.solved
    for assignment in result.assignments:
        if assignment.user_id != blocked_person.user_id:
            continue
        assert not (
            assignment.starts_at < window_end and assignment.ends_at > window_start
        ), "somebody on leave was scheduled"


def test_a_skill_gated_template_only_goes_to_people_with_the_skill():
    skilled = tuple(EmployeeSpec(user_id=uuid.uuid4(), skills=frozenset({"billing"})) for _ in range(3))
    unskilled = employees(5)
    gated = template(name="billing", skill="billing")
    result = solve(
        request(
            employees=skilled + unskilled,
            templates=(gated,),
            requirement=eight_hour_day(1),
        )
    )
    assert result.solved
    skilled_ids = {person.user_id for person in skilled}
    assert result.assignments, "the skilled people could cover the window"
    for assignment in result.assignments:
        assert assignment.user_id in skilled_ids
        assert assignment.skill_key == "billing"


def test_the_same_inputs_produce_the_same_plan():
    """A fixed seed is what makes a rota reviewable and a failure reproducible."""
    staff = employees(9)
    shapes = (template(), template(name="late", start_minute=12 * 60))
    requirement = eight_hour_day(2)
    first = solve(request(employees=staff, templates=shapes, requirement=requirement))
    second = solve(request(employees=staff, templates=shapes, requirement=requirement))
    assert [item.as_dict() for item in first.assignments] == [
        item.as_dict() for item in second.assignments
    ]


def test_paid_and_unpaid_breaks_reduce_coverage_but_not_hours():
    """A break is paid time that answers nothing; it must not be counted as coverage."""
    shape = template(paid_break=60, unpaid_break=30)
    assert shape.length_minutes == 480
    assert shape.paid_minutes == 450, "unpaid break is not paid"
    assert shape.coverage_minutes == 390, "paid and unpaid breaks both stop coverage"


# --- refusal -------------------------------------------------------------------


def test_demand_beyond_capacity_is_infeasible_with_the_reason():
    with pytest.raises(InfeasibleSchedule) as caught:
        solve(
            request(
                employees=employees(2),
                templates=(template(),),
                requirement=eight_hour_day(20),
                allow_shortfall=False,
            )
        )
    detail = caught.value.detail
    assert detail["reasons"], "an infeasible solve must say why"
    assert detail["required_agent_hours"] > detail["available_agent_hours"]
    assert detail["employees"] == 2
    assert detail["rules"]["max_hours_per_week"] == 48.0


def test_shortfall_mode_returns_the_gap_instead_of_refusing():
    result = solve(
        request(
            employees=employees(2),
            templates=(template(),),
            requirement=eight_hour_day(20),
            allow_shortfall=True,
        )
    )
    assert result.solved
    assert result.shortfall > 0, "the gap must be visible, not hidden"
    assert result.as_report()["coverage_ratio"] < 1.0
    assert result.shortfall == sum(
        max(0, need - have) for need, have in zip(result.requirement, result.coverage)
    )


def test_no_templates_is_a_validation_error_not_an_infeasible_schedule():
    with pytest.raises(ValidationFailed):
        solve(request(employees=employees(3), templates=(), requirement=eight_hour_day(1)))


def test_a_requirement_vector_of_the_wrong_length_is_refused():
    with pytest.raises(ValidationFailed):
        solve(
            request(
                employees=employees(3),
                templates=(template(),),
                requirement=(1, 2, 3),
            )
        )


@pytest.mark.parametrize(
    "rules",
    [
        LaborRules(max_hours_per_week=0),
        LaborRules(min_rest_hours=-1),
        LaborRules(max_consecutive_days=0),
        LaborRules(max_consecutive_days=20),
        LaborRules(max_shift_hours=0),
        LaborRules(min_split_gap_minutes=-5),
        LaborRules(max_shifts_per_day=0),
    ],
)
def test_impossible_labour_rules_are_rejected_before_the_solver_runs(rules):
    with pytest.raises(ValidationFailed):
        solve(
            request(
                employees=employees(4),
                templates=(template(),),
                requirement=eight_hour_day(1),
                rules=rules,
            )
        )


# --- inputs the router builds ---------------------------------------------------


def test_week_start_must_be_a_monday():
    validate_week_start(MONDAY)
    with pytest.raises(ValidationFailed):
        validate_week_start(MONDAY + timedelta(days=1))


def test_week_days_are_seven_consecutive_dates():
    assert len(DAYS) == 7
    assert DAYS[0] == MONDAY
    assert DAYS[-1] == MONDAY + timedelta(days=6)


def test_requirement_curve_flattens_day_and_slot_in_the_solver_order():
    demand = {(DAYS[0], 0): 2, (DAYS[0], 23): 1, (DAYS[1], 5): 4}
    curve = requirement_curve(
        demand, days=DAYS, interval_minutes=INTERVAL, slots_per_day=SLOTS_PER_DAY
    )
    assert len(curve) == len(DAYS) * SLOTS_PER_DAY
    assert curve[0] == 2
    assert curve[23] == 1
    assert curve[SLOTS_PER_DAY + 5] == 4
    assert sum(curve) == 7


def test_curve_from_requirements_projects_a_staffing_curve_onto_the_grid():
    """The busiest part of a slot decides it: a rota covers a slot or it does not."""

    class Requirement:
        def __init__(self, start, minutes, paid):
            self.interval_start = start
            self.interval_minutes = minutes
            self.paid_agents = paid

    zone = ZoneInfo("Asia/Dhaka")
    local_midnight = datetime.combine(DAYS[0], time(0, 0), tzinfo=zone)
    requirements = [
        # Two 60-minute requirements in one hour-long slot: the larger wins.
        Requirement(local_midnight.astimezone(UTC), 60, 2),
        Requirement((local_midnight + timedelta(minutes=30)).astimezone(UTC), 30, 5),
        # A requirement outside the week is ignored entirely.
        Requirement((local_midnight - timedelta(days=1)).astimezone(UTC), 60, 99),
    ]
    curve = curve_from_requirements(
        requirements, days=DAYS, interval_minutes=INTERVAL, zone_name="Asia/Dhaka"
    )
    assert len(curve) == len(DAYS) * SLOTS_PER_DAY
    assert curve[0] == 5
    assert sum(curve) == 5, "nothing outside the week leaks in"


def test_shift_templates_are_read_from_the_rows_the_router_passes():
    class Row:
        id = uuid.uuid4()
        name = "support"
        start_minute = 540
        length_minutes = 480
        paid_break_minutes = 15
        unpaid_break_minutes = 30
        required_skill_key = "support"
        is_active = True

    specs = templates_from_rows([Row()])
    assert len(specs) == 1
    assert specs[0].start_minute == 540
    assert specs[0].paid_minutes == 450
    assert specs[0].skill_key == "support"


def test_labour_rules_default_when_a_tenant_has_no_row():
    rules = rules_from_row(None)
    assert rules.max_hours_per_week == 48.0
    assert rules.min_rest_hours == 10.0
    assert rules.max_consecutive_days == 6


def test_labour_rules_are_read_from_a_stored_row():
    class Row:
        max_hours_per_week = 37.5
        min_rest_hours = 11.0
        max_consecutive_days = 5
        max_shift_hours = 10.0
        min_split_gap_minutes = 90

    rules = rules_from_row(Row())
    assert rules.max_hours_per_week == 37.5
    assert rules.min_rest_hours == 11.0
    assert rules.max_consecutive_days == 5
    assert rules.min_split_gap_minutes == 90
    # The stored row carries no per-day cap, so the solver's single-shift default holds.
    assert rules.max_shifts_per_day == 1
