"""Intraday: is today running to plan, and what should we do about the rest of it?

This is the module a supervisor opens at 11am, so it answers in operational
language rather than statistics:

* **Where are we against plan, right now?** Actual offered volume per interval
  versus the published forecast, with the variance both absolute and relative.
* **Is the rest of the day still likely?** The remaining forecast is scaled by the
  ratio actually observed so far, but only when the sample is big enough to mean
  something — a two-interval ratio is noise, and re-planning on noise is how a
  team ends up chasing its own tail.
* **Do we need help?** The agent delta per remaining interval comes from
  ``wfm.staffing`` at the *re-forecast* volume minus what the rota already has, so
  "add two agents at 14:00" is a number the supervisor can act on.

The scaling is capped (``MAX_SCALE``) and shrunk toward 1.0 by sample size, which
is the standard bias/variance trade and the reason this module does not simply
multiply by the ratio it saw.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.platform_v2_models import WfmForecast, WfmIntervalHistory, WfmShift
from app.platform_v2.wfm.forecast import ForecastPoint, points_from_payload
from app.platform_v2.wfm.history import tenant_zone
from app.platform_v2.wfm.staffing import StaffingTarget, requirement_for_point

#: A day that is running 3x plan is not a plan problem, it is an incident.
MAX_SCALE = 3.0
MIN_SCALE = 0.33
#: Intervals needed before the observed ratio outvotes the forecast.
RELIABLE_SAMPLES = 4


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class IntervalComparison:
    interval_start: datetime
    forecast: float
    actual: float | None
    passed: bool

    @property
    def variance(self) -> float | None:
        return None if self.actual is None else self.actual - self.forecast

    @property
    def variance_ratio(self) -> float | None:
        if self.actual is None or self.forecast <= 0:
            return None
        return (self.actual - self.forecast) / self.forecast

    def as_dict(self) -> dict[str, Any]:
        return {
            "interval_start": self.interval_start.isoformat(),
            "forecast": round(self.forecast, 3),
            "actual": None if self.actual is None else round(self.actual, 3),
            "variance": None if self.variance is None else round(self.variance, 3),
            "variance_ratio": (
                None if self.variance_ratio is None else round(self.variance_ratio, 4)
            ),
            "passed": self.passed,
        }


@dataclass
class IntradaySnapshot:
    queue_key: str
    as_of: datetime
    intervals: list[IntervalComparison]
    scale: float
    sample_count: int
    remaining: list[dict[str, Any]]

    @property
    def elapsed_forecast(self) -> float:
        return sum(item.forecast for item in self.intervals if item.passed)

    @property
    def elapsed_actual(self) -> float:
        return sum(item.actual or 0.0 for item in self.intervals if item.passed)

    def as_dict(self) -> dict[str, Any]:
        elapsed_forecast = self.elapsed_forecast
        return {
            "queue_key": self.queue_key,
            "as_of": self.as_of.isoformat(),
            "intervals": [item.as_dict() for item in self.intervals],
            "elapsed_forecast": round(elapsed_forecast, 3),
            "elapsed_actual": round(self.elapsed_actual, 3),
            "variance_ratio": (
                None
                if elapsed_forecast <= 0
                else round((self.elapsed_actual - elapsed_forecast) / elapsed_forecast, 4)
            ),
            "scale": round(self.scale, 4),
            "sample_count": self.sample_count,
            "remaining": self.remaining,
        }


def observed_scale(
    pairs: list[tuple[float, float]], *, reliable_samples: int = RELIABLE_SAMPLES
) -> float:
    """How far today is deviating, shrunk toward 1.0 when the sample is thin.

    Weighting is ``n / (n + RELIABLE_SAMPLES)``: with one interval observed the
    forecast keeps three quarters of the vote; with twelve it keeps a quarter.
    Clamped so a marketing blast cannot ask for ten times the staff.
    """
    usable = [(forecast, actual) for forecast, actual in pairs if forecast > 0]
    if not usable:
        return 1.0
    forecast_total = sum(forecast for forecast, _ in usable)
    actual_total = sum(actual for _, actual in usable)
    raw = actual_total / forecast_total if forecast_total else 1.0
    weight = len(usable) / (len(usable) + reliable_samples)
    blended = 1.0 + (raw - 1.0) * weight
    return min(MAX_SCALE, max(MIN_SCALE, blended))


def compare_points(
    points: list[ForecastPoint],
    actuals: dict[datetime, float],
    *,
    as_of: datetime,
) -> list[IntervalComparison]:
    comparisons: list[IntervalComparison] = []
    for point in points:
        actual = actuals.get(point.interval_start)
        comparisons.append(
            IntervalComparison(
                interval_start=point.interval_start,
                forecast=point.expected,
                actual=actual,
                passed=point.interval_start < as_of,
            )
        )
    comparisons.sort(key=lambda item: item.interval_start)
    return comparisons


def remaining_plan(
    points: list[ForecastPoint],
    *,
    as_of: datetime,
    scale: float,
    target: StaffingTarget,
    scheduled_agents: int | None = None,
) -> list[dict[str, Any]]:
    """Re-forecast the rest of the horizon and say how many more people are needed."""
    rows: list[dict[str, Any]] = []
    for point in points:
        if point.interval_start <= as_of:
            continue
        volume = point.expected * scale
        requirement = requirement_for_point(point, target, volume=volume)
        need = requirement.paid_agents
        delta = None if scheduled_agents is None else need - scheduled_agents
        rows.append(
            {
                "interval_start": point.interval_start.isoformat(),
                "forecast": round(volume, 3),
                "paid_agents": need,
                "productive_agents": requirement.productive_agents,
                "delta": delta,
            }
        )
    return rows


async def actuals_for_day(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    queue_key: str,
    start: datetime,
    end: datetime,
) -> dict[datetime, float]:
    rows = (
        await session.execute(
            select(WfmIntervalHistory).where(
                WfmIntervalHistory.tenant_id == tenant_id,
                WfmIntervalHistory.environment_id == environment_id,
                WfmIntervalHistory.queue_key == queue_key,
                WfmIntervalHistory.interval_start >= start,
                WfmIntervalHistory.interval_start < end,
                WfmIntervalHistory.is_gap.is_(False),
            )
        )
    ).scalars()
    return {_as_utc(row.interval_start): float(row.offered or 0) for row in rows}


async def scheduled_agents_now(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    at: datetime,
) -> int:
    """People whose shift covers ``at`` — from the rota, not from agent states.

    Adherence owns "who is actually logged in"; this is the plan side of the same
    question, and keeping them apart is what lets the supervisor see the gap.
    """
    rows = (
        await session.execute(
            select(WfmShift).where(
                WfmShift.tenant_id == tenant_id,
                WfmShift.environment_id == environment_id,
                WfmShift.status.in_(("scheduled", "published", "swapped")),
                WfmShift.starts_at <= at,
                WfmShift.ends_at > at,
            )
        )
    ).scalars()
    return len(list(rows))


async def latest_forecast(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    queue_key: str,
    at: datetime,
) -> WfmForecast | None:
    """The newest forecast whose horizon covers ``at``."""
    rows = (
        await session.execute(
            select(WfmForecast)
            .where(
                WfmForecast.tenant_id == tenant_id,
                WfmForecast.environment_id == environment_id,
                WfmForecast.queue_key == queue_key,
            )
            .order_by(WfmForecast.generated_at.desc())
            .limit(20)
        )
    ).scalars()
    for row in rows:
        points = points_from_payload(row.points)
        if not points:
            continue
        if min(point.interval_start for point in points) <= at <= max(
            point.interval_start for point in points
        ) + timedelta(minutes=points[0].interval_minutes):
            return row
    return None


async def snapshot(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    queue_key: str = "",
    as_of: datetime | None = None,
    target: StaffingTarget | None = None,
    zone_name: str = "UTC",
) -> IntradaySnapshot:
    """Everything the intraday board shows, in one pass."""
    moment = _as_utc(as_of) if as_of else _now()
    forecast = await latest_forecast(
        session,
        tenant_id=tenant_id,
        environment_id=environment_id,
        queue_key=queue_key,
        at=moment,
    )
    points = points_from_payload(forecast.points) if forecast else []
    if not points:
        return IntradaySnapshot(queue_key, moment, [], 1.0, 0, [])
    zone = tenant_zone(zone_name)
    day_start = (
        moment.astimezone(zone)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(timezone.utc)
    )
    day_end = (day_start.astimezone(zone) + timedelta(days=1)).astimezone(timezone.utc)
    actuals = await actuals_for_day(
        session,
        tenant_id=tenant_id,
        environment_id=environment_id,
        queue_key=queue_key,
        start=day_start,
        end=day_end,
    )
    comparisons = compare_points(points, actuals, as_of=moment)
    pairs = [
        (item.forecast, item.actual)
        for item in comparisons
        if item.passed and item.actual is not None
    ]
    scale = observed_scale([(f, a) for f, a in pairs if a is not None])
    schedules_now = await scheduled_agents_now(
        session, tenant_id=tenant_id, environment_id=environment_id, at=moment
    )
    remaining = remaining_plan(
        points,
        as_of=moment,
        scale=scale,
        target=target or StaffingTarget(),
        scheduled_agents=schedules_now,
    )
    return IntradaySnapshot(
        queue_key=queue_key,
        as_of=moment,
        intervals=comparisons,
        scale=scale,
        sample_count=len(pairs),
        remaining=remaining,
    )


def alert_level(snapshot: IntradaySnapshot, *, tolerance: float = 0.15) -> str:
    """``ok`` / ``watch`` / ``act`` for the board's colour.

    ``act`` when the day is off plan beyond tolerance *and* the remaining plan
    cannot absorb it (the re-forecast adds agents nobody has). This is a
    presentation helper, not a policy engine: the thresholds are arguments.
    """
    ratio = snapshot.as_dict()["variance_ratio"]
    if ratio is None:
        return "ok"
    if abs(ratio) <= tolerance:
        return "ok"
    short = any((row["delta"] or 0) > 0 for row in snapshot.remaining)
    if abs(ratio) > tolerance * 2 and short:
        return "act"
    return "watch"


__all__ = [
    "IntradaySnapshot",
    "IntervalComparison",
    "MAX_SCALE",
    "MIN_SCALE",
    "RELIABLE_SAMPLES",
    "actuals_for_day",
    "alert_level",
    "compare_points",
    "latest_forecast",
    "observed_scale",
    "remaining_plan",
    "scheduled_agents_now",
    "snapshot",
]
