"""Lead service: scope, transitions, consent, history, and legacy compatibility."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import Call, Lead, LeadStatus, Speaker, Turn
from app.leads import service
from app.leads.exceptions import InvalidTransition
from app.leads.repository import history_for


@pytest.mark.asyncio
async def test_create_stamps_tenant_and_environment(db, tenant_a):
    lead, created = await service.create_lead(
        db, tenant_id=tenant_a.id, phone="+1 (555) 200-1001", name="Ada"
    )
    await db.commit()
    assert created is True
    assert lead.tenant_id == tenant_a.id
    assert lead.environment_id is not None
    assert lead.phone == "+15552001001"
    assert lead.status is LeadStatus.NEW


@pytest.mark.asyncio
async def test_client_tenant_claim_is_rejected(db, tenant_a, tenant_b):
    from app.tenancy.isolation import BoundaryDenied

    with pytest.raises(BoundaryDenied):
        await service.create_lead(
            db,
            tenant_id=tenant_a.id,
            claimed_tenant_id=tenant_b.id,
            phone="+15552001002",
        )
    rows = (await db.execute(select(Lead).where(Lead.phone == "+15552001002"))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_transition_appends_history_and_dnc_is_terminal(db, tenant_a):
    lead, _created = await service.create_lead(
        db, tenant_id=tenant_a.id, phone="+15552001003", name="Bea"
    )
    await db.commit()
    await service.change_status(
        db, tenant_id=tenant_a.id, lead_id=lead.id, target="contacted", reason="first call"
    )
    await db.commit()
    await db.refresh(lead)
    assert lead.status is LeadStatus.CALLED
    history = await history_for(db, tenant_a.id, lead.environment_id, lead.id)
    contacted = history[-1]
    assert contacted.to_status == "called"
    assert "alias:contacted" in contacted.reason
    await service.change_status(
        db, tenant_id=tenant_a.id, lead_id=lead.id, target="do_not_call", reason="asked"
    )
    await db.commit()
    later = await history_for(db, tenant_a.id, lead.environment_id, lead.id)
    assert later[-2].reason == contacted.reason
    assert later[-2].id == contacted.id
    with pytest.raises(InvalidTransition):
        await service.change_status(
            db, tenant_id=tenant_a.id, lead_id=lead.id, target="qualified", reason="nope"
        )


@pytest.mark.asyncio
async def test_voice_denial_sets_dnc_and_grant_cannot_clear_it(db, tenant_a):
    lead, _created = await service.create_lead(db, tenant_id=tenant_a.id, phone="+15552001004")
    await service.record_consent(
        db, tenant_id=tenant_a.id, lead_id=lead.id, channel="voice", decision="denied", source="agent"
    )
    await db.commit()
    await db.refresh(lead)
    assert lead.status is LeadStatus.DNC
    with pytest.raises(InvalidTransition):
        await service.record_consent(
            db,
            tenant_id=tenant_a.id,
            lead_id=lead.id,
            channel="voice",
            decision="granted",
            source="agent",
        )


@pytest.mark.asyncio
async def test_owner_assignment_uses_the_user_table(db, tenant_a, owner_a):
    lead, _created = await service.create_lead(db, tenant_id=tenant_a.id, phone="+15552001005")
    await service.assign_owner(
        db, tenant_id=tenant_a.id, lead_id=lead.id, user_id=owner_a.id, actor_id=owner_a.id
    )
    await db.commit()
    from app.leads.repository import get_identity

    identity = await get_identity(db, tenant_a.id, lead.id)
    assert identity.owner_user_id == owner_a.id


@pytest.mark.asyncio
async def test_timeline_references_a_call_without_copying_the_transcript(db, tenant_a):
    lead, _created = await service.create_lead(db, tenant_id=tenant_a.id, phone="+15552001006")
    await db.flush()
    call = Call(
        tenant_id=lead.tenant_id,
        environment_id=lead.environment_id,
        call_sid=f"CA{uuid.uuid4().hex[:20]}",
        from_number="+15550000000",
        to_number=lead.phone,
        lead_id=lead.id,
    )
    db.add(call)
    await db.flush()
    db.add(Turn(call_id=call.id, speaker=Speaker.USER, text="SECRET TRANSCRIPT LINE"))
    await db.commit()
    events = await service.lead_timeline(db, tenant_id=tenant_a.id, lead_id=lead.id)
    blob = repr(events)
    assert "SECRET TRANSCRIPT LINE" not in blob
    assert str(call.id) in blob


@pytest.mark.asyncio
async def test_enrichment_is_not_configured(db, tenant_a):
    lead, _created = await service.create_lead(db, tenant_id=tenant_a.id, phone="+15552001007")
    body = await service.enrich(db, tenant_id=tenant_a.id, lead_id=lead.id)
    assert body["status"] == "NOT_CONFIGURED"
    assert body["fields"] == {}


@pytest.mark.asyncio
async def test_legacy_collection_still_creates(client, tenant_a, owner_a):
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    response = await client.post(
        f"/api/tenants/{tenant_a.id}/leads",
        headers=headers,
        json={"leads": [{"phone": "+15552001008", "name": "Legacy"}]},
    )
    assert response.status_code in (200, 201), response.text
    assert response.json()["created"] == 1
