"""PART 12 / P12-24 — export strips secrets, import plans a diff, merge is explicit."""

from __future__ import annotations

import pytest

from app.services.agent_portability_service import (
    EXPORT_FORMAT,
    PortabilityError,
    export_agent,
    find_secrets,
    import_agent,
    validate_bundle,
)

CONFIG = {
    "name": "Refill agent",
    "greeting": "Thanks for calling.",
    "twilio_auth_token": "super-secret-value",
    "voice": {"voice_id": "v1", "api_key": "sk-live-123"},
    "handoff": {"phone": "+15550100", "password_hint": "not a credential"},
}


def test_export_never_carries_a_real_credential() -> None:
    bundle = export_agent(agent_id="agent_1", config=CONFIG, flow={"nodes": []})
    text = str(bundle)
    assert "super-secret-value" not in text
    assert "sk-live-123" not in text
    assert bundle["agent"]["config"]["twilio_auth_token"] == {"$secret": "agent.config.twilio_auth_token"}


def test_the_export_names_the_secrets_the_importer_must_supply() -> None:
    bundle = export_agent(agent_id="agent_1", config=CONFIG)
    assert bundle["secrets_required"] == [
        "agent.config.twilio_auth_token",
        "agent.config.voice.api_key",
    ]


def test_a_harmless_string_is_not_redacted() -> None:
    bundle = export_agent(agent_id="a", config=CONFIG)
    assert bundle["agent"]["config"]["handoff"]["password_hint"] == "not a credential"


def test_the_format_is_versioned_and_validated() -> None:
    bundle = export_agent(agent_id="agent_1", config=CONFIG)
    assert bundle["format"] == EXPORT_FORMAT
    assert validate_bundle(bundle) == []

    broken = dict(bundle, format="voxdesk.agent/99")
    assert validate_bundle(broken)
    with pytest.raises(PortabilityError, match="format"):
        import_agent(broken)


def test_tenant_id_is_not_exported() -> None:
    bundle = export_agent(agent_id="agent_1", config={**CONFIG, "tenant_id": "tenant-9"})
    assert "tenant_id" not in bundle["agent"]["config"]


def test_create_mode_plans_every_key_as_new() -> None:
    bundle = export_agent(agent_id="agent_1", config=CONFIG)
    plan = import_agent(bundle, mode="create", dry_run=True)
    assert plan.mode == "create"
    assert all(change["from"] is None for change in plan.changes)
    assert plan.dry_run is True


def test_merge_mode_reports_only_real_differences() -> None:
    bundle = export_agent(agent_id="agent_1", config={"name": "New name", "greeting": "Same"})
    plan = import_agent(
        bundle,
        mode="merge",
        existing_config={"name": "Old name", "greeting": "Same"},
    )
    assert plan.changes == [{"path": "name", "from": "Old name", "to": "New name"}]


def test_merge_without_an_existing_agent_is_refused() -> None:
    bundle = export_agent(agent_id="agent_1", config=CONFIG)
    with pytest.raises(PortabilityError, match="merge needs an existing agent"):
        import_agent(bundle, mode="merge")


def test_an_unknown_mode_is_refused() -> None:
    bundle = export_agent(agent_id="agent_1", config=CONFIG)
    with pytest.raises(PortabilityError, match="mode must be one of"):
        import_agent(bundle, mode="overwrite")


def test_supplied_secrets_clear_the_checklist() -> None:
    bundle = export_agent(agent_id="agent_1", config=CONFIG)
    plan = import_agent(
        bundle,
        supplied_secrets={"agent.config.twilio_auth_token": "new", "agent.config.voice.api_key": "k"},
    )
    assert plan.unresolved_secrets == []


def test_a_real_import_mints_an_id() -> None:
    bundle = export_agent(agent_id="agent_1", config=CONFIG)
    plan = import_agent(bundle, mode="create", dry_run=False)
    assert plan.agent_id != "pending" and len(plan.agent_id) == 36


def test_find_secrets_walks_nested_structures() -> None:
    payload = {"a": {"b": {"$secret": "x"}}, "list": [{"$secret": "y"}]}
    assert find_secrets(payload) == ["x", "y"]
