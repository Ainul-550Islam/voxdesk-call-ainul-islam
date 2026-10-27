"""Forecast accuracy, measured — never asserted.

Every number in this module is computed from two stored things: the points that
were published in a ``wfm_forecasts`` document and the intervals that actually
happened in ``wfm_interval_history``. There is no target, no baseline and no
accepted range anywhere in the schema or the code, because "we are 94% accurate"
is a claim a system should have to re-earn every week.

Definitions used (and used consistently by the API and the console):

* **WAPE** — ``sum|actual - forecast| / sum|actual|``. The workhorse: unlike MAPE
  it does not explode on a quiet Sunday, and it is volume-weighted, which is what
  a roster cares about.
* **MAPE** — mean of per-interval percentage errors where the actual is non-zero.
  Reported because vendors quote it, guarded because it is unstable.
* **Bias** — ``sum(forecast - actual) / sum|actual|``. Positive means the model
  over-staffs. This is the number that decides whether the rota is expensive or
  short, so it is signed and never folded into an absolute.
* **MAE** — mean absolute error in calls per interval, the only metric whose unit
  a hiring manager can read directly.

Intervals marked ``is_gap`` are excluded: there was no coverage, so there was no
volume to be wrong about.
"""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.platform_v2_models import WfmForecast, WfmForecastAccuracy, WfmIntervalHistory
from app.platform_v2.errors import Conflict, ValidationFailed
from app.platform_v2.wfm.forecast import ForecastPoint, points_from_payload


@dataclass(frozen=True)
class IntervalError:
    interval_start: datetime
    expected: float
    actual: float

    @property
    def error(self) -> float:
        return self.expected - self.actual

    @property
    def absolute_error(self) -> float:
        return abs(self.error)

    @property
    def percentage_error(self) -> float | None:
        """Absolute error as a **fraction** of the actual (0.5 = 50% off).

        A fraction, not a percentage, so MAPE, WAPE and bias share one scale. Mixing
        the two — MAPE in percent, WAPE as a fraction — is how a dashboard ends up
        reporting "MAPE 12" next to "WAPE 0.12" and calling both 12%.
        """
        if self.actual == 0:
            return None
        return self.absolute_error / abs(self.actual)

    def as_dict(self) -> dict[str, object]:
        return {
            "interval_start": self.interval_start.isoformat(),
            "expected": round(self.expected, 3),
            "actual": round(self.actual, 3),
            "error": round(self.error, 3),
            "absolute_error": round(self.absolute_error, 3),
            "percentage_error": (
                None
                if self.percentage_error is None
                else round(self.percentage_error, 4)
            ),
        }


@dataclass(frozen=True)
class AccuracyMetrics:
    """The four metrics, plus the counts that make them interpretable."""

    wape: float
    mape: float
    bias: float
    mae: float
    sample_count: int
    total_actual: float
    total_forecast: float
    intervals: list[IntervalError] = field(default_factory=list)

    @property
    def scored(self) -> bool:
        """False when the window held no volume at all — nothing was measured."""
        return self.sample_count > 0 and self.total_actual > 0

    def as_dict(self, *, include_intervals: bool = False) -> dict[str, object]:
        body: dict[str, object] = {
            "wape": round(self.wape, 4),
            "mape": round(self.mape, 4),
            "bias": round(self.bias, 4),
            "mae": round(self.mae, 4),
            "sample_count": self.sample_count,
            "total_actual": round(self.total_actual, 3),
            "total_forecast": round(self.total_forecast, 3),
            "scored": self.scored,
        }
        if include_intervals:
            body["per_interval"] = [item.as_dict() for item in self.intervals]
        return body


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def pair_points(
    points: list[ForecastPoint],
    actuals: list[WfmIntervalHistory],
) -> list[IntervalError]:
    """Match forecast points to realised intervals on the exact interval start.

    Exact matching is intentional. Interpolating a 15-minute forecast onto a
    30-minute actual (or the reverse) silently invents volume, and a metric built
    on invented volume is worse than no metric.
    """
    # Both sides are normalised to UTC before matching. A driver that hands back
    # naive datetimes (SQLite in tests, or any timezone-naive column) would otherwise
    # match nothing at all and report a perfectly-scored empty window.
    actual_by_start = {
        _as_utc(row.interval_start): float(row.offered or 0)
        for row in actuals
        if not row.is_gap
    }
    pairs: list[IntervalError] = []
    for point in points:
        actual = actual_by_start.get(_as_utc(point.interval_start))
        if actual is None:
            continue
        pairs.append(
            IntervalError(
                interval_start=point.interval_start,
                expected=float(point.expected),
                actual=actual,
            )
        )
    pairs.sort(key=lambda item: item.interval_start)
    return pairs


def compute_metrics(pairs: list[IntervalError]) -> AccuracyMetrics:
    """Aggregate paired errors into WAPE/MAPE/bias/MAE.

    A window with no realised volume returns zeros *and* ``sample_count`` as it
    is, so a caller can tell "perfect" from "unmeasured".
    """
    if not pairs:
        return AccuracyMetrics(0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, [])
    total_actual = sum(abs(item.actual) for item in pairs)
    total_forecast = sum(item.expected for item in pairs)
    absolute_error = sum(item.absolute_error for item in pairs)
    signed_error = sum(item.error for item in pairs)
    percentages = [item.percentage_error for item in pairs if item.percentage_error is not None]
    wape = absolute_error / total_actual if total_actual else 0.0
    bias = signed_error / total_actual if total_actual else 0.0
    mae = statistics.fmean([item.absolute_error for item in pairs])
    mape = statistics.fmean(percentages) if percentages else 0.0
    return AccuracyMetrics(
        wape=wape,
        mape=mape,
        bias=bias,
        mae=mae,
        sample_count=len(pairs),
        total_actual=total_actual,
        total_forecast=total_forecast,
        intervals=pairs,
    )


def evaluate(
    points: list[ForecastPoint], actuals: list[WfmIntervalHistory]
) -> AccuracyMetrics:
    """Convenience wrapper: pair, then aggregate."""
    return compute_metrics(pair_points(points, actuals))


async def actuals_between(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    queue_key: str,
    start: datetime,
    end: datetime,
) -> list[WfmIntervalHistory]:
    stmt = (
        select(WfmIntervalHistory)
        .where(
            WfmIntervalHistory.tenant_id == tenant_id,
            WfmIntervalHistory.environment_id == environment_id,
            WfmIntervalHistory.queue_key == queue_key,
            WfmIntervalHistory.interval_start >= start,
            WfmIntervalHistory.interval_start < end,
        )
        .order_by(WfmIntervalHistory.interval_start)
    )
    return list((await session.execute(stmt)).scalars().all())


async def evaluate_forecast(
    session: AsyncSession,
    forecast: WfmForecast,
    *,
    evaluated_to: datetime | None = None,
    commit: bool = True,
) -> WfmForecastAccuracy:
    """Score one published forecast against what actually happened.

    Only the part of the horizon that has already elapsed can be scored; asking
    for the future would produce a flattering number built from zeros.
    """
    points = points_from_payload(forecast.points)
    if not points:
        raise ValidationFailed("forecast has no points to score")
    first = min(point.interval_start for point in points)
    # `last` is a *start*, so the horizon ends one interval later. Using the start as
    # the end silently dropped the final interval from every score.
    last_end = max(point.interval_start + timedelta(minutes=point.interval_minutes) for point in points)
    now = datetime.now(timezone.utc)
    horizon_end = min(evaluated_to or now, last_end)
    if horizon_end <= first:
        raise ValidationFailed("no elapsed part of the horizon to score yet")
    scored_points = [point for point in points if point.interval_start < horizon_end]
    actuals = await actuals_between(
        session,
        tenant_id=forecast.tenant_id,
        environment_id=forecast.environment_id,
        queue_key=forecast.queue_key,
        start=first,
        end=horizon_end,
    )
    metrics = evaluate(scored_points, actuals)
    if not metrics.scored:
        # Storing a zero-error row here would make an unscored forecast look like a
        # perfect one: `trend()` would read 0.0 as the latest WAPE and call the model
        # "improving". Nothing was measured, so nothing is recorded.
        raise Conflict(
            "no realised volume in the elapsed window; there is nothing to score yet",
            detail={
                "evaluated_from": first.isoformat(),
                "evaluated_to": horizon_end.isoformat(),
                "scored_intervals": metrics.sample_count,
            },
        )
    existing = (
        await session.execute(
            select(WfmForecastAccuracy).where(
                WfmForecastAccuracy.tenant_id == forecast.tenant_id,
                WfmForecastAccuracy.forecast_id == forecast.id,
                WfmForecastAccuracy.evaluated_from == first,
            )
        )
    ).scalar_one_or_none()
    row = existing or WfmForecastAccuracy(
        tenant_id=forecast.tenant_id,
        environment_id=forecast.environment_id,
        queue_key=forecast.queue_key,
        forecast_id=forecast.id,
        evaluated_from=first,
    )
    row.evaluated_to = horizon_end
    row.wape = round(metrics.wape, 4)
    row.mape = round(metrics.mape, 4)
    row.bias = round(metrics.bias, 4)
    row.mae = round(metrics.mae, 4)
    row.sample_count = metrics.sample_count
    row.per_interval = {
        "metrics": metrics.as_dict(),
        "intervals": [item.as_dict() for item in metrics.intervals],
        "model_version": forecast.model_version,
        "method": forecast.method,
    }
    row.computed_at = now
    session.add(row)
    if commit:
        await session.commit()
    return row


async def latest_accuracy(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    queue_key: str,
    limit: int = 10,
) -> list[WfmForecastAccuracy]:
    """The most recent scored forecasts for a queue, newest first."""
    stmt = (
        select(WfmForecastAccuracy)
        .where(
            WfmForecastAccuracy.tenant_id == tenant_id,
            WfmForecastAccuracy.environment_id == environment_id,
            WfmForecastAccuracy.queue_key == queue_key,
        )
        .order_by(WfmForecastAccuracy.evaluated_to.desc())
        .limit(max(1, min(100, limit)))
    )
    return list((await session.execute(stmt)).scalars().all())


@dataclass(frozen=True)
class AccuracyTrend:
    """A shrinking or growing error over recent evaluations."""

    samples: int
    mean_wape: float
    last_wape: float
    direction: str

    def as_dict(self) -> dict[str, object]:
        return {
            "samples": self.samples,
            "mean_wape": round(self.mean_wape, 4),
            "last_wape": round(self.last_wape, 4),
            "direction": self.direction,
        }


def trend(rows: list[WfmForecastAccuracy]) -> AccuracyTrend:
    """Describe recent accuracy, oldest → newest, without inventing a threshold.

    ``direction`` is a comparison of the newest evaluation against the mean of
    the earlier ones; whether that is "good" depends on the queue, so the caller
    decides and this function only reports.
    """
    if not rows:
        return AccuracyTrend(0, 0.0, 0.0, "unknown")
    ordered = sorted(rows, key=lambda row: row.evaluated_to)
    wapes = [float(row.wape) for row in ordered]
    if len(wapes) == 1:
        return AccuracyTrend(1, wapes[0], wapes[0], "flat")
    earlier = statistics.fmean(wapes[:-1])
    slope = wapes[-1] - earlier
    # 5% of the earlier mean is the noise floor for a metric built on a handful
    # of intervals; anything smaller is called flat rather than a trend.
    threshold = max(0.005, abs(earlier) * 0.05)
    direction = "flat" if abs(slope) <= threshold else ("improving" if slope < 0 else "worsening")
    # `mean_wape` covers all samples (what a reader assumes); `direction` compares the
    # newest evaluation against the earlier ones only.
    return AccuracyTrend(len(wapes), statistics.fmean(wapes), wapes[-1], direction)


def blend_weight(metrics: AccuracyMetrics) -> float:
    """How much to trust this forecast when averaged with a sibling model.

    ``1 / (1 + WAPE)``: a forecast that missed by 10% keeps 91% of its weight, one
    that missed by 100% keeps 50%. Used by ``forecast.blend`` when two models are
    compared on the same window, so the choice is measured rather than argued.
    """
    return 1.0 / (1.0 + max(0.0, metrics.wape))


__all__ = [
    "AccuracyMetrics",
    "AccuracyTrend",
    "IntervalError",
    "actuals_between",
    "blend_weight",
    "compute_metrics",
    "evaluate",
    "evaluate_forecast",
    "latest_accuracy",
    "pair_points",
    "trend",
]
