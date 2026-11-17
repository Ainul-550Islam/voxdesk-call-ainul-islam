"""PART 14 / P14-04 — the library, the presets and the install review."""

from __future__ import annotations

import pytest

from app.db.channel_models import WorkflowInstall
from app.workflows import catalog, preset_functions, tool_store


def test_every_integration_declares_how_it_authenticates() -> None:
    for integration in catalog.CATALOG.values():
        assert integration.auth in catalog.AUTH_TYPES
        if integration.available:
            assert integration.actions, f"{integration.key} is available but has no actions"


def test_an_unavailable_integration_says_why() -> None:
    outlook = catalog.get("outlook")
    assert outlook.available is False
    assert "app registration" in (outlook.reason or "")


def test_scopes_are_the_union_of_the_chosen_actions_only() -> None:
    scopes, unknown = catalog.permissions_for("slack", ["post_message"])
    assert scopes == ["chat:write"]
    assert unknown == []
    both, _ = catalog.permissions_for("slack", ["post_message", "list_channels"])
    assert set(both) == {"chat:write", "channels:read"}


def test_unknown_integration_and_action_are_named() -> None:
    with pytest.raises(catalog.CatalogError) as unknown_integration:
        catalog.get("myspace")
    assert unknown_integration.value.code == "unknown_integration"
    with pytest.raises(catalog.CatalogError) as unknown_action:
        catalog.get("slack").action("delete_workspace")
    assert unknown_action.value.code == "unknown_action"


def test_the_review_text_names_writes_outside_voxdesk() -> None:
    text = catalog.review_text("hubspot", ["upsert_contact", "log_call"])
    assert "change data outside VoxDesk" in text
    assert "upsert_contact" in text


def test_presets_validate_arguments_by_field() -> None:
    cleaned = preset_functions.validate_call(
        "send_sms", {"to": "+8801712345678", "body": "Your appointment is confirmed."}
    )
    assert cleaned["to"] == "+8801712345678"
    with pytest.raises(preset_functions.PresetError) as bad_number:
        preset_functions.validate_call("send_sms", {"to": "0171234", "body": "x"})
    assert "E.164" in str(bad_number.value)
    with pytest.raises(preset_functions.PresetError) as missing:
        preset_functions.validate_call("send_sms", {"to": "+8801712345678"})
    assert missing.value.code == "missing_argument"


def test_a_hallucinated_argument_is_refused_not_dropped() -> None:
    with pytest.raises(preset_functions.PresetError) as exc:
        preset_functions.validate_call(
            "send_sms", {"to": "+8801712345678", "body": "hi", "discount": "90%"}
        )
    assert exc.value.code == "unknown_argument"
    assert "discount" in str(exc.value)


def test_an_appointment_must_end_after_it_starts() -> None:
    with pytest.raises(preset_functions.PresetError):
        preset_functions.validate_call(
            "book_appointment",
            {
                "start": "2026-11-11T10:00:00",
                "end": "2026-11-11T09:00:00",
                "title": "Demo",
            },
        )


def test_lookup_contact_needs_at_least_one_identifier() -> None:
    with pytest.raises(preset_functions.PresetError):
        preset_functions.validate_call("lookup_contact", {})


def test_retry_safety_is_declared_per_preset() -> None:
    safe, unsafe = preset_functions.retry_safe(["lookup_contact", "send_sms", "end_call"])
    assert safe == ["lookup_contact"]
    assert unsafe == ["send_sms", "end_call"]


@pytest.mark.asyncio
async def test_install_requires_a_review_and_can_be_enabled_afterwards(db, tenant_a) -> None:
    row = await tool_store.install(
        db,
        tenant_a.id,
        "slack",
        actions=["post_message"],
        config={"channel": "#ops"},
        reviewed_by="owner@a.test",
    )
    assert row.enabled is False
    assert row.permission_review["scopes"] == ["chat:write"]
    assert row.permission_review["reviewed_by"] == "owner@a.test"

    enabled = await tool_store.set_enabled(db, tenant_a.id, "slack", True)
    assert enabled.enabled is True

    view = tool_store.view(enabled)
    assert view["needs_review"] is False
    assert view["allowed_actions"] == ["post_message"]


@pytest.mark.asyncio
async def test_rescoping_an_install_bumps_its_version(db, tenant_a) -> None:
    first = await tool_store.install(
        db, tenant_a.id, "slack", actions=["post_message"], reviewed_by="owner@a.test"
    )
    second = await tool_store.install(
        db, tenant_a.id, "slack", actions=["post_message", "list_channels"], reviewed_by="owner@a.test"
    )
    assert first.id == second.id
    assert second.version == 2


@pytest.mark.asyncio
async def test_an_unavailable_integration_cannot_be_installed(db, tenant_a) -> None:
    with pytest.raises(tool_store.ToolStoreError) as exc:
        await tool_store.install(db, tenant_a.id, "outlook", actions=["book_slot"], reviewed_by="x")
    assert exc.value.code == "not_available"


@pytest.mark.asyncio
async def test_an_install_without_a_review_cannot_be_enabled(db, tenant_a) -> None:
    row = WorkflowInstall(
        tenant_id=tenant_a.id, workflow_key="slack", allowed_actions=["post_message"]
    )
    db.add(row)
    await db.flush()
    with pytest.raises(tool_store.ToolStoreError) as exc:
        await tool_store.set_enabled(db, tenant_a.id, "slack", True)
    assert exc.value.code == "review_missing"


@pytest.mark.asyncio
async def test_enabled_installs_filter_by_action(db, tenant_a) -> None:
    await tool_store.install(
        db, tenant_a.id, "slack", actions=["post_message"], reviewed_by="x", enabled=True
    )
    await tool_store.install(
        db, tenant_a.id, "google_drive", actions=["list_files"], reviewed_by="x", enabled=False
    )
    assert [row.workflow_key for row in await tool_store.enabled_installs(db, tenant_a.id)] == ["slack"]
    assert await tool_store.enabled_installs(db, tenant_a.id, action="list_files") == []
