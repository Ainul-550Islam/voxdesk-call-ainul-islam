"""Deterministic dedup. Fuzzy similarity must not merge."""

from __future__ import annotations

import pytest

from app.db.models import Environment, LeadStatus
from app.leads import dedup, service
from app.leads.exceptions import MergeRejected
from app.tenancy.isolation import NotFound


@pytest.mark.asyncio
async def test_formatted_phone_and_email_case_match_inside_one_environment(db, tenant_a):
    first, created = await service.create_lead(
        db,
        tenant_id=tenant_a.id,
        phone="+1 (555) 300-1001",
        email="Ada@Example.com",
        name="Ada Lovelace",
    )
    assert created is True
    second, again = await service.create_lead(
        db,
        tenant_id=tenant_a.id,
        phone="+15553001001",
        email="ada@example.com",
        on_duplicate="skip",
    )
    assert again is False
    assert second.id == first.id


@pytest.mark.asyncio
async def test_same_phone_in_another_environment_is_not_a_duplicate(db, tenant_a):
    staging = Environment(
        tenant_id=tenant_a.id,
        name="Staging",
        slug="staging",
        kind="staging",
        status="active",
    )
    db.add(staging)
    await db.commit()
    await db.refresh(staging)
    production, _created = await service.create_lead(
        db, tenant_id=tenant_a.id, phone="+15553001002", name="Prod"
    )
    other, created = await service.create_lead(
        db,
        tenant_id=tenant_a.id,
        environment_id=staging.id,
        phone="+15553001002",
        name="Stage",
    )
    assert created is True
    assert other.id != production.id
    assert other.environment_id == staging.id


@pytest.mark.asyncio
async def test_fuzzy_name_is_informational_and_does_not_merge(db, tenant_a):
    left, _created = await service.create_lead(
        db, tenant_id=tenant_a.id, phone="+15553001003", name="Jane Doe"
    )
    right, created = await service.create_lead(
        db, tenant_id=tenant_a.id, phone="+15553001004", name="Jane Doe"
    )
    assert created is True
    found = await service.duplicates(db, tenant_id=tenant_a.id, lead_id=left.id)
    assert found["exact"] == []
    assert found["informational"]
    assert all(item["mergeable"] is False for item in found["informational"])
    assert dedup.name_similarity("Jane Doe", "Jane Doe") == 1.0
    assert right.id != left.id


@pytest.mark.asyncio
async def test_explicit_merge_keeps_history_and_propagates_dnc(db, tenant_a):
    survivor, _created = await service.create_lead(
        db, tenant_id=tenant_a.id, phone="+15553001005", name="Survivor"
    )
    duplicate, _created = await service.create_lead(
        db, tenant_id=tenant_a.id, phone="+15553001006", name="Duplicate"
    )
    await service.change_status(
        db, tenant_id=tenant_a.id, lead_id=duplicate.id, target="do_not_call", reason="stop"
    )
    await service.merge_leads(
        db,
        tenant_id=tenant_a.id,
        survivor_id=survivor.id,
        duplicate_id=duplicate.id,
        reason="operator confirmed",
    )
    await db.commit()
    await db.refresh(survivor)
    await db.refresh(duplicate)
    assert survivor.status is LeadStatus.DNC
    assert duplicate.id is not None
    from app.leads.repository import get_identity, history_for

    identity = await get_identity(db, tenant_a.id, duplicate.id)
    assert identity.merged_into_id == survivor.id
    history = await history_for(db, tenant_a.id, duplicate.environment_id, duplicate.id)
    assert any("merged_into" in row.reason for row in history)


@pytest.mark.asyncio
async def test_cross_tenant_merge_is_rejected(db, tenant_a, tenant_b):
    survivor, _created = await service.create_lead(
        db, tenant_id=tenant_a.id, phone="+15553001007", name="A"
    )
    foreign, _created = await service.create_lead(
        db, tenant_id=tenant_b.id, phone="+15553001008", name="B"
    )
    with pytest.raises(NotFound):
        await dedup.merge(
            db,
            tenant_id=tenant_a.id,
            environment_id=survivor.environment_id,
            survivor_id=survivor.id,
            duplicate_id=foreign.id,
            reason="no",
        )


@pytest.mark.asyncio
async def test_self_merge_is_rejected(db, tenant_a):
    lead, _created = await service.create_lead(db, tenant_id=tenant_a.id, phone="+15553001009")
    with pytest.raises(MergeRejected):
        await dedup.merge(
            db,
            tenant_id=tenant_a.id,
            environment_id=lead.environment_id,
            survivor_id=lead.id,
            duplicate_id=lead.id,
            reason="no",
        )
