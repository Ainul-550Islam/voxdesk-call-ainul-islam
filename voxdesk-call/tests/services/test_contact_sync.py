"""PART 14 / P14-06 — contact attribute sync: mapping, policy, provenance."""

from __future__ import annotations

import pytest

from app.db.retell_models import Contact
from app.services import contact_sync_service as sync


def test_only_mapped_fields_are_copied() -> None:
    result = sync.merge(
        {}, {"full_name": "Rahim", "budget": "5000", "favourite_colour": "blue"}, source="chat"
    )
    assert result.values["name"] == "Rahim"
    assert result.values["budget"] == "5000"
    reasons = {entry["field"]: entry["reason"] for entry in result.rejected}
    assert reasons["favourite_colour"] == "unmapped_field"


def test_transcripts_and_summaries_never_become_attributes() -> None:
    result = sync.merge({}, {"summary": "caller wanted a refund", "transcript": "..."}, source="call")
    assert "summary" not in result.values
    assert {entry["reason"] for entry in result.rejected} == {"not_a_contact_attribute"}


def test_fill_empty_is_the_default_and_preserves_a_typed_value() -> None:
    result = sync.merge({"company": "Acme Ltd"}, {"company": "Acme"}, source="call:1")
    assert result.values["company"] == "Acme Ltd"
    assert result.conflicts[0]["kept"] == "Acme Ltd"
    assert result.applied == {}


def test_overwrite_records_what_it_replaced() -> None:
    result = sync.merge(
        {"company": "Acme Ltd"}, {"company": "Acme"}, source="crm", policy="overwrite"
    )
    assert result.values["company"] == "Acme"
    assert result.conflicts[0]["replaced"] == "Acme Ltd"
    assert result.applied["company"] == "crm"


def test_keep_existing_never_writes_but_reports_the_conflict() -> None:
    result = sync.merge(
        {"email": "old@example.test"}, {"email": "new@example.test"}, source="crm", policy="keep_existing"
    )
    assert result.values["email"] == "old@example.test"
    assert result.conflicts and result.conflicts[0]["offered"] == "new@example.test"


def test_an_invalid_email_or_phone_is_rejected_not_stored() -> None:
    result = sync.merge(
        {}, {"email": "not-an-email", "phone": "12"}, source="chat"
    )
    assert result.values == {}
    assert {entry["field"] for entry in result.rejected} == {"email", "phone"}


def test_an_unknown_policy_is_refused() -> None:
    with pytest.raises(sync.SyncError):
        sync.merge({}, {"name": "x"}, policy="merge_whatever")


def test_phone_normalisation_makes_one_person_look_like_one_person() -> None:
    assert sync.normalize_phone("+880 1712-345678") == "+8801712345678"
    assert sync.normalize_phone("01712345678") == "01712345678"
    assert sync.normalize_phone("12345") is None


def test_derived_attributes_come_from_the_analysis_extract_only() -> None:
    derived = sync.derive_attributes(
        {
            "extracted": {"intent": "refill", "budget": "5000", "favourite_colour": "blue"},
            "caller_number": "+8801712345678",
            "summary": "long transcript text",
        }
    )
    assert derived == {"intent": "refill", "budget": "5000", "phone": "+8801712345678"}


def test_the_callers_number_fills_a_missing_phone_but_never_overwrites() -> None:
    derived = sync.derive_attributes({"caller_number": "+8801712345678"})
    assert derived == {"phone": "+8801712345678"}


@pytest.mark.asyncio
async def test_sync_contact_writes_provenance_per_field(db, tenant_a) -> None:
    contact = Contact(tenant_id=tenant_a.id, phone="+8801712345678", name="")
    db.add(contact)
    await db.flush()

    result = await sync.sync_contact(
        db,
        tenant_id=tenant_a.id,
        contact=contact,
        incoming={"full_name": "Rahim", "company": "Acme"},
        source="call:abc",
    )
    assert result.values["name"] == "Rahim"
    provenance = sync.provenance_view(contact.custom_fields)
    assert provenance["name"]["source"] == "call:abc"
    # `company` is a real column on Contact, so it is written there; only
    # unmapped-but-mapped-elsewhere fields fall through to custom_fields.
    assert contact.company == "Acme"
    assert provenance["company"]["source"] == "call:abc"


@pytest.mark.asyncio
async def test_backfill_updates_only_contacts_it_can_match(db, tenant_a) -> None:
    contact = Contact(tenant_id=tenant_a.id, phone="+8801712345678", name="Rahim")
    db.add(contact)
    await db.flush()

    report = await sync.backfill_attributes(
        db,
        tenant_id=tenant_a.id,
        analyses=[
            {"call_id": "c1", "caller_number": "+8801712345678", "extracted": {"intent": "refill"}},
            {"call_id": "c2", "extracted": {"intent": "refill"}},
            {"call_id": "c3", "caller_number": "+8801999999999", "extracted": {"intent": "x"}},
        ],
    )
    assert report["considered"] == 3
    assert report["updated"] == 1
    reasons = {entry["reason"] for entry in report["skipped"]}
    assert reasons == {"no_caller_number", "no_contact"}
