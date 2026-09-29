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
