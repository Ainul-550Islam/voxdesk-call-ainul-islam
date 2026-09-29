"""Deterministic statistical anomaly detectors."""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

from app.specialized_agents.enums import QualityState, Severity
from app.specialized_agents.exceptions import DataValidationError

from .schemas import AnomalyResult, DetectorConfig, Observation


def _validate_order(observations: Sequence[Observation]) -> None:
    if any(observations[index].observed_at > observations[index + 1].observed_at for index in range(len(observations) - 1)):
        raise DataValidationError("observations must be ordered by observed_at")


def _confidence(count: int, minimum_history: int) -> tuple[float, str]:
    if count < minimum_history:
        return 0.0, QualityState.INSUFFICIENT_DATA.value
    value = min(0.99, 0.5 + 0.5 * min(1.0, count / max(minimum_history * 2, 1)))
    return round(value, 4), QualityState.VERIFIED.value


def _severity(score: float, config: DetectorConfig) -> str:
    magnitude = abs(score)
    if magnitude >= config.threshold * config.severity_critical_multiplier:
        return Severity.CRITICAL.value
    if magnitude >= config.threshold * config.severity_high_multiplier:
        return Severity.HIGH.value
    if magnitude >= config.threshold:
        return Severity.MODERATE.value
    return Severity.INFO.value


def _result(
    observation: Observation,
    config: DetectorConfig,
    *,
    baseline: float | None,
    deviation: float | None,
    score: float | None,
    quality_state: str,
    explanation: str,
    anomalous: bool,
) -> AnomalyResult:
    confidence, derived_quality = _confidence(
        0 if quality_state == QualityState.INSUFFICIENT_DATA.value else 1,
        config.minimum_history,
    )
    if quality_state == QualityState.VERIFIED.value:
        confidence = min(0.99, max(confidence, 0.75))
    severity = _severity(score or 0.0, config) if anomalous else Severity.INFO.value
    return AnomalyResult(
        metric=observation.metric,
        observation_time=observation.observed_at,
        observed_value=observation.value,
        baseline=baseline,
        deviation=deviation,
        detector=config.detector,
        threshold=config.threshold,
        severity=severity,
        confidence=confidence,
        quality_state=quality_state if quality_state != QualityState.VERIFIED.value else derived_quality if derived_quality == QualityState.VERIFIED.value else quality_state,
        explanation=explanation,
        anomalous=anomalous,
        review_required=anomalous and severity in {Severity.HIGH.value, Severity.CRITICAL.value},
    )


def _metric_observations(observations: Sequence[Observation]) -> list[Observation]:
    if not observations:
        raise DataValidationError("observations are required")
    _validate_order(observations)
    metrics = {item.metric for item in observations}
    if len(metrics) != 1:
        raise DataValidationError("one detector run must contain one metric")
    return list(observations)


def z_score_detect(observations: Sequence[Observation], config: DetectorConfig) -> list[AnomalyResult]:
    items = _metric_observations(observations)
    if len(items) < config.minimum_history:
        return [_result(items[-1], config, baseline=None, deviation=None, score=None, quality_state=QualityState.INSUFFICIENT_DATA.value, explanation="Insufficient ordered history for z-score detection.", anomalous=False)]
    values = [item.value for item in items]
    baseline = statistics.mean(values[:-1])
    spread = statistics.stdev(values[:-1]) if len(values[:-1]) > 1 else 0.0
    if spread == 0.0:
        score = 0.0 if items[-1].value == baseline else math.inf
    else:
        score = (items[-1].value - baseline) / spread
    anomalous = abs(score) >= config.threshold
    return [_result(items[-1], config, baseline=baseline, deviation=items[-1].value - baseline, score=score if math.isfinite(score) else config.threshold * config.severity_critical_multiplier, quality_state=QualityState.VERIFIED.value, explanation=f"Latest value compared with the mean of the prior {len(values)-1} observations and their sample standard deviation.", anomalous=anomalous)]


def median_mad_detect(observations: Sequence[Observation], config: DetectorConfig) -> list[AnomalyResult]:
    items = _metric_observations(observations)
    if len(items) < config.minimum_history:
        return [_result(items[-1], config, baseline=None, deviation=None, score=None, quality_state=QualityState.INSUFFICIENT_DATA.value, explanation="Insufficient ordered history for median/MAD detection.", anomalous=False)]
    values = [item.value for item in items[:-1]]
    baseline = statistics.median(values)
    mad = statistics.median([abs(value - baseline) for value in values])
    if mad == 0.0:
        score = 0.0 if items[-1].value == baseline else math.inf
    else:
        score = 0.6745 * (items[-1].value - baseline) / mad
    anomalous = abs(score) >= config.threshold
    safe_score = score if math.isfinite(score) else config.threshold * config.severity_critical_multiplier
    return [_result(items[-1], config, baseline=baseline, deviation=items[-1].value - baseline, score=safe_score, quality_state=QualityState.VERIFIED.value, explanation="Latest value compared with the prior median and median absolute deviation using the configured threshold.", anomalous=anomalous)]


def rolling_baseline_detect(observations: Sequence[Observation], config: DetectorConfig) -> list[AnomalyResult]:
    items = _metric_observations(observations)
    if len(items) < config.minimum_history:
        return [_result(items[-1], config, baseline=None, deviation=None, score=None, quality_state=QualityState.INSUFFICIENT_DATA.value, explanation="Insufficient ordered history for rolling-baseline detection.", anomalous=False)]
    baseline_values = [item.value for item in items[-config.rolling_window - 1 : -1]]
    baseline = statistics.mean(baseline_values)
    spread = statistics.stdev(baseline_values) if len(baseline_values) > 1 else 0.0
    deviation = items[-1].value - baseline
    score = abs(deviation) / spread if spread else (0.0 if deviation == 0 else math.inf)
    anomalous = abs(deviation) >= config.threshold if spread == 0 else score >= config.threshold
    safe_score = score if math.isfinite(score) else config.threshold * config.severity_critical_multiplier
    return [_result(items[-1], config, baseline=baseline, deviation=deviation, score=safe_score, quality_state=QualityState.VERIFIED.value, explanation=f"Latest value compared with the preceding rolling window of {len(baseline_values)} observations.", anomalous=anomalous)]


def sudden_change_detect(observations: Sequence[Observation], config: DetectorConfig) -> list[AnomalyResult]:
    items = _metric_observations(observations)
    if len(items) < config.minimum_history:
        return [_result(items[-1], config, baseline=None, deviation=None, score=None, quality_state=QualityState.INSUFFICIENT_DATA.value, explanation="Insufficient ordered history for sudden-change detection.", anomalous=False)]
    previous = items[-2].value
    deviation = items[-1].value - previous
    denominator = abs(previous) if previous != 0 else 1.0
    ratio = abs(deviation) / denominator
    anomalous = ratio >= config.sudden_change_threshold
    score = ratio / config.sudden_change_threshold
    return [_result(items[-1], config, baseline=previous, deviation=deviation, score=score, quality_state=QualityState.VERIFIED.value, explanation="Latest value was compared with the immediately preceding observation using the configured relative-change threshold.", anomalous=anomalous)]


def detect(observations: Sequence[Observation], config: DetectorConfig) -> list[AnomalyResult]:
    detectors = {
        "z_score": z_score_detect,
        "median_mad": median_mad_detect,
        "rolling_baseline": rolling_baseline_detect,
        "sudden_change": sudden_change_detect,
    }
    try:
        detector = detectors[config.detector]
    except KeyError as exc:
        raise DataValidationError("unknown anomaly detector") from exc
    return detector(observations, config)
