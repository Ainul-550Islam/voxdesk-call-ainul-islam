"""Governed anomaly-analysis orchestration."""

from __future__ import annotations

from typing import Any

from app.specialized_agents.enums import QualityState, ReviewState
from app.specialized_agents.sources import SourceReference

from .detectors import detect
from .schemas import AnomalyAnalysisResult, DetectorConfig, Observation


class AnomalyService:
    def analyze(
        self,
        *,
        metric: str,
        observations: list[Observation],
        configuration: DetectorConfig,
    ) -> dict[str, Any]:
        if any(item.metric != metric for item in observations):
            from app.specialized_agents.exceptions import DataValidationError

            raise DataValidationError("all observations must use the requested metric")
        results = detect(observations, configuration)
        quality = (
            QualityState.INSUFFICIENT_DATA.value
            if any(item.quality_state == QualityState.INSUFFICIENT_DATA.value for item in results)
            else QualityState.VERIFIED.value
        )
        review_required = any(item.review_required for item in results)
        output = AnomalyAnalysisResult(
            metric=metric,
            detector=configuration.detector,
            quality_state=quality,
            results=results,
            review_required=review_required,
            methodology="Deterministic configured detector over ordered finite observations; thresholds and baseline calculations are stored with the result.",
            configuration=configuration.model_dump(mode="json"),
        )
        return {
            **output.model_dump(mode="json"),
            "review_state": ReviewState.REQUIRED.value if review_required else ReviewState.NOT_REQUIRED.value,
        }

    @staticmethod
    def source_references(metric: str, observations: list[Observation]) -> list[SourceReference]:
        import hashlib

        return [
            SourceReference(
                document_id=f"metric:{metric}",
                chunk_id=item.observed_at.isoformat(),
                source_title="Supplied analytical observation",
                content_fingerprint=hashlib.sha256(
                    f"{metric}|{item.observed_at.isoformat()}|{item.value}".encode()
                ).hexdigest(),
                retrieval_timestamp=item.observed_at,
            )
            for item in observations
        ]
