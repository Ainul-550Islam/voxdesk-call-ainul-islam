"""Query helpers never drop the tenant boundary."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import Environment, Lead
from app.environments.resource_queries import (
    active_environment_scope,
    environment_scope,
    tenant_and_environment_scope,
    tenant_scope,
)
from app.environments.resource_types import EnvironmentResourceType
from app.resources.repository import list_resources

pytestmark = pytest.mark.asyncio


async def test_combined_filter_excludes_other_environments_and_tenants(db, tenant_a, tenant_b):
    production = (await db.execute(
        select(Environment).where(
            Environment.tenant_id == tenant_a.id, Environment.kind == "production",
        )
    )).scalar_one()
    staging = Environment(
        tenant_id=tenant_a.id, name="Staging", slug="staging", kind="staging",
        status="active", is_default=False,
    )
    db.add(staging)
    await db.flush()
    foreign = (await db.execute(
        select(Environment).where(
            Environment.tenant_id == tenant_b.id, Environment.kind == "production",
        )
    )).scalar_one()
    db.add_all([
        Lead(tenant_id=tenant_a.id, environment_id=production.id, name="Home", phone="+15555000001"),
        Lead(tenant_id=tenant_a.id, environment_id=staging.id, name="Stage", phone="+15555000002"),
        Lead(tenant_id=tenant_b.id, environment_id=foreign.id, name="Other", phone="+15555000003"),
    ])
    await db.flush()
    rows, total = await list_resources(
        db, EnvironmentResourceType.LEAD,
        tenant_id=tenant_a.id, environment_id=production.id, limit=10, offset=0,
    )
    assert total == 1
    assert rows[0].name == "Home"
    tenant_only = (await db.execute(
        select(Lead).where(tenant_scope(Lead, tenant_a.id))
    )).scalars().all()
    assert {row.name for row in tenant_only} == {"Home", "Stage"}
    staged = (await db.execute(
        select(Lead).where(tenant_and_environment_scope(Lead, tenant_a.id, staging.id))
    )).scalars().all()
    assert [row.name for row in staged] == ["Stage"]
    assert (await db.execute(
        select(Lead).where(environment_scope(Lead, uuid.uuid4()))
    )).scalars().all() == []
    active = (await db.execute(
        select(Environment).where(
            Environment.tenant_id == tenant_a.id, active_environment_scope(Environment),
        )
    )).scalars().all()
    assert production in active
