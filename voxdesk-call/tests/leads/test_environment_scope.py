"""Lead environment scope (Batch 06).

A lead is readable and writable only inside its own tenant + environment
pair. From another environment of the same tenant every lookup returns the
same safe not-found as from a foreign tenant; activities, tasks, scores and
consents cannot be attached across the boundary; ``environment_id`` is
immutable after creation; and a campaign binding must live in the lead's own
environment.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import Campaign, Environment
from app.leads import activities, service
from app.tenancy.isolation import BoundaryDenied, NotFound, ValidationFailed
from tests.conftest import make_lead
from app.environments.resource_binding import ImmutableEnvironment


# ----------------------------------------------------------------- helpers ---

async def _production(db, tenant) -> Environment:
    return (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()


async def _staging(db, tenant) -> Environment:
    environment = Environment(
        tenant_id=tenant.id, name="Staging", slug="staging", kind="staging",
        status="active", is_default=False,
    )
    db.add(environment)
    await db.commit()
    await db.refresh(environment)
    return environment


# ------------------------------------------------------------ read scope ---

async def test_lead_is_invisible_from_another_environment(db, tenant_a):
    from app.leads.repository import get_lead, require_environment

    staging = await _staging(db, tenant_a)
    production = await _production(db, tenant_a)
    lead = await make_lead(
        db, tenant_a, phone="+15550500001", environment_id=staging.id
    )

    # listing from the default (production) context never shows it
    rows = await service.search(db, tenant_id=tenant_a.id)
    assert lead.id not in [row.id for row in rows]
    # listing from the explicit staging context does
    rows = await service.search(db, tenant_id=tenant_a.id, environment_id=staging.id)
    assert lead.id in [row.id for row in rows]

    # direct reads are scoped the same way
    with pytest.raises(NotFound):
        await get_lead(db, tenant_a.id, lead.id, production.id)
    found = await get_lead(db, tenant_a.id, lead.id, staging.id)
    assert found.id == lead.id
    resolved = await require_environment(db, tenant_a.id, None)
    assert resolved == production.id


async def test_lead_is_invisible_from_another_tenant(db, tenant_a, tenant_b):
    from app.leads.repository import get_lead

    lead = await make_lead(db, tenant_a, phone="+15550500002")
    with pytest.raises(NotFound):
        await get_lead(db, tenant_b.id, lead.id)
    rows = await service.search(db, tenant_id=tenant_b.id, phone=lead.phone)
    assert rows == []


# ----------------------------------------------------------- write scope ---

async def test_update_from_the_wrong_environment_is_not_found(db, tenant_a):
    staging = await _staging(db, tenant_a)
    lead = await make_lead(
        db, tenant_a, phone="+15550500003", environment_id=staging.id
    )
    with pytest.raises(NotFound):
        await service.update_lead(
            db, tenant_id=tenant_a.id, lead_id=lead.id, fields={"name": "Hostile"}
        )
    await db.rollback()
    await db.refresh(lead)
    assert lead.name == "Jane Doe"


async def test_status_change_from_the_wrong_environment_is_not_found(db, tenant_a):
    staging = await _staging(db, tenant_a)
    lead = await make_lead(
        db, tenant_a, phone="+15550500004", environment_id=staging.id
    )
    with pytest.raises(NotFound):
        await service.change_status(
            db, tenant_id=tenant_a.id, lead_id=lead.id, target="qualified",
            reason="cross-env",
        )
    await db.rollback()
    await db.refresh(lead)
    from app.db.models import LeadStatus

    assert lead.status is LeadStatus.NEW


async def test_activity_attachment_from_the_wrong_environment_is_denied(db, tenant_a):
    staging = await _staging(db, tenant_a)
    lead = await make_lead(
        db, tenant_a, phone="+15550500005", environment_id=staging.id
    )
    with pytest.raises(BoundaryDenied):
        await activities.record(
            db, lead, kind="note", summary="cross-env note",
            environment_id=(await _production(db, tenant_a)).id,
        )
    await db.rollback()


async def test_task_from_the_wrong_environment_is_not_found(db, tenant_a):
    staging = await _staging(db, tenant_a)
    lead = await make_lead(
        db, tenant_a, phone="+15550500006", environment_id=staging.id
    )
    with pytest.raises(NotFound):
        await service.open_task(
            db, tenant_id=tenant_a.id, lead_id=lead.id, title="Call back"
        )
    await db.rollback()


async def test_score_from_the_wrong_environment_is_not_found(db, tenant_a):
    staging = await _staging(db, tenant_a)
    lead = await make_lead(
        db, tenant_a, phone="+15550500007", environment_id=staging.id
    )
    with pytest.raises(NotFound):
        await service.score_lead(db, tenant_id=tenant_a.id, lead_id=lead.id)
    await db.rollback()


async def test_consent_from_the_wrong_environment_is_not_found(db, tenant_a):
    staging = await _staging(db, tenant_a)
    lead = await make_lead(
        db, tenant_a, phone="+15550500008", environment_id=staging.id
    )
    with pytest.raises(NotFound):
        await service.record_consent(
            db, tenant_id=tenant_a.id, lead_id=lead.id,
            channel="voice", decision="denied",
        )
    await db.rollback()
    await db.refresh(lead)
    from app.db.models import LeadStatus

    assert lead.status is LeadStatus.NEW  # untouched from production context


# ----------------------------------------------------- campaign binding ---

async def test_campaign_binding_must_share_the_leads_environment(db, tenant_a):
    staging = await _staging(db, tenant_a)
    production = await _production(db, tenant_a)
    production_campaign = Campaign(
        tenant_id=tenant_a.id, environment_id=production.id, name="Prod push",
    )
    staging_campaign = Campaign(
        tenant_id=tenant_a.id, environment_id=staging.id, name="Staging push",
    )
    db.add_all([production_campaign, staging_campaign])
    await db.commit()

    # staging lead + production campaign → refused like a foreign campaign
    with pytest.raises(NotFound):
        await service.create_lead(
            db, tenant_id=tenant_a.id, phone="+15550500009",
            campaign_id=production_campaign.id, environment_id=staging.id,
        )
    await db.rollback()

    # staging lead + staging campaign → accepted
    lead, created = await service.create_lead(
        db, tenant_id=tenant_a.id, phone="+15550500010",
        campaign_id=staging_campaign.id, environment_id=staging.id,
    )
    assert created is True
    assert lead.campaign_id == staging_campaign.id
    assert lead.environment_id == staging.id


async def test_update_cannot_rebind_to_a_cross_environment_campaign(db, tenant_a):
    staging = await _staging(db, tenant_a)
    production = await _production(db, tenant_a)
    production_campaign = Campaign(
        tenant_id=tenant_a.id, environment_id=production.id, name="Prod push 2",
    )
    db.add(production_campaign)
    await db.commit()
    lead = await make_lead(
        db, tenant_a, phone="+15550500011", environment_id=staging.id
    )

    with pytest.raises(NotFound):
        await service.update_lead(
            db, tenant_id=tenant_a.id, lead_id=lead.id,
            environment_id=staging.id,
            fields={"campaign_id": str(production_campaign.id)},
        )
    await db.rollback()


# --------------------------------------------------------- immutability ---

async def test_update_rejects_environment_id_as_a_field(db, tenant_a):
    staging = await _staging(db, tenant_a)
    lead = await make_lead(
        db, tenant_a, phone="+15550500012", environment_id=staging.id
    )
    with pytest.raises(ValidationFailed):
        await service.update_lead(
            db, tenant_id=tenant_a.id, lead_id=lead.id,
            environment_id=staging.id,
            fields={"environment_id": str((await _production(db, tenant_a)).id)},
        )
    await db.rollback()


async def test_orm_level_environment_move_is_frozen(db, tenant_a):
    """The resource-binding freeze hook is the backstop behind the service
    refusal: even a raw ORM attribute change cannot move a lead."""
    staging = await _staging(db, tenant_a)
    production = await _production(db, tenant_a)
    staging_id = staging.id  # capture before the rollback expires the objects
    lead = await make_lead(
        db, tenant_a, phone="+15550500013", environment_id=staging_id
    )
    lead.environment_id = production.id
    with pytest.raises(ImmutableEnvironment):
        await db.flush()
    await db.rollback()
    await db.refresh(lead)
    assert lead.environment_id == staging_id


async def test_create_lead_defaults_to_production_and_stamps_history(db, tenant_a):
    from app.leads.activities import history_for

    production = await _production(db, tenant_a)
    lead, created = await service.create_lead(
        db, tenant_id=tenant_a.id, phone="+15550500014"
    )
    assert created is True
    assert lead.environment_id == production.id
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert [h.to_status for h in history] == ["new"]
    assert history[0].from_status is None


async def test_supplied_foreign_environment_is_boundary_denied(db, tenant_a, tenant_b):
    foreign = await _production(db, tenant_b)
    foreign_id = foreign.id  # capture before the rollback expires the object
    with pytest.raises(BoundaryDenied):
        await service.create_lead(
            db, tenant_id=tenant_a.id, phone="+15550500015",
            environment_id=foreign_id,
        )
    await db.rollback()
    rows = (
        await db.execute(select(Environment).where(Environment.id == foreign_id))
    ).scalars().all()
    assert len(rows) == 1  # the foreign environment itself is untouched
