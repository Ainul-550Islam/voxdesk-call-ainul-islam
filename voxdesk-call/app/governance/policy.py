"""Central policy decision point (PDP) helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .context import GovernanceScope
from .enums import DecisionType
from .exceptions import MissingGovernanceConfiguration, PolicyDenied
from .service import decide


@dataclass(frozen=True)
class PolicyEvaluation:
    decision: str
    reason_code: str
    conditions: tuple[str, ...] = ()


def evaluate_rules(rules: dict[str, Any], context: dict[str, Any]) -> PolicyEvaluation:
    """Evaluate declarative rules without making authorization decisions locally."""
    missing = [key for key in rules.get("required_fields", []) if key not in context]
    if missing:
        return PolicyEvaluation(DecisionType.DENY.value, "required_policy_input_missing")

    checks: tuple[tuple[str, str], ...] = (
        ("required_human_approval", "human_approval_required"),
        ("approved_model_only", "approved_model_required"),
        ("approved_environment", "approved_environment_required"),
        ("allowed_tool", "tool_not_allowed"),
        ("data_classification", "data_classification_not_allowed"),
        ("tenant_feature", "tenant_feature_disabled"),
        ("risk_tier", "risk_tier_not_allowed"),
    )
    unmet: list[str] = []
    for key, reason in checks:
        expected = rules.get(key)
        if expected is None:
            continue
        actual = context.get(key)
        if key == "required_human_approval" and expected is True and actual is not True:
            unmet.append(reason)
        elif key != "required_human_approval":
            mismatch = actual not in expected if isinstance(expected, list) else actual != expected
            if mismatch:
                unmet.append(reason)
    if unmet:
        return PolicyEvaluation(DecisionType.CONDITION.value, unmet[0], tuple(unmet))

    decision = str(rules.get("decision", DecisionType.ALLOW.value)).lower()
    if decision not in {item.value for item in DecisionType}:
        return PolicyEvaluation(DecisionType.DENY.value, "invalid_policy_decision")
    return PolicyEvaluation(decision, str(rules.get("reason_code", "policy_allowed")))


async def evaluate_policy(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    policy_type: str,
    context: dict[str, Any],
    principal_id: uuid.UUID | None,
    correlation_id: str | None = None,
):
    """Persist and return the decision record through the shared service."""
    return await decide(
        session,
        scope,
        policy_type=policy_type,
        input_payload=context,
        principal_id=principal_id,
        correlation_id=correlation_id,
    )


async def require_policy(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    policy_type: str,
    context: dict[str, Any],
    principal_id: uuid.UUID | None,
    correlation_id: str | None = None,
):
    decision = await evaluate_policy(
        session,
        scope,
        policy_type=policy_type,
        context=context,
        principal_id=principal_id,
        correlation_id=correlation_id,
    )
    if decision.decision == DecisionType.DENY.value:
        if decision.reason_code == "governance_configuration_missing":
            raise MissingGovernanceConfiguration(decision.reason_code)
        raise PolicyDenied(decision.reason_code)
    if decision.decision == DecisionType.CONDITION.value:
        raise PolicyDenied(decision.reason_code)
    return decision
