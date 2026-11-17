"""PART 12 / P12-26 — promoting a version between environments."""

from __future__ import annotations

import pytest

from app.environments.service import PromotionError, plan_promotion


def test_promotion_names_which_defaults_change() -> None:
    plan = plan_promotion(
        agent_id="agent_1",
        version=3,
        from_env="staging",
        to_env="production",
        source_variables={"api_base_url": "https://staging", "campaign": "spring"},
        target_variables={"api_base_url": "https://prod", "region": "us-east"},
    )
    assert plan.allowed is True
    assert plan.variables_overridden == ("api_base_url",), "the target environment wins"
    assert plan.variables_kept == ("campaign",), "staging-only defaults survive untouched"
    assert plan.variables_added == ("region",)


def test_the_same_environment_is_not_a_promotion() -> None:
    with pytest.raises(PromotionError, match="two different environments"):
        plan_promotion(agent_id="a", version=1, from_env="prod", to_env="prod")


def test_a_missing_environment_is_refused() -> None:
    with pytest.raises(PromotionError, match="both environments"):
        plan_promotion(agent_id="a", version=1, from_env="", to_env="prod")


def test_a_non_positive_version_is_refused() -> None:
    with pytest.raises(PromotionError, match="version must be positive"):
        plan_promotion(agent_id="a", version=0, from_env="staging", to_env="prod")


def test_missing_required_facts_block_the_promotion() -> None:
    plan = plan_promotion(
        agent_id="agent_1",
        version=2,
        from_env="staging",
        to_env="production",
        required_facts=("twilio_number", "sip_trunk"),
        target_facts_present=False,
    )
    assert plan.allowed is False
    assert plan.blockers and "twilio_number" in plan.blockers[0]


def test_the_plan_serialises_for_the_audit_log() -> None:
    payload = plan_promotion(
        agent_id="agent_1", version=1, from_env="staging", to_env="production"
    ).as_dict()
    assert payload["from_env"] == "staging"
    assert payload["allowed"] is True
    assert payload["blockers"] == []
