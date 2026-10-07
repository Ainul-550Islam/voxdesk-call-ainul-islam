"""Schema and backfill for the organization foundation.

Empty databases stay empty. A database that already has tenants gets one
organization and one production environment per tenant, without new ids and
without a shared Default Organization.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditAction,
    Call,
    Environment,
    Organization,
    Subscription,
    Tenant,
    User,
    UserRole,
)
from app.organization.models import (
    OrganizationStatus,
    legacy_organization_name,
    legacy_organization_slug,
)
from tests.conftest import make_call, make_tenant, make_user, subscribe

pytestmark = pytest.mark.asyncio

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "0017_org_environment_foundation.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("m0017", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_an_empty_database_has_no_synthetic_organization(engine):
    async with engine.connect() as conn:
        organizations = (await conn.execute(text("SELECT COUNT(*) FROM organizations"))).scalar()
        environments = (await conn.execute(text("SELECT COUNT(*) FROM environments"))).scalar()
    assert organizations == 0
    assert environments == 0


async def test_a_legacy_insert_gets_its_own_organization_and_production(db: AsyncSession):
    tenant = await make_tenant(db, "Bright Smile")
    assert tenant.organization_id is not None
    assert tenant.lifecycle_status == "active"
    assert tenant.is_active is True

    organization = await db.get(Organization, tenant.organization_id)
    assert organization is not None
    assert organization.name == "Bright Smile"
    assert organization.slug == legacy_organization_slug(tenant.id)
    assert organization.status == OrganizationStatus.ACTIVE.value
    assert "Default Organization" not in organization.name

    environments = (
        await db.execute(select(Environment).where(Environment.tenant_id == tenant.id))
    ).scalars().all()
    assert len(environments) == 1
    assert environments[0].kind == "production"
    assert environments[0].is_default is True
    assert environments[0].production_guard == tenant.id


async def test_two_tenants_are_not_folded_into_one_organization(db: AsyncSession):
    first = await make_tenant(db, "Alpha")
    second = await make_tenant(db, "Beta")
    first_id, second_id = first.id, second.id

    assert first.organization_id != second.organization_id
    assert (await db.get(Tenant, first_id)).id == first_id
    assert (await db.get(Tenant, second_id)).id == second_id
    count = (await db.execute(select(func.count()).select_from(Organization))).scalar()
    assert count == 2


async def test_users_calls_and_billing_keep_their_tenant_id(db, tenant_a, billing_plans):
    user = await make_user(db, tenant_a, UserRole.OWNER)
    call = await make_call(db, tenant_a)
    subscription = await subscribe(db, tenant_a)
    original = tenant_a.id

    await db.refresh(tenant_a)
    assert tenant_a.organization_id is not None
    assert (await db.get(User, user.id)).tenant_id == original
    assert (await db.get(Call, call.id)).tenant_id == original
    assert (await db.get(Subscription, subscription.id)).tenant_id == original


def test_backfill_is_empty_safe_then_one_org_per_tenant_and_idempotent(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'hierarchy.db'}")
    migration = _migration()
    tenant_id = uuid.uuid4()
    other_id = uuid.uuid4()
    user_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE tenants ("
            "id VARCHAR(36) PRIMARY KEY, name VARCHAR(200), "
            "organization_id VARCHAR(36), lifecycle_status VARCHAR(16) DEFAULT 'active', "
            "twilio_number VARCHAR(32))"
        ))
        conn.execute(sa.text(
            "CREATE TABLE organizations ("
            "id VARCHAR(36) PRIMARY KEY, name VARCHAR(200) NOT NULL, "
            "slug VARCHAR(63) NOT NULL UNIQUE, status VARCHAR(16) NOT NULL, "
            "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        ))
        conn.execute(sa.text(
            "CREATE TABLE environments ("
            "id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL, "
            "name VARCHAR(120) NOT NULL, slug VARCHAR(63) NOT NULL, kind VARCHAR(16) NOT NULL, "
            "status VARCHAR(16) NOT NULL, is_default BOOLEAN NOT NULL, "
            "production_guard VARCHAR(36), default_guard VARCHAR(36), "
            "release_version VARCHAR(64) NOT NULL, deployed_at DATETIME, "
            "deployment_status VARCHAR(16) NOT NULL, deployment_source VARCHAR(64) NOT NULL, "
            "health_state VARCHAR(16) NOT NULL, created_at DATETIME NOT NULL, "
            "updated_at DATETIME NOT NULL)"
        ))
        conn.execute(sa.text(
            "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36) NOT NULL)"
        ))
        assert migration.backfill_tenants(conn) == 0

        conn.execute(
            sa.text("INSERT INTO tenants (id, name, twilio_number) VALUES (:id, :name, :number)"),
            [
                {"id": str(tenant_id), "name": "Kept Name", "number": "+15551110001"},
                {"id": str(other_id), "name": "", "number": "+15551110002"},
            ],
        )
        conn.execute(
            sa.text("INSERT INTO users (id, tenant_id) VALUES (:id, :tenant_id)"),
            {"id": str(user_id), "tenant_id": str(tenant_id)},
        )
        assert migration.backfill_tenants(conn) == 2
        assert migration.backfill_tenants(conn) == 0

        tenants = conn.execute(sa.text(
            "SELECT id, name, twilio_number, organization_id FROM tenants ORDER BY twilio_number"
        )).mappings().all()
        assert [row["id"] for row in tenants] == [str(tenant_id), str(other_id)]
        assert tenants[0]["name"] == "Kept Name"
        assert tenants[0]["twilio_number"] == "+15551110001"
        assert tenants[0]["organization_id"] != tenants[1]["organization_id"]

        orgs = conn.execute(sa.text("SELECT name, slug FROM organizations")).mappings().all()
        names = {row["name"] for row in orgs}
        slugs = {row["slug"] for row in orgs}
        assert names == {"Kept Name", legacy_organization_name("")}
        assert slugs == {
            legacy_organization_slug(tenant_id),
            legacy_organization_slug(other_id),
        }
        assert "Default Organization" not in names

        productions = conn.execute(sa.text(
            "SELECT tenant_id, kind FROM environments WHERE kind = 'production'"
        )).mappings().all()
        assert {row["tenant_id"] for row in productions} == {str(tenant_id), str(other_id)}
        assert conn.execute(sa.text("SELECT tenant_id FROM users")).scalar() == str(tenant_id)


def test_new_audit_actions_are_member_names():
    expected = {
        "ORGANIZATION_CREATED",
        "ORGANIZATION_UPDATED",
        "ORGANIZATION_SUSPENDED",
        "ORGANIZATION_RESTORED",
        "ORGANIZATION_READ_ONLY",
        "TENANT_LIFECYCLE_CHANGED",
        "ENVIRONMENT_CREATED",
        "ENVIRONMENT_UPDATED",
        "ENVIRONMENT_SUSPENDED",
        "ENVIRONMENT_RESTORED",
        "ENVIRONMENT_ARCHIVED",
        "ENVIRONMENT_DEFAULT_CHANGED",
    }
    assert expected <= {action.name for action in AuditAction}
    source = _MIGRATION.read_text()
    for name in expected:
        assert name in source
