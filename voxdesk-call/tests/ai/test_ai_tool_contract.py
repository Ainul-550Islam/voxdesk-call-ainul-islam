"""The existing dispatcher contract is the source of runtime tool admission."""

from __future__ import annotations

from app.agent.functions import DISPATCHABLE_TOOLS, tool_contract
from app.ai.guardrails.safety import DENY, evaluate
from app.ai.guardrails.tool_policy import authorize
from app.db.models import UserRole


def test_runtime_tool_policy_matches_the_only_dispatcher():
    for name in DISPATCHABLE_TOOLS:
        assert tool_contract(name)["agent_runtime"] is True
        assert authorize(name, principal="agent_runtime").allowed is True
    assert authorize("delete_tenant", principal="agent_runtime").allowed is False
    assert authorize("refund_payment", principal="model").allowed is False


def test_unknown_action_and_model_principal_fail_closed():
    unknown = evaluate("unregistered-action", role=UserRole.OWNER)
    model = evaluate("refund_payment", role=UserRole.OWNER, principal="model", fresh_mfa=True)
    assert unknown.decision == DENY
    assert model.decision == DENY
    assert model.reason == "model_cannot_authorize"
