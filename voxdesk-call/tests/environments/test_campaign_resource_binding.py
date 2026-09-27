"""Campaign ↔ environment resource binding (Batch 06).

The campaigns table follows the same environment conventions as leads,
calls and appointments: a NOT NULL ``environment_id``, the single-column FK
with ``ON DELETE RESTRICT``, the composite ``(tenant_id, environment_id)``
FK against ``uq_environments_tenant_identity`` and the two scope indexes.

Binding reuses the *existing* scope machinery rather than adding a new one:
the ``before_insert``/``before_update`` hooks at the bottom of
``app/db/models.py`` delegate to ``app.environments.resource_binding``
(``_assign_scope``/``_freeze_scope``), so a legacy ``Campaign(tenant_id=...)``
insert binds to the tenant's active production environment exactly like a
legacy ``Lead(...)`` insert, cross-tenant and closed environments fail
closed, and the environment can never move after insert.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.errors import NotFoundError
from app.db.models import Base, Campaign, Environment, Lead
from app.domain.campaign_models import Audience, CampaignDefinition
from app.environments.resource_binding import ImmutableEnvironment
from app.services import campaign_service
from app.tenancy.isolation import BoundaryDenied, LifecycleDenied
from tests.conftest import make_lead, make_tenant


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


async def _staging(db, tenant, *, status: str = "active") -> Environment:
    environment = Environment(
        tenant_id=tenant.id, name="Staging", slug="staging", kind="staging",
        status=status, is_default=False,
    )
    db.add(environment)
    await db.commit()
    await db.refresh(environment)
    return environment


def _definition(tenant_id: str, *, environment_id: str = "", name: str = "Bound") -> CampaignDefinition:
    return CampaignDefinition(
        id=f"campaign-{uuid.uuid4().hex[:8]}", tenant_id=tenant_id, name=name,
        environment_id=environment_id,
        audience=Audience(tenant_id=tenant_id, environment_id=environment_id),
    )


# ------------------------------------------------------------ schema shape ---

def test_campaign_table_matches_the_lead_conventions():
    table = Campaign.__table__
    column = table.columns["environment_id"]
    assert column.nullable is False
    assert column.index is True

    # the column carries both the single-column FK (ON DELETE RESTRICT) and
    # its half of the composite FK — the same shape leads use
    fks = column.foreign_keys
    assert fks
    assert all(fk.column.table.name == "environments" for fk in fks)
    assert any(
        fk.column.name == "id" and fk.ondelete == "RESTRICT" for fk in fks
    )

    named = {c.name: c for c in table.constraints if c.name}
    assert "fk_campaigns_tenant_environment" in named
    pair = named["fk_campaigns_tenant_environment"]
    assert [c.name for c in pair.columns] == ["tenant_id", "environment_id"]
    assert [
        (element.column.table.name, element.column.name) for element in pair.elements
    ] == [("environments", "tenant_id"), ("environments", "id")]

    index_names = {ix.name for ix in table.indexes}
    assert "ix_campaigns_environment_id" in index_names
    assert "ix_campaigns_tenant_environment" in index_names

    # the referenced unique constraint must exist for PostgreSQL to accept
    # the composite FK (this is what makes cross-tenant pointers impossible)
    env_constraints = {c.name for c in Environment.__table__.constraints if c.name}
    assert "uq_environments_tenant_identity" in env_constraints


def test_lead_and_campaign_use_the_same_column_conventions():
    lead_col = Lead.__table__.columns["environment_id"]
    campaign_col = Campaign.__table__.columns["environment_id"]
    assert lead_col.nullable == campaign_col.nullable is False
    assert lead_col.index == campaign_col.index is True


# ------------------------------------------------------------- ORM binding ---

async def test_legacy_insert_without_environment_binds_production(db, tenant_a):
    """The pre-0026 insert shape keeps working and can never produce an
    environment-less campaign: the before_insert hook binds the tenant's
    active production environment, exactly like legacy Lead inserts."""
    production = await _production(db, tenant_a)
    campaign = Campaign(tenant_id=tenant_a.id, name="Legacy shape")
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    assert campaign.environment_id == production.id


async def test_insert_with_explicit_environment_persists(db, tenant_a):
    staging = await _staging(db, tenant_a)
    campaign = Campaign(
        tenant_id=tenant_a.id, environment_id=staging.id, name="Staging push"
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    assert campaign.environment_id == staging.id


async def test_insert_with_a_cross_tenant_environment_is_denied(db, tenant_a, tenant_b):
    foreign = await _production(db, tenant_b)
    campaign = Campaign(
        tenant_id=tenant_a.id, environment_id=foreign.id, name="Hostile"
    )
    db.add(campaign)
    with pytest.raises(BoundaryDenied):
        await db.flush()
    await db.rollback()


async def test_insert_into_a_closed_environment_is_denied(db, tenant_a):
    suspended = await _staging(db, tenant_a, status="suspended")
    campaign = Campaign(
        tenant_id=tenant_a.id, environment_id=suspended.id, name="Suspended"
    )
    db.add(campaign)
    with pytest.raises(LifecycleDenied):
        await db.flush()
    await db.rollback()


async def test_campaign_environment_is_immutable_after_insert(db, tenant_a):
    staging = await _staging(db, tenant_a)
    production = await _production(db, tenant_a)
    staging_id, production_id = staging.id, production.id
    campaign = Campaign(
        tenant_id=tenant_a.id, environment_id=staging_id, name="Frozen"
    )
    db.add(campaign)
    await db.commit()

    campaign.environment_id = production_id
    with pytest.raises(ImmutableEnvironment):
        await db.flush()
    await db.rollback()
    await db.refresh(campaign)
    assert campaign.environment_id == staging_id


# --------------------------------------------------------- service binding ---

async def test_service_binds_the_default_production_environment(db, tenant_a):
    production = await _production(db, tenant_a)
    row = await campaign_service.create_campaign(
        db, tenant_a, _definition(str(tenant_a.id))
    )
    assert row.environment_id == production.id


async def test_service_refuses_a_cross_tenant_environment(db, tenant_a, tenant_b):
    foreign = await _production(db, tenant_b)
    with pytest.raises(NotFoundError):  # service layer maps boundary misses
        await campaign_service.create_campaign(
            db, tenant_a,
            _definition(str(tenant_a.id), environment_id=str(foreign.id),
                        name="Hostile"),
        )
    await db.rollback()


# ------------------------------------------------------- lifecycle gating ---

async def test_suspended_environment_refuses_campaign_writes(db, tenant_a):
    suspended = await _staging(db, tenant_a, status="suspended")
    with pytest.raises(LifecycleDenied):
        await campaign_service.create_campaign(
            db, tenant_a,
            _definition(str(tenant_a.id), environment_id=str(suspended.id),
                        name="Suspended write"),
        )


async def test_archived_environment_refuses_campaign_writes(db, tenant_a):
    archived = await _staging(db, tenant_a, status="archived")
    with pytest.raises(LifecycleDenied):
        await campaign_service.create_campaign(
            db, tenant_a,
            _definition(str(tenant_a.id), environment_id=str(archived.id),
                        name="Archived write"),
        )


async def test_suspended_production_refuses_default_campaign_creation(db, tenant_a):
    """A tenant whose only production environment is suspended cannot create
    campaigns in the default context — the resolver only ever returns an
    *active* production environment and never invents or falls back to
    another one, so the miss is a safe not-found."""
    production = await _production(db, tenant_a)
    production.status = "suspended"
    await db.commit()
    with pytest.raises(NotFoundError):
        await campaign_service.create_campaign(
            db, tenant_a, _definition(str(tenant_a.id), name="No home")
        )


# --------------------------------------------------- lead/campaign pairing ---

async def test_campaign_lead_pair_must_share_tenant_and_environment(db, tenant_a, tenant_b):
    from app.leads.repository import campaign_in_scope

    staging = await _staging(db, tenant_a)
    production = await _production(db, tenant_a)
    staging_campaign = Campaign(
        tenant_id=tenant_a.id, environment_id=staging.id, name="S"
    )
    db.add(staging_campaign)
    await db.commit()

    production_lead = await make_lead(db, tenant_a, phone="+15550600001")
    assert production_lead.environment_id == production.id  # lead hooks intact

    # same tenant, different environment → None (safe, no boundary detail)
    assert await campaign_in_scope(
        db, tenant_a.id, production_lead.environment_id, staging_campaign.id
    ) is None
    # same pair → the campaign
    assert await campaign_in_scope(
        db, tenant_a.id, staging.id, staging_campaign.id
    ) is not None
    # foreign tenant → None as well
    assert await campaign_in_scope(
        db, tenant_b.id, production_lead.environment_id, staging_campaign.id
    ) is None


async def test_lead_hooks_still_bind_freeze_and_deny():
    """Regression guard: adding campaigns to the environment conventions did
    not change how leads bind. Leads still auto-bind to production, refuse
    cross-tenant environments, freeze their environment after insert and
    refuse writes into closed environments."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with maker() as session:
        tenant = await make_tenant(session, "Hook Co")
        foreign = await make_tenant(session, "Foreign Co")
        production = await _production(session, tenant)
        foreign_production_id = (await _production(session, foreign)).id
        production_id = production.id

        lead = Lead(tenant_id=tenant.id, name="Auto", phone="+15550600002")
        session.add(lead)
        await session.commit()
        assert lead.environment_id == production_id

        hostile = Lead(
            tenant_id=tenant.id, name="Hostile", phone="+15550600003",
            environment_id=foreign_production_id,
        )
        session.add(hostile)
        with pytest.raises(BoundaryDenied):
            await session.flush()
        await session.rollback()

        await session.refresh(lead)
        lead.environment_id = foreign_production_id
        with pytest.raises(ImmutableEnvironment):
            await session.flush()
        await session.rollback()

    await engine.dispose()
