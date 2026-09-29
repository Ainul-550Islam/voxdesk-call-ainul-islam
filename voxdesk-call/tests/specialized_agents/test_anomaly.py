"""Behavioral tests for deterministic anomaly detectors."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.anomaly.detectors import detect, median_mad_detect, rolling_baseline_detect, z_score_detect
from app.anomaly.schemas import DetectorConfig, Observation
from app.specialized_agents.exceptions import DataValidationError


def observations(values):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [Observation(metric="latency", observed_at=start + timedelta(days=index), value=value) for index, value in enumerate(values)]


def test_normal_baseline_and_strong_z_score_deviation():
    normal = z_score_detect(observations([10, 11, 10, 9, 10]), DetectorConfig(minimum_history=5))
    assert normal[0].anomalous is False
    strong = z_score_detect(observations([10, 10, 10, 10, 100]), DetectorConfig(minimum_history=5))
    assert strong[0].anomalous is True
    assert strong[0].severity == "critical"
    assert strong[0].baseline == 10


def test_mad_and_rolling_detectors_are_reproducible():
    config = DetectorConfig(detector="median_mad", minimum_history=5)
    first = median_mad_detect(observations([10, 10, 11, 9, 100]), config)[0]
    second = median_mad_detect(observations([10, 10, 11, 9, 100]), config)[0]
    assert first.model_dump() == second.model_dump()
    rolling = rolling_baseline_detect(
        observations([10, 10, 10, 10, 30]),
        DetectorConfig(detector="rolling_baseline", minimum_history=5, rolling_window=3, threshold=2),
    )[0]
    assert rolling.anomalous is True


def test_insufficient_data_and_validation_fail_closed():
    result = detect(observations([1, 2]), DetectorConfig(minimum_history=5))
    assert result[0].quality_state == "insufficient_data"
    assert result[0].anomalous is False
    with pytest.raises(ValidationError):
        Observation(metric="latency", observed_at=datetime.now(timezone.utc), value=float("nan"))
    unordered = observations([1, 2, 3])
    unordered[1] = unordered[1].model_copy(update={"observed_at": unordered[0].observed_at - timedelta(days=1)})
    with pytest.raises(DataValidationError):
        detect(unordered, DetectorConfig(minimum_history=2))
    with pytest.raises(ValidationError):
        DetectorConfig(detector="rolling_baseline", minimum_history=5, rolling_window=5)
