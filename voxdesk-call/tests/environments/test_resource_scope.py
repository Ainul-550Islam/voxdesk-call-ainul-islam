"""Scope resolution, including the legacy production fallback."""

from __future__ import annotations

import uuid

import pytest

from app.db.models import Environment, EnvironmentSelection
from app.environments.resource_scope import resolve_scope
from app.resources.exceptions import ResourceScopeError
from app.tenancy.isolation import BoundaryDenied

pytestmark = pytest.mark.asyncio


async def _production(db, tenant) -> Environment:
    from sqlalchemy import select
    return (await db.execute(
        select(Environment).where(
            Environment.tenant_id == tenant.id, Environment.kind == "production",
        )
    )).scalar_one()


async def test_explicit_environment_wins(db, tenant_a, owner_a):
    staging = Environment(
        tenant_id=tenant_a.id, name="Staging", slug="staging", kind="staging",
        status="active", is_default=False,
    )
    db.add(staging)
    await db.flush()
    resolved = await resolve_scope(
        db, tenant_id=tenant_a.id, user_id=owner_a.id,
        explicit_environment_id=staging.id, for_write=True,
    )
    assert resolved.id == staging.id


async def test_selected_environment_is_used_when_no_explicit_id(db, tenant_a, owner_a):
    staging = Environment(
        tenant_id=tenant_a.id, name="Staging", slug="staging", kind="staging",
        status="active", is_default=False,
    )
    db.add(staging)
    await db.flush()
    db.add(EnvironmentSelection(
        user_id=owner_a.id, tenant_id=tenant_a.id, environment_id=staging.id,
    ))
    await db.flush()
    resolved = await resolve_scope(db, tenant_id=tenant_a.id, user_id=owner_a.id, for_write=True)
    assert resolved.id == staging.id


async def test_legacy_resolution_uses_active_default_production(db, tenant_a):
    production = await _production(db, tenant_a)
    resolved = await resolve_scope(db, tenant_id=tenant_a.id, for_write=True)
    assert resolved.id == production.id
    assert resolved.kind == "production"
    assert resolved.is_default is True


async def test_archived_or_suspended_default_is_not_used_for_a_write(db, tenant_a):
    production = await _production(db, tenant_a)
    production.status = "archived"
    await db.flush()
    with pytest.raises(ResourceScopeError):
        await resolve_scope(db, tenant_id=tenant_a.id, for_write=True)


async def test_a_cross_tenant_environment_id_is_rejected(db, tenant_a, tenant_b):
    foreign = await _production(db, tenant_b)
    with pytest.raises(BoundaryDenied):
        await resolve_scope(
            db, tenant_id=tenant_a.id, explicit_environment_id=foreign.id, for_write=True,
        )


async def test_a_malformed_environment_id_does_not_resolve(db, tenant_a):
    with pytest.raises(BoundaryDenied):
        await resolve_scope(
            db, tenant_id=tenant_a.id, explicit_environment_id=uuid.uuid4(), for_write=False,
        )
