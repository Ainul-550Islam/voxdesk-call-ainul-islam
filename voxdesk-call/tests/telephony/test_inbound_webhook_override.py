"""PART 12 / P12-25 — the inbound webhook may choose the agent, version and variables."""

from __future__ import annotations

from app.telephony.webhooks import (
    INBOUND_OVERRIDE_KEYS,
    parse_inbound_overrides,
    resolve_inbound_agent,
)


def test_a_webhook_can_choose_the_agent_and_version() -> None:
    overrides = parse_inbound_overrides(
        {"agent_id": "agent_vip", "agent_version": 4, "dynamic_variables": {"tier": "gold"}}
    )
    assert overrides.agent_id == "agent_vip"
    assert overrides.agent_version == 4
    assert overrides.dynamic_variables == {"tier": "gold"}
    assert overrides.source == "webhook"


def test_metadata_is_carried_through_untouched() -> None:
    overrides = parse_inbound_overrides({"metadata": {"campaign": "spring", "lead": 12}})
    assert overrides.metadata == {"campaign": "spring", "lead": 12}


def test_unknown_keys_are_recorded_not_merged() -> None:
    overrides = parse_inbound_overrides({"agent_id": "a", "banana": 1, "tenant_id": "other"})
    assert set(overrides.ignored_keys) == {"banana", "tenant_id"}
    assert "tenant_id" not in overrides.as_dict()["metadata"]


def test_a_bad_version_does_not_break_the_call() -> None:
    overrides = parse_inbound_overrides({"agent_id": "a", "agent_version": "latest"})
    assert overrides.agent_version is None
    assert "agent_version" in overrides.ignored_keys


def test_a_non_object_body_falls_back_to_the_number_agent() -> None:
    overrides = parse_inbound_overrides("not json")
    assert overrides.source == "default"
    resolved = resolve_inbound_agent(overrides, number_agent_id="number_agent")
    assert resolved.agent_id == "number_agent"
    assert resolved.source == "default"


def test_a_failed_webhook_uses_the_fallback_agent() -> None:
    overrides = parse_inbound_overrides({"fallback_agent_id": "backup_agent"})
    resolved = resolve_inbound_agent(overrides, number_agent_id="number_agent", webhook_failed=True)
    assert resolved.agent_id == "backup_agent"
    assert resolved.source == "fallback"


def test_without_a_fallback_the_number_agent_takes_it() -> None:
    resolved = resolve_inbound_agent(
        parse_inbound_overrides({}), number_agent_id="number_agent", webhook_failed=True
    )
    assert resolved.agent_id == "number_agent"


def test_a_working_webhook_wins_over_the_number_default() -> None:
    overrides = parse_inbound_overrides({"agent_id": "webhook_agent"})
    resolved = resolve_inbound_agent(overrides, number_agent_id="number_agent")
    assert resolved.agent_id == "webhook_agent"
    assert resolved.source == "webhook"


def test_the_allowed_key_set_is_small_and_documented() -> None:
    assert INBOUND_OVERRIDE_KEYS == {
        "agent_id",
        "agent_version",
        "dynamic_variables",
        "metadata",
        "fallback_agent_id",
    }
