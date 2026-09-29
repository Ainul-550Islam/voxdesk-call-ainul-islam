"""Model lifecycle and runtime admission unit coverage."""

from __future__ import annotations

import pytest

from app.governance.enums import (
    MODEL_REGISTRY_TRANSITIONS,
    MODEL_VERSION_TRANSITIONS,
    ModelRegistryStatus,
    ModelVersionStatus,
)
from app.governance.exceptions import GovernanceValidation
from app.governance.risk import normalize_tier


def test_model_lifecycle_is_explicit_and_terminal_retirement_is_final():
    assert ModelRegistryStatus.ACTIVE in MODEL_REGISTRY_TRANSITIONS[ModelRegistryStatus.REGISTERED]
    assert not MODEL_REGISTRY_TRANSITIONS[ModelRegistryStatus.RETIRED]
    assert ModelVersionStatus.APPROVED in MODEL_VERSION_TRANSITIONS[ModelVersionStatus.SUBMITTED]
    assert not MODEL_VERSION_TRANSITIONS[ModelVersionStatus.RETIRED]


def test_unknown_risk_tier_is_rejected_not_normalized_to_a_safe_value():
    with pytest.raises(GovernanceValidation):
        normalize_tier("regulated")
