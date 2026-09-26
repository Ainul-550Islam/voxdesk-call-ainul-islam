"""Organization service rules: uniqueness, lifecycle, and no hard delete."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.db.models import AuditAction, AuditLog, Organization, Tenant
from app.organization.service import (
    Actor,
    create_organization,
    transition_organization,
    update_organization,
)
from app.tenancy.isolation import Conflict, LifecycleDenied, ValidationFailed
from app.tenancy.service import create_tenant_under_organization
from tests.conftest import make_call, make_tenant

pytestmark = pytest.mark.asyncio


async def test_create_update_and_slug_collision(db):
    created = await create_organization(db, name="North Clinic", slug="north-clinic", commit=True)
    assert created.status == "active"
    assert created.slug == "north-clinic"

    renamed = await update_organization(db, created, name="North Clinic West", commit=True)
    assert renamed.name == "North Clinic West"
    assert renamed.id == created.id

    with pytest.raises(Conflict):
        await create_organization(db, name="Other", slug="north-clinic")
    with pytest.raises(ValidationFailed):
        await create_organization(db, name="Bad", slug="Not A Slug")
    with pytest.raises(ValidationFailed):
        await create_organization(db, name="   ")


async def test_suspend_is_idempotent_and_does_not_delete_tenants(db):
    organization = await create_organization(db, name="Hold", slug="hold-org", commit=True)
    tenant = await create_tenant_under_organization(
        db,
        organization,
        name="Hold Dental",
        twilio_number="+15550007771",
        commit=True,
    )
    call = await make_call(db, tenant)
    before = tenant.id

    once = await transition_organization(db, organization, "suspended", actor=Actor(), commit=True)
    twice = await transition_organization(db, once, "suspended", actor=Actor(), commit=True)
    assert twice.status == "suspended"
    assert twice.id == organization.id

    still = await db.get(Tenant, before)
    assert still is not None
    assert still.organization_id == organization.id
    assert await db.get(type(call), call.id) is not None

    entries = (
        await db.execute(
            select(AuditLog).where(AuditLog.action == AuditAction.ORGANIZATION_SUSPENDED)
        )
    ).scalars().all()
    assert len(entries) == 1
    assert entries[0].detail["organization_id"] == str(organization.id)
    assert "password" not in str(entries[0].detail)
    rendered = str(entries[0].detail)
    assert "secret" not in rendered


async def test_deleted_is_terminal_and_blocks_new_tenants(db):
    organization = await create_organization(db, name="Gone", slug="gone-org", commit=True)
    await transition_organization(db, organization, "deleted", commit=True)
    with pytest.raises(LifecycleDenied):
        await transition_organization(db, organization, "active")
    with pytest.raises(LifecycleDenied):
        await update_organization(db, organization, name="Revived")
    with pytest.raises(LifecycleDenied):
        await create_tenant_under_organization(
            db,
            organization,
            name="Should Fail",
            twilio_number="+15550007772",
        )
    assert await db.get(Organization, organization.id) is not None


async def test_suspended_organization_rejects_a_new_tenant_and_keeps_the_old_one(db):
    organization = await create_organization(db, name="Frozen", slug="frozen-org", commit=True)
    existing = await create_tenant_under_organization(
        db,
        organization,
        name="Already There",
        twilio_number="+15550007773",
        commit=True,
    )
    await transition_organization(db, organization, "suspended", commit=True)
    with pytest.raises(LifecycleDenied):
        await create_tenant_under_organization(
            db,
            organization,
            name="Too Late",
            twilio_number="+15550007774",
        )
    assert (await db.get(Tenant, existing.id)).name == "Already There"
    # The legacy insert path still works for other businesses and does not
    # join this suspended organization.
    other = await make_tenant(db, "Separate")
    assert other.organization_id != organization.id
    total = (await db.execute(select(func.count()).select_from(Organization))).scalar()
    assert total == 2
