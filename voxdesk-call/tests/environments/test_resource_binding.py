"""Tenant, environment and resource binding."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Call, CallStatus, Environment, Lead
from app.environments.resource_binding import (
    ImmutableEnvironment,
    assert_environment_accepts_write,
    assert_environment_belongs_to_tenant,
)
from app.resources.service import bind_new_lead, refuse_rebind
from app.tenancy.isolation import BoundaryDenied, LifecycleDenied

pytestmark = pytest.mark.asyncio


async def _production(db, tenant) -> Environment:
    from sqlalchemy import select
    return (await db.execute(
        select(Environment).where(
            Environment.tenant_id == tenant.id, Environment.kind == "production",
        )
    )).scalar_one()


async def test_a_new_lead_binds_to_the_tenants_production_environment(db, tenant_a):
    production = await _production(db, tenant_a)
    lead = Lead(tenant_id=tenant_a.id, name="Ada", phone="+15550001111")
    db.add(lead)
    await db.flush()
    assert lead.environment_id == production.id
    assert_environment_belongs_to_tenant(production, tenant_a.id)


async def test_an_explicit_same_tenant_environment_is_accepted(db, tenant_a):
    staging = Environment(
        tenant_id=tenant_a.id, name="Staging", slug="staging", kind="staging",
        status="active", is_default=False,
    )
    db.add(staging)
    await db.flush()
    lead = await bind_new_lead(
        db, tenant_id=tenant_a.id, environment=staging, name="Bea", phone="+15550002222",
    )
    assert lead.environment_id == staging.id


async def test_a_cross_tenant_environment_is_rejected(db, tenant_a, tenant_b):
    foreign = await _production(db, tenant_b)
    with pytest.raises(BoundaryDenied):
        assert_environment_belongs_to_tenant(foreign, tenant_a.id)
    lead = Lead(tenant_id=tenant_a.id, environment_id=foreign.id, name="No", phone="+15550003333")
    db.add(lead)
    with pytest.raises(BoundaryDenied):
        await db.flush()
    await db.rollback()


async def test_a_missing_environment_is_rejected_for_a_new_scoped_write(db, tenant_a):
    missing = uuid.uuid4()
    lead = Lead(tenant_id=tenant_a.id, environment_id=missing, name="No", phone="+15550004444")
    db.add(lead)
    with pytest.raises(BoundaryDenied):
        await db.flush()
    await db.rollback()


async def test_archived_and_suspended_environments_reject_writes(db, tenant_a):
    production = await _production(db, tenant_a)
    production.status = "suspended"
    await db.flush()
    with pytest.raises(LifecycleDenied):
        assert_environment_accepts_write(production)
    lead = Lead(tenant_id=tenant_a.id, name="Late", phone="+15550005555")
    db.add(lead)
    with pytest.raises(LifecycleDenied):
        await db.flush()
    await db.rollback()


async def test_environment_id_is_immutable_after_insert(db, tenant_a):
    production = await _production(db, tenant_a)
    staging = Environment(
        tenant_id=tenant_a.id, name="Staging", slug="staging", kind="staging",
        status="active", is_default=False,
    )
    db.add(staging)
    call = Call(
        tenant_id=tenant_a.id, call_sid=f"bind-{uuid.uuid4().hex[:8]}",
        from_number="+15551110000", to_number=tenant_a.twilio_number,
        status=CallStatus.RINGING,
    )
    db.add(call)
    await db.flush()
    assert call.environment_id == production.id
    call.environment_id = staging.id
    with pytest.raises(ImmutableEnvironment):
        await db.flush()
    await db.rollback()
    refuse_rebind(call, staging.id)


async def test_a_foreign_key_to_another_tenants_environment_is_not_stored(db, tenant_a, tenant_b):
    foreign = await _production(db, tenant_b)
    lead = Lead(tenant_id=tenant_a.id, environment_id=foreign.id, name="X", phone="+15550006666")
    db.add(lead)
    with pytest.raises((BoundaryDenied, IntegrityError)):
        await db.flush()
    await db.rollback()
