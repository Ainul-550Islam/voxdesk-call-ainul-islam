"""Staffing: forecast volume -> a requirement for every interval.

The chain is short and every step is explicit so an operations lead can point at
the number that looks wrong:

``volume -> offered load (Erlangs) -> productive agents (Erlang C/A) -> paid agents (shrinkage)``

* The **service level** and **answer time** come from the queue's own target, not
  from a global constant, because a retention line and a sales line do not buy
  the same experience.
* **Occupancy** is capped so the Erlang result leaves room for after-call work;
  an uncapped 90/20 target for a small queue produces one agent at 100% and a
  queue that fails the moment they take a break.
* **Shrinkage** (breaks, coaching, absence) inflates productive agents into the
  paid agents a rota must actually cover. It is applied *after* the Erlang step
  and never inside it, since Erlang knows nothing about breaks.
* **Minimum staffing** is honoured per interval: a queue with any traffic needs at
  least one logged-in agent even if Erlang says 0.4.

The output is a curve, not a rota. ``wfm.scheduler`` is what turns the curve into
named shifts, and keeping the two apart is what makes "we are short on Thursday"
a question about demand rather than about people.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from app.platform_v2.errors import ValidationFailed
from app.platform_v2.settings import PlatformV2Settings, get_plat2_settings
from app.platform_v2.wfm.erlang import ErlangResult, required_agents
from app.platform_v2.wfm.forecast import ForecastPoint, ForecastResult
from app.platform_v2.wfm.history import HistorySummary, tenant_zone

#: Nobody plans a queue below this when it has traffic at all.
MIN_PRODUCTIVE_AGENTS = 1


@dataclass(frozen=True)
class StaffingTarget:
    """Everything the model needs to know about the experience being bought."""

    service_level: float = 0.8
    answer_seconds: float = 20.0
    max_occupancy: float = 0.85
    shrinkage: float = 0.0
    average_handle_seconds: float = 240.0
    model: str = "erlang_c"
    average_patience_seconds: float = 300.0
    minimum_agents: int = 0

    def validate(self) -> None:
        if not 0.0 < self.service_level <= 1.0:
            raise ValidationFailed("service_level must be in (0, 1]")
        if self.answer_seconds < 0:
            raise ValidationFailed("answer_seconds must be non-negative")
        if not 0.0 < self.max_occupancy <= 1.0:
            raise ValidationFailed("max_occupancy must be in (0, 1]")
        if not 0.0 <= self.shrinkage < 1.0:
            # 100% shrinkage means nobody is ever available; refusing is kinder
            # than producing an infinite requirement.
            raise ValidationFailed("shrinkage must be in [0, 1)")
        if self.average_handle_seconds <= 0:
            raise ValidationFailed("average_handle_seconds must be positive")
        if self.model not in ("erlang_c", "erlang_a"):
            raise ValidationFailed("model must be 'erlang_c' or 'erlang_a'")
        if self.average_patience_seconds <= 0:
            raise ValidationFailed("average_patience_seconds must be positive")
        if self.minimum_agents < 0:
            raise ValidationFailed("minimum_agents must be non-negative")

    @property
    def paid_multiplier(self) -> float:
        """How many paid hours buy one productive hour."""
        return 1.0 / (1.0 - self.shrinkage)

    def as_dict(self) -> dict[str, object]:
        return {
            "service_level": self.service_level,
            "answer_seconds": self.answer_seconds,
            "max_occupancy": self.max_occupancy,
            "shrinkage": self.shrinkage,
            "average_handle_seconds": self.average_handle_seconds,
            "model": self.model,
            "average_patience_seconds": self.average_patience_seconds,
            "minimum_agents": self.minimum_agents,
            "paid_multiplier": round(self.paid_multiplier, 4),
        }


@dataclass(frozen=True)
class IntervalRequirement:
    """What one interval needs, in both productive and paid people."""

    interval_start: datetime
    interval_minutes: int
    volume: float
    productive_agents: int
    paid_agents: int
    service_level: float
    occupancy: float
    offered_load: float

    @property
    def productive_hours(self) -> float:
        return self.productive_agents * self.interval_minutes / 60.0

    @property
    def paid_hours(self) -> float:
        return self.paid_agents * self.interval_minutes / 60.0

    def as_dict(self) -> dict[str, object]:
        return {
            "interval_start": self.interval_start.isoformat(),
            "interval_minutes": self.interval_minutes,
            "volume": round(self.volume, 3),
            "productive_agents": self.productive_agents,
            "paid_agents": self.paid_agents,
            "service_level": round(self.service_level, 4),
            "occupancy": round(self.occupancy, 4),
            "offered_load": round(self.offered_load, 4),
        }


@dataclass
class StaffingPlan:
    """A staffing curve plus the totals anybody planning money will ask for."""

    requirements: list[IntervalRequirement]
    target: StaffingTarget
    timezone: str
    band: str
    queue_key: str = ""

    @property
    def productive_hours(self) -> float:
        return sum(item.productive_hours for item in self.requirements)

    @property
    def paid_hours(self) -> float:
        return sum(item.paid_hours for item in self.requirements)

    @property
    def peak(self) -> IntervalRequirement | None:
        return max(self.requirements, key=lambda item: item.paid_agents, default=None)

    def daily(self) -> dict[str, dict[str, float]]:
        zone = tenant_zone(self.timezone)
        days: dict[str, dict[str, float]] = {}
        for item in self.requirements:
            key = item.interval_start.astimezone(zone).date().isoformat()
            bucket = days.setdefault(
                key, {"paid_agents_peak": 0.0, "paid_hours": 0.0, "volume": 0.0}
            )
            bucket["paid_agents_peak"] = max(bucket["paid_agents_peak"], item.paid_agents)
            bucket["paid_hours"] += item.paid_hours
            bucket["volume"] += item.volume
        return {
            day: {name: round(value, 3) for name, value in bucket.items()}
            for day, bucket in sorted(days.items())
        }

    def fte(self, *, hours_per_fte_week: float = 40.0, weeks: int = 1) -> float:
        """Rounded-up headcount for the plan, before any absence planning."""
        if hours_per_fte_week <= 0:
            raise ValidationFailed("hours_per_fte_week must be positive")
        if weeks < 1:
            raise ValidationFailed("weeks must be at least 1")
        return math.ceil(self.paid_hours / (hours_per_fte_week * weeks) * 100) / 100

    def as_dict(self, *, include_intervals: bool = False) -> dict[str, object]:
        body: dict[str, object] = {
            "queue_key": self.queue_key,
            "timezone": self.timezone,
            "band": self.band,
            "target": self.target.as_dict(),
            "intervals": len(self.requirements),
            "peak_paid_agents": self.peak.paid_agents if self.peak else 0,
            "peak_at": self.peak.interval_start.isoformat() if self.peak else None,
            "productive_hours": round(self.productive_hours, 2),
            "paid_hours": round(self.paid_hours, 2),
            "daily": self.daily(),
        }
        if include_intervals:
            body["curve"] = [item.as_dict() for item in self.requirements]
        return body


def requirement_for_point(
    point: ForecastPoint,
    target: StaffingTarget,
    *,
    volume: float | None = None,
) -> IntervalRequirement:
    """Staff one interval, or explain why it needs nobody."""
    offered = point.expected if volume is None else volume
    if offered <= 0:
        return IntervalRequirement(
            interval_start=point.interval_start,
            interval_minutes=point.interval_minutes,
            volume=0.0,
            productive_agents=target.minimum_agents,
            paid_agents=_paid_for(target.minimum_agents, target.shrinkage),
            service_level=1.0,
            occupancy=0.0,
            offered_load=0.0,
        )
    result: ErlangResult = required_agents(
        interval_volume=offered,
        average_handle_seconds=target.average_handle_seconds,
        interval_seconds=point.interval_minutes * 60.0,
        target_service_level=target.service_level,
        target_answer_seconds=target.answer_seconds,
        max_occupancy=target.max_occupancy,
        model=target.model,
        average_patience_seconds=target.average_patience_seconds,
    )
    productive = max(result.agents, target.minimum_agents, MIN_PRODUCTIVE_AGENTS)
    return IntervalRequirement(
        interval_start=point.interval_start,
        interval_minutes=point.interval_minutes,
        volume=offered,
        productive_agents=productive,
        paid_agents=_paid_for(productive, target.shrinkage),
        service_level=result.service_level,
        occupancy=result.occupancy,
        offered_load=result.offered_load,
    )


def _paid_for(productive: int, shrinkage: float) -> int:
    if productive <= 0:
        return 0
    return int(math.ceil(productive / (1.0 - shrinkage) - 1e-9))


def plan_from_forecast(
    forecast: ForecastResult,
    target: StaffingTarget,
    *,
    band: str = "expected",
    queue_key: str = "",
) -> StaffingPlan:
    """Build the whole curve for a forecast, staffing to the chosen band."""
    target.validate()
    if band not in ("low", "expected", "high"):
        raise ValidationFailed("band must be one of low, expected, high")
    requirements = [
        requirement_for_point(
            point,
            target,
            volume={"low": point.low, "expected": point.expected, "high": point.high}[band],
        )
        for point in forecast.points
    ]
    return StaffingPlan(
        requirements=requirements,
        target=target,
        timezone=forecast.timezone,
        band=band,
        queue_key=queue_key,
    )


def plan_from_history(
    summary: HistorySummary,
    points: list[ForecastPoint],
    *,
    timezone_name: str,
    band: str = "expected",
    occupancy: float = 0.85,
    queue_key: str = "",
) -> StaffingPlan:
    """Staff a forward curve using the handle time the history actually measured.

    This is the honest default: AHT from stored conversations, not from a config
    field somebody set when the queue was created.
    """
    aht = summary.average_handle_seconds
    if aht <= 0:
        raise ValidationFailed(
            "history has no measured handle time; set average_handle_seconds explicitly"
        )
    target = StaffingTarget(
        service_level=0.8,
        answer_seconds=20.0,
        max_occupancy=occupancy,
        average_handle_seconds=aht,
    )
    requirements = [
        requirement_for_point(
            point,
            target,
            volume={"low": point.low, "expected": point.expected, "high": point.high}[band],
        )
        for point in points
    ]
    return StaffingPlan(
        requirements=requirements,
        target=target,
        timezone=timezone_name,
        band=band,
        queue_key=queue_key,
    )


def smooth(requirements: list[IntervalRequirement]) -> list[IntervalRequirement]:
    """Take a rolling three-interval maximum over the paid requirement.

    Rosters are built from shifts, not half-hours, so a curve that dips for one
    interval still needs the same number of people through it. Smoothing here —
    rather than in the solver — keeps the solver's coverage constraint honest
    about what the plan admits.
    """
    if len(requirements) < 3:
        return requirements
    smoothed: list[IntervalRequirement] = []
    for index, item in enumerate(requirements):
        window = requirements[max(0, index - 1) : index + 2]
        peak = max(window, key=lambda candidate: candidate.paid_agents)
        if peak.paid_agents <= item.paid_agents:
            smoothed.append(item)
            continue
        smoothed.append(
            IntervalRequirement(
                interval_start=item.interval_start,
                interval_minutes=item.interval_minutes,
                volume=item.volume,
                productive_agents=peak.productive_agents,
                paid_agents=peak.paid_agents,
                service_level=item.service_level,
                occupancy=item.occupancy,
                offered_load=item.offered_load,
            )
        )
    return smoothed


def coverage_slots(
    plan: StaffingPlan,
    *,
    interval_minutes: int = 15,
) -> list[tuple[datetime, int]]:
    """Flatten the plan to ``(interval_start, paid_agents)`` for the solver.

    Intervals are split or merged onto the solver's grid so a plan built on
    30-minute history can still drive a 15-minute rota; when splitting, the
    requirement is the *maximum* of the source interval, never the mean, because
    a roster either covers the busiest part of the half hour or it does not.
    """
    if interval_minutes not in (15, 30):
        raise ValidationFailed("interval_minutes must be 15 or 30")
    slots: list[tuple[datetime, int]] = []
    for item in plan.requirements:
        steps = max(1, item.interval_minutes // interval_minutes)
        for step in range(steps):
            start = item.interval_start + timedelta(minutes=step * interval_minutes)
            slots.append((start, item.paid_agents))
    slots.sort(key=lambda pair: pair[0])
    return slots


def window_requirements(
    requirements: list[IntervalRequirement],
    *,
    start: datetime,
    end: datetime,
    interval_minutes: int = 15,
) -> list[int]:
    """Paid-agent requirement per 15-minute slot across an arbitrary window.

    Used by the publisher's coverage check and by intraday, both of which need the
    plan aligned to real clock time rather than to the grid it was built on.
    """
    if end <= start:
        raise ValidationFailed("end must be after start")
    total_slots = int((end - start).total_seconds() // (interval_minutes * 60))
    curve = [0] * max(1, total_slots)
    for item in requirements:
        item_start = item.interval_start
        item_end = item_start + timedelta(minutes=item.interval_minutes)
        low = max(item_start, start)
        high = min(item_end, end)
        if high <= low:
            continue
        first = int((low - start).total_seconds() // (interval_minutes * 60))
        last = int(math.ceil((high - start).total_seconds() / (interval_minutes * 60)))
        for index in range(first, min(last, len(curve))):
            curve[index] = max(curve[index], item.paid_agents)
    return curve


def service_level_of(
    curve_agents: list[int], requirement: list[int]
) -> dict[str, float]:
    """How much of a requirement the curve actually covers.

    Reported alongside every published rota: ``coverage_ratio`` is the share of
    required agent-intervals covered, ``short_intervals`` counts the slots where
    the rota is below the requirement.
    """
    if len(curve_agents) != len(requirement):
        raise ValidationFailed("curve and requirement must have the same length")
    required_total = sum(requirement)
    covered = sum(min(actual, need) for actual, need in zip(curve_agents, requirement))
    short = sum(1 for actual, need in zip(curve_agents, requirement) if actual < need)
    return {
        "coverage_ratio": round(covered / required_total, 6) if required_total else 1.0,
        "short_intervals": short,
        "intervals": len(requirement),
        "extra_agent_intervals": sum(
            max(0, actual - need) for actual, need in zip(curve_agents, requirement)
        ),
    }


def shrinkage_from_history(summary: HistorySummary) -> float:
    """Measured shrinkage, clamped to a range where planning still makes sense.

    A brand-new environment reports 0 occupied seconds and therefore 100%
    shrinkage; planning on that would be absurd, so the value is clamped and the
    caller can see both the measurement and the clamp.
    """
    if summary.paid_seconds <= 0:
        return 0.0
    return min(0.6, max(0.0, summary.shrinkage))


def distribution(requirements: list[IntervalRequirement]) -> dict[str, float]:
    """Descriptive statistics of the paid curve — the console's sparkline data."""
    if not requirements:
        return {"min": 0.0, "median": 0.0, "mean": 0.0, "max": 0.0, "p90": 0.0}
    values = sorted(item.paid_agents for item in requirements)
    p90_index = min(len(values) - 1, int(round(0.9 * (len(values) - 1))))
    return {
        "min": float(values[0]),
        "median": float(statistics.median(values)),
        "mean": round(statistics.fmean(values), 4),
        "max": float(values[-1]),
        "p90": float(values[p90_index]),
    }


def default_target(settings: PlatformV2Settings | None = None) -> StaffingTarget:
    cfg = settings or get_plat2_settings()
    # Every one of these is a deployment setting, not a constant in the code: the
    # service level and occupancy cap are commercial decisions, and a hardcoded 90/20
    # is how a tenant ends up planned against a target nobody agreed to.
    return StaffingTarget(
        service_level=float(cfg.wfm_service_level),
        answer_seconds=float(cfg.wfm_answer_seconds),
        max_occupancy=float(cfg.wfm_max_occupancy),
        shrinkage=float(cfg.wfm_shrinkage),
        average_handle_seconds=float(cfg.wfm_average_handle_seconds),
        model=str(cfg.wfm_staffing_model),
        average_patience_seconds=float(cfg.wfm_average_patience_seconds),
    )


def horizon_dates(start: date, days: int, timezone_name: str) -> list[date]:
    """Local dates covered by a horizon — DST-safe because it counts days, not hours."""
    tenant_zone(timezone_name)
    return [start + timedelta(days=offset) for offset in range(max(1, days))]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "IntervalRequirement",
    "MIN_PRODUCTIVE_AGENTS",
    "StaffingPlan",
    "StaffingTarget",
    "coverage_slots",
    "default_target",
    "distribution",
    "horizon_dates",
    "now_utc",
    "plan_from_forecast",
    "plan_from_history",
    "requirement_for_point",
    "service_level_of",
    "shrinkage_from_history",
    "smooth",
    "window_requirements",
]
