"""Governed forecast normalization over the existing analytics algorithms."""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from app.analytics.forecast import FORECAST_METHODS, UsagePoint, project_usage
from app.governance.hashing import sha256_hex
from app.specialized_agents.enums import QualityState, ReviewState
from app.specialized_agents.exceptions import DataValidationError


class ForecastingService:
    """No statistical algorithm is implemented here; all calculations delegate."""

    def analyze(self, *, points: list[UsagePoint], method: str, horizon: int, window: int = 3, alpha: float = 0.5, series_reference: str | None = None) -> dict[str, Any]:
        if method not in FORECAST_METHODS:
            raise DataValidationError("forecast method is unsupported")
        if not 1 <= horizon <= 365:
            raise DataValidationError("horizon must be between 1 and 365")
        if not points or len(points) > 100_000:
            raise DataValidationError("series must contain between 1 and 100000 points")
        periods = [point.period for point in points]
        if any(not period for period in periods) or len(set(periods)) != len(periods):
            raise DataValidationError("series period labels must be non-empty and unique")
        try:
            date_periods = [date.fromisoformat(period) for period in periods]
        except ValueError:
            date_periods = []
        if date_periods and any(right <= left for left, right in zip(date_periods, date_periods[1:])):
            raise DataValidationError("date-based series periods must be strictly increasing")
        if not all(math.isfinite(float(point.value)) for point in points):
            raise DataValidationError("series values must be finite")
        projections = project_usage(points, horizon, method, window=window, alpha=alpha)
        values = [{"period": point.period, "value": float(point.value)} for point in points]
        series_hash = sha256_hex(values)
        enough_data = len(points) >= (2 if method == "linear" else max(1, window if method == "moving_average" else 2))
        residual_available = method == "linear" and len(points) >= 3
        interval_available = bool(projections) and residual_available
        normalized = []
        for projection in projections:
            normalized.append({
                "step": projection.step,
                "period": projection.label,
                "value": projection.value,
                "lower": projection.lower if interval_available else None,
                "upper": projection.upper if interval_available else None,
                "interval_status": "available" if interval_available else "NOT_AVAILABLE",
            })
        quality = QualityState.VERIFIED.value if enough_data else QualityState.NOT_AVAILABLE.value
        review = quality != QualityState.VERIFIED.value
        return {
            "operation": "forecast",
            "method": method,
            "method_metadata": {"implementation": "app.analytics.forecast.project_usage", "version": "existing"},
            "series_reference": series_reference or f"sha256:{series_hash}",
            "series_fingerprint": series_hash,
            "horizon": horizon,
            "projections": normalized,
            "interval_method": "existing analytics forecast spread" if interval_available else "NOT_AVAILABLE",
            "data_quality": quality,
            "observation_count": len(points),
            "residual_error": {"status": "available" if residual_available else "NOT_AVAILABLE", "method": "linear residual sample spread" if residual_available else None},
            "review_required": review,
            "review_state": ReviewState.REQUIRED.value if review else ReviewState.NOT_REQUIRED.value,
            "disclaimer": "Forecasts are estimates from the supplied series and do not guarantee future outcomes.",
        }

    def backtest(
        self,
        *,
        points: list[UsagePoint],
        method: str,
        test_size: int,
        window: int = 3,
        alpha: float = 0.5,
        series_reference: str | None = None,
    ) -> dict[str, Any]:
        """Score the *existing* forecast engine by holding out the series tail.

        The last ``test_size`` observations are removed, ``project_usage`` is
        asked to forecast exactly that many steps from the remaining history,
        and the result is compared with what actually happened. No new
        algorithm is introduced: the same function that serves live forecasts
        is the one being measured, so the error figures describe the product
        rather than a test-only reimplementation.

        ``rmse`` is ``None`` -- never a fabricated zero -- when the held-out
        actuals are not finite. ``mape`` is ``None`` when any held-out actual is
        zero, because division by zero would make the metric meaningless.
        """
        if method not in FORECAST_METHODS:
            raise DataValidationError("forecast method is unsupported")
        if not points or len(points) > 100_000:
            raise DataValidationError("series must contain between 1 and 100000 points")
        periods = [point.period for point in points]
        if any(not period for period in periods) or len(set(periods)) != len(periods):
            raise DataValidationError("series period labels must be non-empty and unique")
        values = [float(point.value) for point in points]
        if not all(math.isfinite(value) for value in values):
            raise DataValidationError("series values must be finite")
        # One observation must survive for the engine to condition on.
        if not 1 <= test_size < len(points):
            raise DataValidationError(
                "test_size must be at least 1 and smaller than the series length"
            )

        train = points[: len(points) - test_size]
        actual = values[len(points) - test_size :]
        projections = project_usage(train, test_size, method, window=window, alpha=alpha)
        predicted = [float(projection.value) for projection in projections]
        if len(predicted) != len(actual):
            raise DataValidationError("the forecast engine did not cover the held-out window")

        paired = [
            (index, expected, observed)
            for index, (expected, observed) in enumerate(zip(predicted, actual), start=1)
        ]
        residuals = [expected - observed for _, expected, observed in paired]
        finite_actuals = all(math.isfinite(value) for value in actual)
        rmse = (
            math.sqrt(sum(residual * residual for residual in residuals) / len(residuals))
            if finite_actuals
            else None
        )
        zero_actuals = any(value == 0 for value in actual)
        mape = (
            (100.0 * sum(abs(expected - observed) / abs(observed) for _, expected, observed in paired) / len(paired))
            if finite_actuals and not zero_actuals
            else None
        )

        series_hash = sha256_hex(
            [{"period": point.period, "value": float(point.value)} for point in points]
        )
        # A residual sample this small cannot carry a confidence interval, so it
        # is disclosed as unavailable instead of being invented from two points.
        interval_available = len(train) >= 2 and method == "linear"
        return {
            "operation": "backtest",
            "method": method,
            "method_metadata": {"implementation": "app.analytics.forecast.project_usage", "version": "existing"},
            "series_reference": series_reference or f"sha256:{series_hash}",
            "series_fingerprint": series_hash,
            "observation_count": len(points),
            "train_size": len(train),
            "test_size": test_size,
            "horizon": test_size,
            "rmse": rmse,
            "mape": mape,
            "interval_method": "NOT_AVAILABLE",
            "residual_error": {
                "status": "available" if rmse is not None else "NOT_AVAILABLE",
                "method": "held-out absolute error",
                "residual_count": len(residuals),
            },
            "comparison": [
                {"step": step, "period": points[len(train) + step - 1].period,
                 "predicted": expected, "actual": observed,
                 "residual": expected - observed}
                for step, expected, observed in paired
            ],
            "data_quality": (
                QualityState.VERIFIED.value if interval_available else QualityState.NOT_AVAILABLE.value
            ),
            "review_required": not interval_available,
            "review_state": (
                ReviewState.NOT_REQUIRED.value if interval_available else ReviewState.REQUIRED.value
            ),
            "disclaimer": "Backtest error is measured on held-out history and does not guarantee future outcomes.",
        }
