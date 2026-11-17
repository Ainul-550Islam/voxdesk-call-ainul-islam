"""PART 14 / P14-05 — memory settings, the running brief, recall and skip."""

from __future__ import annotations

import pytest

from sqlalchemy import select

from app.db.models import Call, CallDirection, CallStatus, Environment
from app.db.channel_models import ContactBrief
from app.db.retell_models import Contact, ContactMemoryEntry
from app.domain.contact_memory_models import MemorySaveRequest
from app.services import contact_memory_service as memory


@pytest.fixture
async def contact(db, tenant_a) -> Contact:
    row = Contact(tenant_id=tenant_a.id, phone="+8801712345678", name="Rahim")
    db.add(row)
    await db.flush()
    return row


async def _entry(db, tenant, contact, key: str, value: str, importance: int = 0):
    entry = ContactMemoryEntry(
        tenant_id=tenant.id,
        contact_id=contact.id,
        key=key,
        value=value,
        importance=importance,
    )
    db.add(entry)
    await db.flush()
    return entry


@pytest.mark.asyncio
async def test_settings_are_created_on_first_read_with_safe_defaults(db, tenant_a) -> None:
    row = await memory.get_settings(db, tenant_a.id)
    assert row.save_enabled is True
    assert row.recall_enabled is True
    assert row.brief_enabled is True
    assert row.workspace_prompt == ""
    assert row.retention_days == 90


@pytest.mark.asyncio
async def test_settings_validation_refuses_absurd_values(db, tenant_a) -> None:
    with pytest.raises(ValueError):
        await memory.update_settings(db, tenant_a.id, updated_by="x", retention_days=0)
    with pytest.raises(ValueError):
        await memory.update_settings(
            db, tenant_a.id, updated_by="x", workspace_prompt="a" * 2001
        )


def test_a_brief_redacts_card_numbers_and_ssn_shapes() -> None:
    text = memory.redact("card 4111 1111 1111 1111 and ssn 123-45-6789")
    assert "4111" not in text
    assert "123-45-6789" not in text
    assert text.count("[redacted]") == 2


@pytest.mark.asyncio
async def test_the_brief_orders_high_importance_facts_first_and_never_leaks_secrets(
    db, tenant_a, contact
) -> None:
    await _entry(db, tenant_a, contact, "plan", "[redacted]", importance=1)
    await _entry(db, tenant_a, contact, "preferred_time", "Friday morning", importance=9)
    await _entry(db, tenant_a, contact, "api_token", "sk-live-123", importance=100)

    entries = await memory.list_entries(db, tenant_a.id, contact.id)
    composed = memory.compose_brief(entries=entries, calls=[])
    first_line = composed["brief"].splitlines()[0]
    assert first_line.startswith("preferred_time")
    assert "sk-live-123" not in composed["brief"]


@pytest.mark.asyncio
async def test_the_brief_counts_what_it_was_built_from(db, tenant_a, contact) -> None:
    await _entry(db, tenant_a, contact, "plan", "Pro")
    environment = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant_a.id, Environment.kind == "production"
            )
        )
    ).scalar_one()
    for index in range(3):
        db.add(
            Call(
                tenant_id=tenant_a.id,
                environment_id=environment.id,
                call_sid=f"CA-{index}",
                from_number="+8801712345678",
                to_number="+8801900000000",
                status=CallStatus.COMPLETED,
                direction=CallDirection.INBOUND,
                summary=f"call {index} summary",
            )
        )
    await db.flush()
    row = await memory.build_brief(db, tenant_a.id, contact.id)
    assert row.calls_behind == 3
    assert "call " in row.brief
    assert row.pii_redacted is True


@pytest.mark.asyncio
async def test_an_operator_edit_is_recorded_and_clearing_keeps_the_facts(
    db, tenant_a, contact
) -> None:
    await _entry(db, tenant_a, contact, "plan", "Pro")
    await memory.build_brief(db, tenant_a.id, contact.id)
    edited = await memory.set_brief(
        db, tenant_a.id, contact.id, "Called twice about billing.", edited_by="owner@a.test"
    )
    assert edited.brief == "Called twice about billing."
    assert edited.edited_by == "owner@a.test"

    await memory.clear_brief(db, tenant_a.id, contact.id)
    remaining = (
        await db.execute(
            ContactBrief.__table__.select().where(ContactBrief.contact_id == contact.id)
        )
    ).all()
    assert remaining == []
    assert len(await memory.list_entries(db, tenant_a.id, contact.id)) == 1


@pytest.mark.asyncio
async def test_recall_reports_a_known_caller_only_with_facts_and_a_brief(
    db, tenant_a, contact
) -> None:
    entry = await _entry(db, tenant_a, contact, "plan", "Pro")
    without_brief = memory.recall(entries=[entry], brief=None, workspace_prompt="Be brief.")
    assert without_brief["known_caller"] is False
    assert without_brief["block"].startswith("Be brief.")

    brief = ContactBrief(
        tenant_id=tenant_a.id, contact_id=contact.id, brief="plan: Pro\nprefers mornings"
    )
    with_brief = memory.recall(entries=[entry], brief=brief, workspace_prompt="Be brief.")
    assert with_brief["known_caller"] is True
    assert "prefers mornings" in with_brief["block"]


def test_recall_never_includes_a_credential_fact() -> None:
    class Entry:
        key = "password"
        value = "hunter2"
        importance = 5

    result = memory.recall(entries=[Entry()], brief=None)
    assert "hunter2" not in result["block"]
    assert result["facts"] == []


def test_a_node_is_skipped_only_when_the_workflow_allows_it_and_the_caller_is_known() -> None:
    known = {"known_caller": True}
    unknown = {"known_caller": False}
    assert memory.should_skip_node(
        node_id="identity", recall_result=known, skippable_when_known=["identity"]
    )
    assert not memory.should_skip_node(
        node_id="identity", recall_result=unknown, skippable_when_known=["identity"]
    )
    assert not memory.should_skip_node(
        node_id="payment", recall_result=known, skippable_when_known=["identity"]
    )


@pytest.mark.asyncio
async def test_backfill_builds_briefs_only_for_contacts_with_facts(db, tenant_a, contact) -> None:
    await _entry(db, tenant_a, contact, "plan", "Pro")
    report = await memory.backfill_briefs(db, tenant_a.id)
    assert report == {"considered": 1, "built": 1, "skipped": 0}
    again = await memory.backfill_briefs(db, tenant_a.id)
    assert again["built"] == 0


@pytest.mark.asyncio
async def test_saving_a_fact_still_works_through_the_existing_entry_point(db, tenant_a, contact) -> None:
    saved = await memory.save(
        db,
        tenant_a.id,
        contact.id,
        "plan",
        MemorySaveRequest(value="Enterprise", source="manual"),
    )
    assert saved.value == "Enterprise"
    fetched = await memory.get_entry(db, tenant_a.id, contact.id, "plan")
    assert fetched is not None and fetched.value == "Enterprise"
