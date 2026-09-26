"""Hierarchy resolution and environment rules. Client ids are not authority."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import Environment, UserRole
from app.environments.service import describe_baseline
from app.organization.service import create_organization
from app.tenancy.service import create_tenant_under_organization
from tests.conftest import auth_headers, make_user

pytestmark = pytest.mark.asyncio


async def test_hierarchy_is_server_derived(client, db, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    production = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant_a.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()
    response = await client.get(f"/api/tenants/{tenant_a.id}/hierarchy", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["organization_id"] == str(tenant_a.organization_id)
    assert body["tenant_id"] == str(tenant_a.id)
    assert body["environment_id"] is None
    assert body["default_environment_id"] == str(production.id)
    assert body["is_machine"] is False

    named = await client.get(
        f"/api/tenants/{tenant_a.id}/hierarchy?environment_id={production.id}",
        headers=headers,
    )
    assert named.status_code == 200, named.text
    assert named.json()["environment_kind"] == "production"


async def test_a_foreign_environment_id_is_not_found(client, db, tenant_a, tenant_b, owner_a):
    headers = await auth_headers(client, owner_a)
    foreign = (
        await db.execute(select(Environment).where(Environment.tenant_id == tenant_b.id))
    ).scalar_one()
    by_query = await client.get(
        f"/api/tenants/{tenant_a.id}/hierarchy?environment_id={foreign.id}",
        headers=headers,
    )
    by_path = await client.get(
        f"/api/tenants/{tenant_b.id}/hierarchy?environment_id={foreign.id}",
        headers=headers,
    )
    missing = await client.get(
        f"/api/tenants/{tenant_a.id}/hierarchy?environment_id={uuid.uuid4()}",
        headers=headers,
    )
    assert by_query.status_code == by_path.status_code == missing.status_code == 404
    assert by_query.json() == by_path.json() == missing.json()


async def test_profile_update_ignores_client_hierarchy_ids(client, tenant_a, tenant_b, admin_a):
    headers = await auth_headers(client, admin_a)
    response = await client.patch(
        f"/api/tenants/{tenant_a.id}/profile",
        json={
            "name": "Renamed Workspace",
            "organization_id": str(tenant_b.organization_id),
            "tenant_id": str(tenant_b.id),
            "environment_id": str(uuid.uuid4()),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "Renamed Workspace"
    assert body["id"] == str(tenant_a.id)
    assert body["organization_id"] == str(tenant_a.organization_id)


async def test_http_lists_only_the_member_tenant_even_when_the_org_has_two(client, db):
    organization = await create_organization(db, name="Shared", slug="shared-org", commit=True)
    first = await create_tenant_under_organization(
        db, organization, name="First", twilio_number="+15552220001", commit=True
    )
    second = await create_tenant_under_organization(
        db, organization, name="Second", twilio_number="+15552220002", commit=True
    )
    user = await make_user(db, first, UserRole.OWNER)
    headers = await auth_headers(client, user)

    listed = await client.get(
        f"/api/organizations/{organization.id}/tenants",
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [str(first.id)]

    hidden = await client.get(f"/api/tenants/{second.id}/hierarchy", headers=headers)
    assert hidden.status_code == 404, hidden.text


async def test_staging_can_be_added_production_cannot_be_duplicated_or_archived(
    client, db, tenant_a, owner_a
):
    headers = await auth_headers(client, owner_a)
    staging = await client.post(
        f"/api/tenants/{tenant_a.id}/environments",
        json={"name": "Staging", "kind": "staging"},
        headers=headers,
    )
    assert staging.status_code == 201, staging.text
    assert staging.json()["kind"] == "staging"
    assert staging.json()["tenant_id"] == str(tenant_a.id)

    duplicate = await client.post(
        f"/api/tenants/{tenant_a.id}/environments",
        json={"name": "Another Production", "kind": "production"},
        headers=headers,
    )
    assert duplicate.status_code == 409, duplicate.text

    production = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant_a.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()
    archived = await client.post(
        f"/api/tenants/{tenant_a.id}/environments/{production.id}/archive",
        headers=headers,
    )
    assert archived.status_code == 409, archived.text

    baseline = describe_baseline("production")
    assert baseline["executes_deployment"] is False
    assert baseline["production_constraints"] is True


async def test_deployment_metadata_is_recorded_and_not_executed(client, db, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    production = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant_a.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()
    recorded = await client.post(
        f"/api/tenants/{tenant_a.id}/environments/{production.id}/deployment",
        json={"release_version": "2026.9.23", "source": "manual", "health_state": "healthy"},
        headers=headers,
    )
    assert recorded.status_code == 200, recorded.text
    assert recorded.json()["executed"] is False
    assert recorded.json()["status"] == "recorded"
    assert recorded.json()["release_version"] == "2026.9.23"

    leaked = await client.post(
        f"/api/tenants/{tenant_a.id}/environments/{production.id}/deployment",
        json={"release_version": "v2", "source": "Bearer super-secret-token"},
        headers=headers,
    )
    assert leaked.status_code == 422, leaked.text
    assert "super-secret-token" not in leaked.text
