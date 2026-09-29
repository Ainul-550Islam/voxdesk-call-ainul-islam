"""Validated anomaly observations, configuration, and result contracts."""

from __future__ import annotations

import datetime as dt
import math
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.specialized_agents.enums import Severity


class AnomalyModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Observation(BaseModel):
    metric: str = Field(min_length=1, max_length=200)
    observed_at: dt.datetime
    value: float

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("observation value must be finite")
        return value


class DetectorConfig(BaseModel):
    detector: str = Field(default="z_score", pattern=r"^(z_score|median_mad|rolling_baseline|sudden_change)$")
    threshold: float = Field(default=3.0, gt=0.0)
    minimum_history: int = Field(default=5, ge=2, le=10_000)
    rolling_window: int = Field(default=5, ge=2, le=10_000)
    sudden_change_threshold: float = Field(default=0.5, gt=0.0)
    severity_high_multiplier: float = Field(default=1.5, gt=1.0)
    severity_critical_multiplier: float = Field(default=2.0, gt=1.0)

    @model_validator(mode="after")
    def valid_thresholds(self):
        if self.severity_critical_multiplier < self.severity_high_multiplier:
            raise ValueError("critical severity multiplier must be >= high severity multiplier")
        if self.detector == "rolling_baseline" and self.rolling_window >= self.minimum_history:
            raise ValueError("rolling_window must be smaller than minimum_history")
        return self


class AnomalyResult(AnomalyModel):
    metric: str
    observation_time: dt.datetime
    observed_value: float
    baseline: float | None
    deviation: float | None
    detector: str
    threshold: float
    severity: str
    confidence: float
    quality_state: str
    explanation: str
    anomalous: bool
    review_required: bool


class AnomalyAnalysisRequest(BaseModel):
    environment_id: uuid.UUID
    model_version_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    observations: list[Observation] = Field(min_length=1, max_length=10_000)
    configuration: DetectorConfig
    risk_tier: str = Field(default="moderate", min_length=1, max_length=24)
    agent_version: str = Field(default="1.0.0", min_length=1, max_length=100)
    metric: str | None = Field(default=None, max_length=200)


class AnomalyAnalysisResult(AnomalyModel):
    execution_id: uuid.UUID | None = None
    metric: str
    detector: str
    quality_state: str
    results: list[AnomalyResult]
    review_required: bool
    methodology: str
    configuration: dict[str, Any]


SEVERITY_ORDER = {
    Severity.INFO.value: 0,
    Severity.LOW.value: 1,
    Severity.MODERATE.value: 2,
    Severity.HIGH.value: 3,
    Severity.CRITICAL.value: 4,
}
