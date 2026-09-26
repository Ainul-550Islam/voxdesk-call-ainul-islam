"""Allowlisted segments. Raw SQL is rejected. Membership stays in tenant scope."""

from __future__ import annotations

import pytest

from app.leads import segmentation, service
from app.leads.exceptions import InvalidSegment
from app.tenancy.isolation import NotFound


@pytest.mark.asyncio
async def test_segment_returns_only_matching_leads_in_scope(db, tenant_a, tenant_b):
    qualified, _created = await service.create_lead(
        db, tenant_id=tenant_a.id, phone="+15556001001", name="Qualified"
    )
    await service.create_lead(db, tenant_id=tenant_a.id, phone="+15556001002", name="New")
    await service.create_lead(db, tenant_id=tenant_b.id, phone="+15556001003", name="Other")
    await service.change_status(
        db, tenant_id=tenant_a.id, lead_id=qualified.id, target="qualified", reason="fit"
    )
    segment = await service.create_segment(
        db,
        tenant_id=tenant_a.id,
        name="Qualified",
        definition={"all": [{"field": "status", "op": "eq", "value": "qualified"}]},
    )
    await db.commit()
    members = await service.segment_members(
        db, tenant_id=tenant_a.id, segment_id=segment.id, limit=10, offset=0
    )
    assert [row.id for row in members] == [qualified.id]
    with pytest.raises(NotFound):
        await service.segment_members(db, tenant_id=tenant_b.id, segment_id=segment.id)


def test_raw_sql_and_unknown_fields_are_rejected():
    with pytest.raises(InvalidSegment):
        segmentation.validate_definition({"sql": "drop table leads"})
    with pytest.raises(InvalidSegment):
        segmentation.validate_definition(
            {"all": [{"field": "status; drop table leads", "op": "eq", "value": "new"}]}
        )
    with pytest.raises(InvalidSegment):
        segmentation.validate_definition({"all": [{"field": "notes", "op": "eq", "value": "x"}]})


@pytest.mark.asyncio
async def test_member_pagination(db, tenant_a):
    for index in range(3):
        lead, _created = await service.create_lead(
            db, tenant_id=tenant_a.id, phone=f"+1555600200{index}", name=f"N{index}"
        )
        await service.change_status(
            db, tenant_id=tenant_a.id, lead_id=lead.id, target="qualified", reason="fit"
        )
    segment = await service.create_segment(
        db,
        tenant_id=tenant_a.id,
        name="All qualified",
        definition={"all": [{"field": "status", "op": "eq", "value": "qualified"}]},
    )
    first = await service.segment_members(
        db, tenant_id=tenant_a.id, segment_id=segment.id, limit=2, offset=0
    )
    second = await service.segment_members(
        db, tenant_id=tenant_a.id, segment_id=segment.id, limit=2, offset=2
    )
    assert len(first) == 2
    assert len(second) == 1
    assert {row.id for row in first}.isdisjoint({row.id for row in second})
