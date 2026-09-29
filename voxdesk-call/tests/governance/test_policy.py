"""PDP rule behavior and fail-closed policy integration tests."""

from __future__ import annotations

from app.governance.enums import DecisionType
from app.governance.policy import evaluate_rules


def test_pdp_supports_allow_deny_and_human_condition():
    assert evaluate_rules({"decision": "allow"}, {}).decision == DecisionType.ALLOW.value
    assert evaluate_rules({"decision": "deny", "reason_code": "blocked"}, {}).reason_code == "blocked"
    conditional = evaluate_rules({"required_human_approval": True}, {})
    assert conditional.decision == DecisionType.CONDITION.value
    assert conditional.reason_code == "human_approval_required"


def test_pdp_denies_missing_configuration_inputs():
    result = evaluate_rules({"required_fields": ["model_version"]}, {})
    assert result.decision == DecisionType.DENY.value
    assert result.reason_code == "required_policy_input_missing"
