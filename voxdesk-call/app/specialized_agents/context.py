"""Canonical immutable context for one specialized-agent execution."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.governance.context import GovernanceScope

from .enums import AgentType, RiskTier
from .exceptions import DataValidationError


@dataclass(frozen=True)
class SpecializedAgentContext:
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    environment_id: uuid.UUID
    actor_id: uuid.UUID
    request_id: str
    trace_id: str
    agent_type: str
    agent_version: str
    policy_version: int | None
    model_version_id: uuid.UUID
    risk_tier: str
    locale: str | None = None
    language: str | None = None
    source_references: tuple[str, ...] = ()
    created_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    scope: GovernanceScope | None = field(default=None, compare=False, repr=False)
    tenant: Any | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        try:
            AgentType(self.agent_type)
        except ValueError as exc:
            raise DataValidationError("agent_type is not supported") from exc
        if self.environment_id is None:
            raise DataValidationError("environment_id is required for specialized execution")
        if self.tenant_id is None or self.organization_id is None:
            raise DataValidationError("tenant and organization scope are required")
        if not self.request_id or len(self.request_id) > 128:
            raise DataValidationError("request_id is required and must be at most 128 characters")
        if not self.trace_id or len(self.trace_id) > 128:
            raise DataValidationError("trace_id is required and must be at most 128 characters")
        if not self.agent_version or len(self.agent_version) > 100:
            raise DataValidationError("agent_version is required and must be at most 100 characters")
        if not self.model_version_id:
            raise DataValidationError("model_version_id is required")
        try:
            RiskTier(self.risk_tier)
        except ValueError as exc:
            raise DataValidationError("risk_tier is not supported") from exc

    @classmethod
    def from_scope(
        cls,
        scope: GovernanceScope,
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
    ) -> "SpecializedAgentContext":
        return cls(
            tenant_id=scope.tenant_id,
            organization_id=scope.organization_id,
            environment_id=scope.environment_id,
            actor_id=actor_id,
            request_id=request_id,
            trace_id=trace_id,
            agent_type=agent_type,
            agent_version=agent_version,
            policy_version=policy_version,
            model_version_id=model_version_id,
            risk_tier=risk_tier,
            locale=locale,
            language=language,
            source_references=source_references,
            scope=scope,
            tenant=scope.tenant,
        )

    def governance_payload(self, *, input_fingerprint: str, source_count: int) -> dict[str, Any]:
        return {
            "agent_type": self.agent_type,
            "agent_version": self.agent_version,
            "model_version_id": str(self.model_version_id),
            "risk_tier": self.risk_tier,
            "environment_id": str(self.environment_id),
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "input_fingerprint": input_fingerprint,
            "source_count": source_count,
        }
