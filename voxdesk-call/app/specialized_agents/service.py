"""High-level orchestration helpers for specialized agents."""

from __future__ import annotations

import uuid
from typing import Any

from app.analytics.forecast import UsagePoint, project_usage
from app.governance.context import GovernanceScope

from .context import SpecializedAgentContext
from .enums import QualityState, ReviewState
from .executor import ExecutionOutcome, SpecializedExecutor, SourceResolver
from .registry import list_agents
from .sources import SourceReference


class SpecializedAgentService:
    def __init__(self, session, scope: GovernanceScope):
        self.session = session
        self.scope = scope
        self.executor = SpecializedExecutor(session, scope)

    @staticmethod
    def capabilities() -> list[dict[str, Any]]:
        return [definition.as_dict() for definition in list_agents()]

    def context(
        self,
        *,
        actor_id: uuid.UUID,
        request_id: str,
        trace_id: str,
        agent_type: str,
        agent_version: str,
        model_version_id: uuid.UUID,
        risk_tier: str,
        policy_version: int | None = None,
        locale: str | None = None,
        language: str | None = None,
        source_references: tuple[str, ...] = (),
    ) -> SpecializedAgentContext:
        return SpecializedAgentContext.from_scope(
            self.scope,
            actor_id=actor_id,
            request_id=request_id,
            trace_id=trace_id,
            agent_type=agent_type,
            agent_version=agent_version,
            model_version_id=model_version_id,
            risk_tier=risk_tier,
            policy_version=policy_version,
            locale=locale,
            language=language,
            source_references=source_references,
        )

    async def execute(
        self,
        context: SpecializedAgentContext,
        *,
        idempotency_key: str,
        payload: dict[str, Any],
        source_references: list[SourceReference | dict[str, Any]] | tuple[SourceReference, ...] = (),
        handler,
        policy_context: dict[str, Any] | None = None,
        source_resolver: SourceResolver | None = None,
    ) -> ExecutionOutcome:
        return await self.executor.execute(
            context,
            idempotency_key=idempotency_key,
            payload=payload,
            source_references=source_references,
            handler=handler,
            policy_context=policy_context,
            source_resolver=source_resolver,
        )

    async def forecast(
        self,
        context: SpecializedAgentContext,
        *,
        idempotency_key: str,
        points: list[UsagePoint],
        horizon: int,
        method: str = "linear",
        window: int = 3,
        alpha: float = 0.5,
    ) -> ExecutionOutcome:
        def handler(_context, _payload, _sources):
            projections = project_usage(points, horizon, method, window=window, alpha=alpha)
            quality = QualityState.VERIFIED.value if len(points) >= 2 else QualityState.NOT_AVAILABLE.value
            return {
                "method": method,
                "input_series_reference": {
                    "periods": [point.period for point in points],
                    "fingerprint": __import__("app.governance.hashing", fromlist=["sha256_hex"]).sha256_hex(
                        [{"period": point.period, "value": point.value} for point in points]
                    ),
                },
                "horizon": horizon,
                "projections": [projection.__dict__ for projection in projections],
                "lower_upper_method": "analytics.forecast documented spread",
                "residual_error_statistics": {
                    "available": method == "linear" and len(points) >= 2,
                    "not_available_reason": None if method == "linear" and len(points) >= 2 else "method does not expose residuals",
                },
                "data_quality_state": quality,
                "review_required": quality != QualityState.VERIFIED.value,
                "review_state": ReviewState.REQUIRED.value if quality != QualityState.VERIFIED.value else ReviewState.NOT_REQUIRED.value,
            }

        payload = {
            "series": [{"period": point.period, "value": point.value} for point in points],
            "horizon": horizon,
            "method": method,
            "window": window,
            "alpha": alpha,
        }
        return await self.execute(
            context,
            idempotency_key=idempotency_key,
            payload=payload,
            handler=handler,
            policy_context={"method": method, "uncertainty_disclosed": True},
        )
