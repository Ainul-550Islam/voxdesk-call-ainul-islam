"""Shift scheduling as a constrained optimisation problem (OR-Tools CP-SAT).

Why a solver and not a greedy pass: the constraints are simultaneous. A rota that
respects the weekly hour cap can break the rest rule; one that respects rest can
leave Thursday short. A greedy builder fixes whichever constraint it looked at
last and calls the result a plan. CP-SAT states the rules once and either finds a
rota that satisfies all of them or proves that none exists — and the proof is the
useful part, because "no plan" without a reason is what sends people back to
spreadsheets.

Model
-----
Decision variables ``x[employee, day, template]`` — one boolean per plausible
assignment. Everything else is derived:

* **Coverage**: for each interval, the sum of shifts covering it must meet the
  requirement from ``wfm.staffing``. With ``allow_shortfall`` the model may miss
  it and pays a large penalty per missing agent-interval; the shortfall is then
  reported per interval so the gap is visible rather than absorbed.
* **One shift a day**, **max hours a week**, **max shift length**.
* **Minimum rest** between consecutive days' shifts, checked pair-wise on the
  template grid (a template pair either satisfies rest or is forbidden).
* **Consecutive days** capped by a sliding-window constraint.
* **Approved time off** and employee availability remove variables outright, so
  the solver never reports a plan that schedules somebody on leave.
* **Fairness**: total hours per employee enter the objective, so the plan does not
  give every Saturday to the same three people.

Determinism
-----------
``num_search_workers = 1`` with a fixed seed from settings. A rota that changes
because a different worker thread finished first is untestable and unexplainable,
and schedulers are exactly the code people ask to explain.

Timeout
-------
``wfm_solver_timeout_seconds`` bounds the search. A feasible-but-not-proven-optimal
plan is accepted and labelled ``FEASIBLE``; the status is stored with the version
so nobody mistakes a heuristic result for a proof.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from app.platform_v2.errors import InfeasibleSchedule, SolverUnavailable, ValidationFailed
from app.platform_v2.settings import PlatformV2Settings, get_plat2_settings
from app.platform_v2.wfm.history import tenant_zone

#: Penalty per uncovered agent-interval. Large enough that the solver will always
#: prefer covering to saving hours, finite so a genuinely impossible Thursday
#: still produces a plan for the rest of the week.
SHORTFALL_PENALTY = 10_000
#: Over-coverage is only mildly discouraged: spare people cost money, unmet demand
#: costs customers, and the ratio is what encodes that opinion.
OVERCOVER_PENALTY = 1
#: Weight on the spread between the busiest and quietest employee.
FAIRNESS_PENALTY = 20


def _load_cp_model():
    """Import OR-Tools lazily so the rest of the platform boots without it."""
    try:  # pragma: no cover - exercised by the solver-unavailable test
        from ortools.sat.python import cp_model
    except Exception as exc:  # pragma: no cover
        raise SolverUnavailable(
            "OR-Tools is not installed; scheduling is unavailable in this deployment"
        ) from exc
    return cp_model


@dataclass(frozen=True)
class ShiftSpec:
    """A shift template flattened for the solver (minutes since local midnight)."""

    template_id: uuid.UUID
    name: str
    start_minute: int
    length_minutes: int
    paid_break_minutes: int = 0
    unpaid_break_minutes: int = 0
    skill_key: str = ""

    @property
    def paid_minutes(self) -> int:
        return max(0, self.length_minutes - self.unpaid_break_minutes)

    @property
    def coverage_minutes(self) -> int:
        """Minutes the shift actually answers contacts."""
        return max(0, self.length_minutes - self.unpaid_break_minutes - self.paid_break_minutes)

    @property
    def end_minute(self) -> int:
        return self.start_minute + self.length_minutes

    def slots(self, interval_minutes: int) -> range:
        first = self.start_minute // interval_minutes
        last = (self.end_minute + interval_minutes - 1) // interval_minutes
        return range(first, last)


@dataclass(frozen=True)
class LaborRules:
    """The rules a rota must satisfy. Defaults are deliberately permissive."""

    max_hours_per_week: float = 48.0
    min_rest_hours: float = 10.0
    max_consecutive_days: int = 6
    max_shift_hours: float = 12.0
    min_split_gap_minutes: int = 0
    max_shifts_per_day: int = 1

    def validate(self) -> None:
        if self.max_hours_per_week <= 0:
            raise ValidationFailed("max_hours_per_week must be positive")
        if self.min_rest_hours < 0:
            raise ValidationFailed("min_rest_hours must be non-negative")
        if not 1 <= self.max_consecutive_days <= 14:
            raise ValidationFailed("max_consecutive_days must be between 1 and 14")
        if self.max_shift_hours <= 0:
            raise ValidationFailed("max_shift_hours must be positive")
        if self.min_split_gap_minutes < 0:
            raise ValidationFailed("min_split_gap_minutes must be non-negative")
        if self.max_shifts_per_day < 1:
            raise ValidationFailed("max_shifts_per_day must be at least 1")

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_hours_per_week": self.max_hours_per_week,
            "min_rest_hours": self.min_rest_hours,
            "max_consecutive_days": self.max_consecutive_days,
            "max_shift_hours": self.max_shift_hours,
            "min_split_gap_minutes": self.min_split_gap_minutes,
            "max_shifts_per_day": self.max_shifts_per_day,
        }


@dataclass(frozen=True)
class EmployeeSpec:
    user_id: uuid.UUID
    skills: frozenset[str] = frozenset()
    max_hours: float | None = None
    prior_hours: float = 0.0

    def can_take(self, shift: ShiftSpec) -> bool:
        if not shift.skill_key:
            return True
        return shift.skill_key in self.skills or "*" in self.skills


@dataclass(frozen=True)
class Assignment:
    user_id: uuid.UUID
    template_id: uuid.UUID
    day: date
    starts_at: datetime
    ends_at: datetime
    break_minutes: int
    skill_key: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "user_id": str(self.user_id),
            "template_id": str(self.template_id),
            "day": self.day.isoformat(),
            "starts_at": self.starts_at.isoformat(),
            "ends_at": self.ends_at.isoformat(),
            "break_minutes": self.break_minutes,
            "skill_key": self.skill_key,
        }


@dataclass
class SolveResult:
    status: str
    assignments: list[Assignment]
    hours_by_user: dict[str, float]
    coverage: list[int]
    requirement: list[int]
    shortfall: int
    overcoverage: int
    objective: float
    wall_time_seconds: float
    rule_report: dict[str, Any] = field(default_factory=dict)

    @property
    def solved(self) -> bool:
        return self.status in ("OPTIMAL", "FEASIBLE")

    def as_report(self) -> dict[str, Any]:
        """What gets stored on ``wfm_schedule_versions.solver_report``."""
        return {
            "status": self.status,
            "assignments": len(self.assignments),
            "hours_by_user": {user: round(hours, 2) for user, hours in self.hours_by_user.items()},
            "shortfall_agent_intervals": self.shortfall,
            "overcoverage_agent_intervals": self.overcoverage,
            "objective": round(self.objective, 3),
            "wall_time_seconds": round(self.wall_time_seconds, 3),
            "intervals": len(self.requirement),
            "coverage_ratio": round(
                sum(min(c, r) for c, r in zip(self.coverage, self.requirement))
                / sum(self.requirement),
                6,
            )
            if sum(self.requirement)
            else 1.0,
            "rules": self.rule_report,
        }


@dataclass(frozen=True)
class ScheduleRequest:
    """Everything the solver needs, already resolved to plain values."""

    week_start: date
    timezone_name: str
    interval_minutes: int
    employees: tuple[EmployeeSpec, ...]
    templates: tuple[ShiftSpec, ...]
    rules: LaborRules
    requirement: tuple[int, ...]
    days: tuple[date, ...]
    blocked: tuple[tuple[uuid.UUID, datetime, datetime], ...] = ()
    allow_shortfall: bool = True

    def slot_start(self, day_index: int, slot_index: int) -> datetime:
        zone = tenant_zone(self.timezone_name)
        midnight = datetime.combine(self.days[day_index], time(0, 0), tzinfo=zone)
        return (midnight + timedelta(minutes=slot_index * self.interval_minutes)).astimezone(
            timezone.utc
        )


def requirement_curve(
    demand: dict[tuple[date, int], int],
    *,
    days: tuple[date, ...],
    interval_minutes: int,
    slots_per_day: int,
) -> tuple[int, ...]:
    """Flatten per-(day, slot) demand into one vector in the solver's order."""
    curve: list[int] = []
    for day in days:
        for slot in range(slots_per_day):
            curve.append(max(0, int(demand.get((day, slot), 0))))
    return tuple(curve)


def _overlaps(
    day: date, shift: ShiftSpec, zone_name: str, window_start: datetime, window_end: datetime
) -> bool:
    zone = tenant_zone(zone_name)
    midnight = datetime.combine(day, time(0, 0), tzinfo=zone)
    start = (midnight + timedelta(minutes=shift.start_minute)).astimezone(timezone.utc)
    end = start + timedelta(minutes=shift.length_minutes)
    return start < window_end and end > window_start


def solve(request: ScheduleRequest, settings: PlatformV2Settings | None = None) -> SolveResult:
    """Build one week of shifts that satisfies the rules and covers the demand."""
    cp_model = _load_cp_model()
    cfg = settings or get_plat2_settings()
    request.rules.validate()
    if not request.templates:
        raise ValidationFailed("at least one shift template is required")
    if not request.employees:
        raise InfeasibleSchedule(
            "no employees are available to schedule",
            detail={"reason": "no_employees", "days": [day.isoformat() for day in request.days]},
        )
    interval = request.interval_minutes
    slots_per_day = 24 * 60 // interval
    days = request.days
    if len(request.requirement) != len(days) * slots_per_day:
        raise ValidationFailed("requirement length does not match days x slots")

    templates = [
        shift for shift in request.templates
        if shift.length_minutes <= request.rules.max_shift_hours * 60
    ]
    if not templates:
        raise InfeasibleSchedule(
            "every shift template exceeds max_shift_hours",
            detail={
                "max_shift_hours": request.rules.max_shift_hours,
                "templates": [shift.name for shift in request.templates],
            },
        )

    model = cp_model.CpModel()
    x: dict[tuple[int, int, int], Any] = {}
    hours_per_employee: dict[int, list[Any]] = {index: [] for index in range(len(request.employees))}
    paid_by_employee: list[list[tuple[int, int, int, float]]] = [
        [] for _ in request.employees
    ]

    for e_index, employee in enumerate(request.employees):
        cap = request.rules.max_hours_per_week
        if employee.max_hours is not None:
            cap = min(cap, employee.max_hours)
        for d_index, day in enumerate(days):
            for t_index, shift in enumerate(templates):
                if not employee.can_take(shift):
                    continue
                if any(
                    user == employee.user_id
                    and _overlaps(day, shift, request.timezone_name, start, end)
                    for user, start, end in request.blocked
                ):
                    continue
                variable = model.NewBoolVar(f"x_{e_index}_{d_index}_{t_index}")
                x[(e_index, d_index, t_index)] = variable
                if shift.paid_minutes:
                    paid = shift.paid_minutes / 60.0
                    hours_per_employee[e_index].append(paid * variable)
                    paid_by_employee[e_index].append((d_index, t_index, e_index, paid))

    # One shift per day (per employee), and never more than the daily cap.
    for e_index in range(len(request.employees)):
        for d_index in range(len(days)):
            same_day = [
                variable
                for (emp, day_index, _), variable in x.items()
                if emp == e_index and day_index == d_index
            ]
            if same_day:
                model.Add(sum(same_day) <= request.rules.max_shifts_per_day)

    # Weekly hours cap.
    for e_index, employee in enumerate(request.employees):
        cap = request.rules.max_hours_per_week
        if employee.max_hours is not None:
            cap = min(cap, employee.max_hours)
        # Hours are fractional; scale by 100 and use an integer bound.
        scaled = [
            int(round(paid * 100)) * x[(e_index, d_index, t_index)]
            for d_index, t_index, _, paid in paid_by_employee[e_index]
        ]
        if scaled:
            model.Add(sum(scaled) <= int(cap * 100))

    # Rest between consecutive days.
    for e_index in range(len(request.employees)):
        for d_index in range(len(days) - 1):
            for t_index, shift in enumerate(templates):
                current = x.get((e_index, d_index, t_index))
                if current is None:
                    continue
                for t_next, next_shift in enumerate(templates):
                    following = x.get((e_index, d_index + 1, t_next))
                    if following is None:
                        continue
                    gap_minutes = 24 * 60 - shift.end_minute + next_shift.start_minute
                    if gap_minutes < request.rules.min_rest_hours * 60:
                        model.Add(current + following <= 1)

    # Consecutive working days.
    window = request.rules.max_consecutive_days + 1
    if len(days) >= window:
        for e_index in range(len(request.employees)):
            for start in range(0, len(days) - window + 1):
                terms = [
                    variable
                    for (emp, day_index, _), variable in x.items()
                    if emp == e_index and start <= day_index < start + window
                ]
                if terms:
                    model.Add(sum(terms) <= request.rules.max_consecutive_days)

    # Coverage.
    shortfall_terms: list[Any] = []
    overcover_terms: list[Any] = []
    shortfall_by_slot: dict[tuple[int, int], Any] = {}
    over_by_slot: dict[tuple[int, int], Any] = {}
    # Which variables cover which slot, built once: the naive nested scan is
    # quadratic in employees and makes a 60-person week slow to even model.
    covering_by_slot: dict[tuple[int, int], list[Any]] = {}
    for (e_index, d_index, t_index), variable in x.items():
        for slot in templates[t_index].slots(interval):
            if 0 <= slot < slots_per_day:
                covering_by_slot.setdefault((d_index, slot), []).append(variable)

    for d_index, day in enumerate(days):
        for slot in range(slots_per_day):
            need = request.requirement[d_index * slots_per_day + slot]
            covering = covering_by_slot.get((d_index, slot), [])
            total = sum(covering) if covering else 0
            if need:
                if request.allow_shortfall:
                    short = model.NewIntVar(0, need, f"short_{d_index}_{slot}")
                    model.Add(total + short >= need)
                    shortfall_terms.append(short)
                    shortfall_by_slot[(d_index, slot)] = short
                else:
                    # Hard coverage. Without this branch ``allow_shortfall`` would be
                    # a decoration: the soft form always has a solution, so a caller
                    # asking for "a perfect rota or nothing" would silently get an
                    # understaffed one. The slot still enters the map with a constant
                    # zero so reporting code has one shape to handle.
                    model.Add(total >= need)
                    shortfall_by_slot[(d_index, slot)] = model.NewConstant(0)
            # Over-coverage beyond the requirement is allowed but priced.
            over = model.NewIntVar(0, len(request.employees), f"over_{d_index}_{slot}")
            model.Add(over >= total - need)
            overcover_terms.append(over)
            over_by_slot[(d_index, slot)] = over

    # Fairness: the busiest employee defines the spread.
    total_hours_terms: list[Any] = []
    for e_index in range(len(request.employees)):
        scaled = []
        for d_index, t_index, _, paid in paid_by_employee[e_index]:
            scaled.append(int(round(paid * 100)) * x[(e_index, d_index, t_index)])
        if not scaled:
            continue
        total = model.NewIntVar(0, 100 * 24 * 7, f"hours_{e_index}")
        model.Add(total == sum(scaled))
        total_hours_terms.append(total)
    fairness_penalty = None
    if len(total_hours_terms) > 1:
        peak = model.NewIntVar(0, 100 * 24 * 7, "peak_hours")
        model.AddMaxEquality(peak, total_hours_terms)
        fairness_penalty = peak

    objective_terms: list[Any] = [SHORTFALL_PENALTY * term for term in shortfall_terms]
    objective_terms += [OVERCOVER_PENALTY * term for term in overcover_terms]
    if fairness_penalty is not None:
        objective_terms.append(FAIRNESS_PENALTY * fairness_penalty)
    model.Minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(cfg.wfm_solver_timeout_seconds)
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = int(cfg.wfm_solver_seed)
    status = solver.Solve(model)
    status_name = solver.StatusName(status)

    if status_name == "INFEASIBLE":
        raise InfeasibleSchedule(
            "no rota satisfies the labour rules and the coverage requirement",
            detail=_infeasible_detail(request, templates, slots_per_day),
        )
    if status_name == "MODEL_INVALID":
        raise ValidationFailed("the scheduling model is invalid")
    if status_name == "UNKNOWN":
        raise InfeasibleSchedule(
            "the solver stopped before finding a feasible rota",
            detail={
                "status": status_name,
                "timeout_seconds": cfg.wfm_solver_timeout_seconds,
                "hint": "shorten the horizon, add templates, or allow shortfall",
            },
        )

    assignments: list[Assignment] = []
    hours_by_user: dict[str, float] = {str(employee.user_id): 0.0 for employee in request.employees}
    zone = tenant_zone(request.timezone_name)
    for (e_index, d_index, t_index), variable in x.items():
        if solver.Value(variable) != 1:
            continue
        shift = templates[t_index]
        employee = request.employees[e_index]
        midnight = datetime.combine(days[d_index], time(0, 0), tzinfo=zone)
        starts_at = (midnight + timedelta(minutes=shift.start_minute)).astimezone(timezone.utc)
        ends_at = starts_at + timedelta(minutes=shift.length_minutes)
        assignments.append(
            Assignment(
                user_id=employee.user_id,
                template_id=shift.template_id,
                day=days[d_index],
                starts_at=starts_at,
                ends_at=ends_at,
                break_minutes=shift.paid_break_minutes + shift.unpaid_break_minutes,
                skill_key=shift.skill_key,
            )
        )
        hours_by_user[str(employee.user_id)] += shift.paid_minutes / 60.0

    coverage: list[int] = []
    for d_index in range(len(days)):
        for slot in range(slots_per_day):
            coverage.append(
                sum(
                    1
                    for assignment in assignments
                    if assignment.day == days[d_index]
                    and assignment.starts_at
                    <= request.slot_start(d_index, slot)
                    < assignment.ends_at
                )
            )

    shortfall_total = sum(solver.Value(term) for term in shortfall_terms)
    over_total = sum(solver.Value(term) for term in overcover_terms)
    assignments.sort(key=lambda item: (item.starts_at, str(item.user_id)))
    return SolveResult(
        status=status_name,
        assignments=assignments,
        hours_by_user={user: round(hours, 2) for user, hours in hours_by_user.items()},
        coverage=coverage,
        requirement=list(request.requirement),
        shortfall=int(shortfall_total),
        overcoverage=int(over_total),
        objective=float(solver.ObjectiveValue()),
        wall_time_seconds=float(solver.WallTime()),
        rule_report={
            **request.rules.as_dict(),
            "templates_considered": len(templates),
            "templates_rejected": [
                shift.name
                for shift in request.templates
                if shift not in templates
            ],
            "employees": len(request.employees),
            "blocked_windows": len(request.blocked),
            "shortfall_by_day": _shortfall_by_day(
                {key: solver.Value(term) for key, term in shortfall_by_slot.items()},
                days,
                slots_per_day,
            ),
        },
    )


def _shortfall_by_day(
    shortfall: dict[tuple[int, int], int], days: tuple[date, ...], slots_per_day: int
) -> dict[str, int]:
    totals: dict[str, int] = {}
    for (d_index, _), value in shortfall.items():
        if not value:
            continue
        key = days[d_index].isoformat()
        totals[key] = totals.get(key, 0) + value
    return totals


def _infeasible_detail(
    request: ScheduleRequest, templates: list[ShiftSpec], slots_per_day: int
) -> dict[str, Any]:
    """Explain *why* no rota exists, in terms an operations lead can act on.

    Three checks cover almost every real infeasibility: demand exceeds headcount,
    the hour cap cannot cover the demand, or nothing can be scheduled on a day at
    all. Each is cheap arithmetic and each maps to a different fix.
    """
    per_day: dict[str, int] = {}
    peak = 0
    for d_index, day in enumerate(request.days):
        slots = request.requirement[d_index * slots_per_day : (d_index + 1) * slots_per_day]
        per_day[day.isoformat()] = sum(slots)
        peak = max([peak, *slots])
    reasons: list[str] = []
    if peak > len(request.employees):
        reasons.append(
            f"peak requirement of {peak} agents exceeds {len(request.employees)} employees"
        )
    capacity_hours = sum(
        min(request.rules.max_hours_per_week, employee.max_hours or request.rules.max_hours_per_week)
        for employee in request.employees
    )
    required_hours = sum(request.requirement) * request.interval_minutes / 60.0
    if required_hours > capacity_hours:
        reasons.append(
            f"required {round(required_hours, 1)} paid agent-hours but only "
            f"{round(capacity_hours, 1)} are available under max_hours_per_week"
        )
    if not reasons:
        reasons.append(
            "rest, consecutive-day or time-off constraints together leave no valid assignment"
        )
    return {
        "reasons": reasons,
        "peak_requirement": peak,
        "employees": len(request.employees),
        "templates": [shift.name for shift in templates],
        "demand_by_day": per_day,
        "required_agent_hours": round(required_hours, 2),
        "available_agent_hours": round(capacity_hours, 2),
        "rules": request.rules.as_dict(),
    }


def templates_from_rows(rows: list[Any]) -> list[ShiftSpec]:
    """Adapt stored ``ShiftTemplate`` rows without leaking ORM objects inwards."""
    return [
        ShiftSpec(
            template_id=row.id,
            name=row.name,
            start_minute=int(row.start_minute),
            length_minutes=int(row.length_minutes),
            paid_break_minutes=int(row.paid_break_minutes or 0),
            unpaid_break_minutes=int(row.unpaid_break_minutes or 0),
            skill_key=str(row.required_skill_key or ""),
        )
        for row in rows
        if row.is_active
    ]


def rules_from_row(row: Any | None) -> LaborRules:
    if row is None:
        return LaborRules()
    return LaborRules(
        max_hours_per_week=float(row.max_hours_per_week),
        min_rest_hours=float(row.min_rest_hours),
        max_consecutive_days=int(row.max_consecutive_days),
        max_shift_hours=float(row.max_shift_hours),
        min_split_gap_minutes=int(row.min_split_gap_minutes or 0),
    )


def week_days(week_start: date) -> tuple[date, ...]:
    """Seven consecutive local dates starting at ``week_start``."""
    return tuple(week_start + timedelta(days=offset) for offset in range(7))


def validate_week_start(week_start: date) -> None:
    """Rotas are weekly; a Wednesday start would produce two half-covered weeks."""
    if week_start.weekday() != 0:
        raise ValidationFailed("week_start must be a Monday")


def curve_from_requirements(
    requirements: list[Any],
    *,
    days: tuple[date, ...],
    interval_minutes: int,
    zone_name: str,
) -> tuple[int, ...]:
    """Project a staffing curve onto the solver grid, taking the maximum per slot.

    Maximum, not mean: a rota covers a slot or it does not, so the busiest part of
    a half hour decides.
    """
    slots_per_day = 24 * 60 // interval_minutes
    demand: dict[tuple[date, int], int] = {}
    zone = tenant_zone(zone_name)
    for item in requirements:
        start = item.interval_start
        span = item.interval_minutes
        step = 0
        while step < span:
            moment = start + timedelta(minutes=step)
            local = moment.astimezone(zone)
            day = local.date()
            slot = (local.hour * 60 + local.minute) // interval_minutes
            if day in days:
                demand[(day, slot)] = max(demand.get((day, slot), 0), int(item.paid_agents))
            step += interval_minutes
    return requirement_curve(
        demand, days=days, interval_minutes=interval_minutes, slots_per_day=slots_per_day
    )


__all__ = [
    "Assignment",
    "EmployeeSpec",
    "FAIRNESS_PENALTY",
    "LaborRules",
    "OVERCOVER_PENALTY",
    "SHORTFALL_PENALTY",
    "ScheduleRequest",
    "ShiftSpec",
    "SolveResult",
    "curve_from_requirements",
    "requirement_curve",
    "rules_from_row",
    "solve",
    "templates_from_rows",
    "validate_week_start",
    "week_days",
]
