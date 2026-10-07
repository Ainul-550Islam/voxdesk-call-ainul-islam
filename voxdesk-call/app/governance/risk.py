"""Configurable, deterministic risk-tier controls."""

from __future__ import annotations

from typing import Any

from .enums import RiskTier
from .exceptions import GovernanceValidation

BASE_CONTROLS: dict[RiskTier, tuple[str, ...]] = {
    RiskTier.LOW: ("basic_audit",),
    RiskTier.MODERATE: ("basic_audit", "model_approval", "guardrails"),
    RiskTier.HIGH: (
        "basic_audit",
        "model_approval",
        "guardrails",
        "human_approval",
        "enhanced_evidence",
        "deployment_restriction",
    ),
    RiskTier.CRITICAL: (
        "basic_audit",
        "model_approval",
        "guardrails",
        "human_approval",
        "enhanced_evidence",
        "deployment_restriction",
        "administrator_approval",
        "strong_retention",
    ),
}


def normalize_tier(value: str | RiskTier) -> RiskTier:
    try:
        return value if isinstance(value, RiskTier) else RiskTier(value)
    except (TypeError, ValueError) as exc:
        raise GovernanceValidation("Invalid risk tier") from exc


def required_controls(
    tier: str | RiskTier, *, extra_controls: list[str] | tuple[str, ...] = ()
) -> list[str]:
    normalized = normalize_tier(tier)
    return list(dict.fromkeys((*BASE_CONTROLS[normalized], *extra_controls)))


def score_to_tier(score: int | float) -> RiskTier:
    """Convert an explicitly configured score, not a regulatory assumption."""
    if score < 0:
        raise GovernanceValidation("Risk score cannot be negative")
    if score < 25:
        return RiskTier.LOW
    if score < 50:
        return RiskTier.MODERATE
    if score < 75:
        return RiskTier.HIGH
    return RiskTier.CRITICAL


def deterministic_score(factors: dict[str, Any]) -> int:
    """Score only declared factors; unknown factors do not invent a claim."""
    weights = factors.get("weights", {})
    if not isinstance(weights, dict):
        raise GovernanceValidation("Risk weights must be an object")
    score = 0
    for key in sorted(weights):
        value = weights[key]
        if not isinstance(value, (int, float)) or value < 0:
            raise GovernanceValidation(f"Risk weight {key!r} must be non-negative")
        score += int(value)
    return score


def controls_satisfied(tier: str | RiskTier, satisfied: set[str]) -> bool:
    return set(required_controls(tier)).issubset(satisfied)
