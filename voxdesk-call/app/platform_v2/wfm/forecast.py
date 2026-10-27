"""Interval forecasting for workforce planning.

The model is deliberately small and inspectable, because a forecast nobody can
explain is a forecast nobody will staff against. Four effects do the work:

* **Interval of day.** The 09:15 spike repeats. It is not noise and it is not
  smoothed away, because smoothing a spike is how a rota ends up one agent short
  every morning at exactly the same minute.
* **Day of week.** Monday is not Sunday. Estimated per weekday.
* **Recent level.** Volume drifts, so the level is anchored on the last few weeks
  with exponentially decaying weight, not on the whole history.
* **Holiday overrides.** A date→factor map applied after the seasonal estimate and
  echoed back in the result, so a surprising Tuesday is explainable.

Multiplicative decomposition over *one interval*::

    expected = baseline_interval_volume * weekday_factor * slot_factor * level_factor

All three factors are ratios around 1.0, which is why they compose. Mixing a daily
total with an interval ratio — the obvious first attempt, and the one this file's
first draft got wrong — multiplies every number by the number of intervals in a day.

The prediction band widens with the horizon and never collapses to zero width: a
band of zero is read as certainty, and certainty about a call volume three weeks out
is a lie.

Nothing here reads a configured accuracy figure. Error is measured from stored
actuals by :mod:`app.platform_v2.wfm.accuracy`.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from app.platform_v2.errors import InsufficientHistory, ValidationFailed
from app.platform_v2.wfm.history import IntervalSlot, interval_grid, tenant_zone

#: Below this many complete days the multiplicative model is not identifiable.
MIN_DAYS_MULTIPLICATIVE = 21

#: Below this the forecast degrades to a slot profile scaled by a recent level.
MIN_DAYS_ADDITIVE = 3

#: Weekly half-life for the level anchor: rides out one bad week, still moves.
LEVEL_HALF_LIFE_DAYS = 7.0

#: Bounds on the multiplicative factors, so one freak day cannot create a factor of 9.
MIN_FACTOR = 0.2
MAX_FACTOR = 5.0

#: Prediction band floor and horizon widening, as fractions of the point estimate.
MIN_BAND_FRACTION = 0.05
BAND_GROWTH_PER_DAY = 0.012

#: Extra band on a weekday with almost no history: the estimate is real, the
#: confidence is not.
THIN_WEEKDAY_BAND = 0.15
THIN_WEEKDAY_SAMPLES = 3

MODEL_VERSION = "v1"
METHOD_SEASONAL = "seasonal_interval"
METHOD_ADDITIVE = "additive_interval"


@dataclass(frozen=True)
class ForecastPoint:
    """One forecast interval.

    ``low``/``high`` are the prediction band the staffing planner can choose to
    staff against; ``expected`` is the point estimate. Stored shape is
    :meth:`as_dict`, which is also what ``points_from_payload`` reads back, so a
    stored forecast can be re-planned years later without the model.
    """

    interval_start: datetime
    interval_minutes: int
    expected: float
    low: float
    high: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "interval_start": self.interval_start.isoformat(),
            "interval_minutes": self.interval_minutes,
            "expected": round(self.expected, 4),
            "low": round(self.low, 4),
            "high": round(self.high, 4),
        }


@dataclass
class ForecastResult:
    """A fitted forecast: the points plus everything needed to explain them."""

    points: list[ForecastPoint]
    timezone: str
    interval_minutes: int
    start_date: date
    horizon_days: int
    history_days: int
    method: str = METHOD_SEASONAL
    model_version: str = MODEL_VERSION
    generated_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    overrides: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> list[dict[str, Any]]:
        return [point.as_dict() for point in self.points]

    def as_dict(self) -> dict[str, Any]:
        return {
            "timezone": self.timezone,
            "interval_minutes": self.interval_minutes,
            "start_date": self.start_date.isoformat(),
            "horizon_days": self.horizon_days,
            "history_days": self.history_days,
            "method": self.method,
            "model_version": self.model_version,
            "generated_at": self.generated_at.isoformat(),
            "overrides": dict(self.overrides),
            "profile": dict(self.profile),
            "points": len(self.points),
            "total_expected": round(sum(point.expected for point in self.points), 4),
        }

    def for_date(self, day: date) -> list[ForecastPoint]:
        return [point for point in self.points if _local_day(point.interval_start, self.timezone) == day]

    def totals_by_day(self) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for point in self.points:
            totals[_local_day(point.interval_start, self.timezone).isoformat()] += point.expected
        return {key: round(value, 4) for key, value in sorted(totals.items())}

    def peak(self) -> ForecastPoint | None:
        return max(self.points, key=lambda point: point.expected) if self.points else None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _local_day(moment: datetime, zone_name: str) -> date:
    return _as_utc(moment).astimezone(tenant_zone(zone_name)).date()


def _clamp(value: float, low: float = MIN_FACTOR, high: float = MAX_FACTOR) -> float:
    return max(low, min(high, value))


def fit_profile(
    samples: Sequence[tuple[IntervalSlot, float]], *, zone_name: str = "UTC"
) -> dict[str, Any]:
    """Estimate level, weekday factors and slot factors from history.

    Everything is indexed by **local** wall clock in ``zone_name``: a platform that
    stores UTC intervals but rosters in Dhaka must learn that its 09:00 spike is at
    09:00 local. Keying the profile by UTC hour instead shifted every factor by the
    zone offset, and the resulting forecast was 40% low in exactly the hours that
    matter most.

    Returned as plain data so it can be stored in the forecast row and read back
    without a migration. Every factor is a ratio around 1.0 applied to
    ``baseline_interval_volume``.
    """
    if not samples:
        raise InsufficientHistory("no interval history to fit a forecast from")

    by_slot: dict[tuple[int, int], list[float]] = defaultdict(list)
    by_weekday: dict[int, list[float]] = defaultdict(list)
    by_day: dict[date, list[float]] = defaultdict(list)

    zone = tenant_zone(zone_name)
    for slot, volume in samples:
        local = _as_utc(slot.start).astimezone(zone)
        by_slot[(local.hour, local.minute)].append(float(volume))
        by_weekday[local.weekday()].append(float(volume))
        by_day[local.date()].append(float(volume))

    all_values = [value for values in by_weekday.values() for value in values]
    baseline = statistics.fmean(all_values) if all_values else 0.0
    days = len(by_day)
    if baseline <= 0 or days == 0:
        raise InsufficientHistory(
            "history has no volume to fit a forecast from",
            detail={"days": days, "baseline": round(baseline, 4)},
        )

    daily_totals = {day: sum(values) for day, values in by_day.items()}
    mean_daily = statistics.fmean(daily_totals.values()) or 1.0
    recent = sorted(daily_totals)[-max(7, min(28, len(daily_totals))):]
    recent_values = [daily_totals[day] for day in recent]
    level = _weighted_level(recent_values)
    # The denominator is the *same weighting* applied to the whole history, so the
    # weekly shape cancels and the factor measures drift only. Comparing against an
    # unweighted mean instead bakes in the calendar: a stationary week that ends on
    # a quiet Sunday comes out 3% low, every week, forever.
    history_level = _weighted_level([daily_totals[day] for day in sorted(daily_totals)]) or 1.0

    # A slot the queue was closed for has a factor of exactly zero. Clamping it to
    # the 0.2 floor would forecast demand at 03:00 in a business that shuts at 22:00,
    # and a planner would staff it.
    slot_factor = {}
    for (hour, minute), values in by_slot.items():
        mean = statistics.fmean(values)
        slot_factor[f"{hour:02d}:{minute:02d}"] = (
            0.0 if mean <= 0 else min(MAX_FACTOR, mean / baseline)
        )
    # Weekday factors are per-*interval* ratios: dividing an interval mean by a daily
    # total is a unit error that makes every day look 20x too quiet.
    weekday_factor = {}
    weekday_samples = {}
    for weekday in range(7):
        values = by_weekday.get(weekday, [])
        weekday_samples[str(weekday)] = len(values)
        weekday_factor[str(weekday)] = (
            _clamp(statistics.fmean(values) / baseline) if values else 1.0
        )

    return {
        "days": days,
        "baseline_interval_volume": round(baseline, 6),
        "level_factor": round(level / history_level, 6) if history_level else 1.0,
        "level_daily_volume": round(level, 4),
        "mean_daily_volume": round(mean_daily, 4),
        "slot_factor": {key: round(value, 6) for key, value in sorted(slot_factor.items())},
        "weekday_factor": weekday_factor,
        "weekday_samples": weekday_samples,
        "model": "multiplicative" if days >= MIN_DAYS_MULTIPLICATIVE else "additive",
    }


def _weighted_level(values: Sequence[float]) -> float:
    """Exponentially weighted level, newest last."""
    if not values:
        return 0.0
    decay = 0.5 ** (1.0 / LEVEL_HALF_LIFE_DAYS)
    weights = [decay ** (len(values) - 1 - index) for index in range(len(values))]
    total = sum(weights)
    return sum(value * weight for value, weight in zip(values, weights)) / total


def normalise_overrides(overrides: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Accept either ``{"2026-12-25": 1.4}`` or ``[{"date": ..., "factor": ..., "label": ...}]``.

    Stored in one shape so the audit trail of a forecast is uniform regardless of
    which API the console called.
    """
    normalised: dict[str, dict[str, Any]] = {}
    if overrides is None:
        return normalised
    items: Iterable[tuple[str, Any]]
    if isinstance(overrides, Mapping):
        items = overrides.items()
    else:
        items = [(str(item.get("date", "")), item) for item in overrides]
    for key, value in items:
        day = str(key)[:10]
        try:
            date.fromisoformat(day)
        except ValueError as exc:
            raise ValidationFailed(f"override date {key!r} is not an ISO date") from exc
        if isinstance(value, Mapping):
            factor = float(value.get("factor", 1.0))
            label = str(value.get("label", ""))[:120]
        else:
            factor = float(value)
            label = ""
        if not 0.0 < factor <= 10.0:
            raise ValidationFailed(
                f"override for {day} must be between 0 and 10",
                detail={"date": day, "factor": factor},
            )
        normalised[day] = {"factor": factor, "label": label}
    return normalised


def forecast_intervals(
    samples: Sequence[tuple[IntervalSlot, float]],
    *,
    zone_name: str,
    start_date: date,
    horizon_days: int = 14,
    interval_minutes: int = 15,
    overrides: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
    profile: dict[str, Any] | None = None,
    model_version: str = MODEL_VERSION,
) -> ForecastResult:
    """Forecast ``horizon_days`` local days from ``(slot, volume)`` history."""
    if horizon_days < 1 or horizon_days > 90:
        raise ValidationFailed(
            "forecast horizon must be between 1 and 90 days", detail={"horizon_days": horizon_days}
        )
    if interval_minutes not in (5, 10, 15, 20, 30, 60):
        raise ValidationFailed(
            "interval_minutes must be one of 5, 10, 15, 20, 30, 60",
            detail={"interval_minutes": interval_minutes},
        )
    fitted = profile or fit_profile(samples, zone_name=zone_name)
    if fitted["days"] < MIN_DAYS_ADDITIVE:
        raise InsufficientHistory(
            f"{fitted['days']} day(s) of history is not enough to forecast "
            f"(minimum {MIN_DAYS_ADDITIVE})",
            detail={"days": fitted["days"], "minimum": MIN_DAYS_ADDITIVE},
        )

    applied = normalise_overrides(overrides)
    baseline = float(fitted["baseline_interval_volume"])
    level_factor = float(fitted.get("level_factor", 1.0))
    multiplicative = fitted.get("model") == "multiplicative"
    slot_factor_by_key = {str(key): float(value) for key, value in fitted["slot_factor"].items()}

    points: list[ForecastPoint] = []
    for offset in range(horizon_days):
        day = start_date + timedelta(days=offset)
        # The day's own local grid, so the point count follows `interval_minutes`
        # instead of whatever granularity the history happened to be stored at, and
        # a DST day produces 23 or 25 hours of points rather than a fixed 24.
        day_slots = interval_grid(zone_name, day, day, interval_minutes)
        weekday_key = str(day.weekday())
        weekday_factor = float(fitted["weekday_factor"].get(weekday_key, 1.0))
        weekday_samples = int(fitted["weekday_samples"].get(weekday_key, 0))
        override = applied.get(day.isoformat())
        override_factor = float(override["factor"]) if override else 1.0

        band = MIN_BAND_FRACTION + BAND_GROWTH_PER_DAY * offset
        if weekday_samples < THIN_WEEKDAY_SAMPLES:
            band += THIN_WEEKDAY_BAND

        for day_slot in day_slots:
            start_local = day_slot.start.astimezone(tenant_zone(zone_name))
            slot_key = start_local.strftime("%H:%M")
            # A local slot the history never contained is one the queue was closed
            # for: zero demand, not a guess.
            slot_factor = slot_factor_by_key.get(slot_key, 0.0)
            expected = (
                baseline * weekday_factor * slot_factor * level_factor * override_factor
                if multiplicative
                else baseline * slot_factor * level_factor * override_factor
            )
            points.append(
                ForecastPoint(
                    interval_start=start_local.astimezone(UTC),
                    interval_minutes=interval_minutes,
                    expected=expected,
                    low=max(0.0, expected * (1.0 - band)),
                    high=expected * (1.0 + band),
                )
            )

    points.sort(key=lambda point: point.interval_start)
    return ForecastResult(
        points=points,
        timezone=zone_name,
        interval_minutes=interval_minutes,
        start_date=start_date,
        horizon_days=horizon_days,
        history_days=int(fitted["days"]),
        method=METHOD_SEASONAL if multiplicative else METHOD_ADDITIVE,
        model_version=model_version,
        overrides=applied,
        profile=fitted,
    )


def points_from_payload(payload: Sequence[Mapping[str, Any]]) -> list[ForecastPoint]:
    """Read stored forecast points back into :class:`ForecastPoint` objects.

    Tolerant of the key name ``volume`` so a row written by an earlier build still
    reads; strict about the timestamp, because a point without a time is not a
    forecast of anything.
    """
    points: list[ForecastPoint] = []
    for item in payload or []:
        start = item.get("interval_start") or item.get("start")
        if start is None:
            raise ValidationFailed("stored forecast point has no interval_start")
        moment = start if isinstance(start, datetime) else datetime.fromisoformat(str(start))
        expected = item.get("expected", item.get("volume", 0.0))
        points.append(
            ForecastPoint(
                interval_start=_as_utc(moment),
                interval_minutes=int(item.get("interval_minutes", 15) or 15),
                expected=float(expected or 0.0),
                low=float(item.get("low", expected) or 0.0),
                high=float(item.get("high", expected) or 0.0),
            )
        )
    points.sort(key=lambda point: point.interval_start)
    return points


def interval_key(moment: datetime, *, zone_name: str | None = None) -> tuple[int, int]:
    """(weekday, minutes-of-day) — the coordinate the seasonal table is indexed by.

    The profile is keyed by *local* wall clock (see :func:`fit_profile`), so this
    converts into the tenant's zone when one is given. Normalising to UTC first and
    then taking the clock time would hand back the UTC coordinate, which is the bug
    that shifted every factor by the zone offset.
    """
    if zone_name is not None:
        moment = _as_utc(moment).astimezone(tenant_zone(zone_name))
    elif moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.weekday(), moment.hour * 60 + moment.minute


def summarise(result: ForecastResult) -> dict[str, Any]:
    """Totals and peaks as the API and the console report them."""
    peak = result.peak()
    return {
        "intervals": len(result.points),
        "volume": round(sum(point.expected for point in result.points), 3),
        "peak_volume": round(peak.expected, 3) if peak else 0.0,
        "peak_at": peak.interval_start.isoformat() if peak else None,
        "band": [
            round(sum(point.low for point in result.points), 3),
            round(sum(point.high for point in result.points), 3),
        ],
        "daily": result.totals_by_day(),
    }


def poisson_band(volume: float, z: float = 1.28) -> tuple[float, float]:
    """Normal-approximation band for a count; exposed for tests and callers."""
    if volume <= 0:
        return 0.0, 0.0
    spread = z * math.sqrt(volume)
    return max(0.0, volume - spread), volume + spread
