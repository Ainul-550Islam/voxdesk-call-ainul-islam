"""Tenant and environment isolation. A miss and a cross-boundary read look the same."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.auth.identity import api_keys as key_service
from app.auth.permissions import Permission
from app.db.models import Environment
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def test_tenant_a_cannot_read_tenant_b(client, tenant_a, tenant_b, owner_a, owner_b):
    headers = await auth_headers(client, owner_a)
    foreign = await client.get(f"/api/tenants/{tenant_b.id}/hierarchy", headers=headers)
    missing = await client.get(f"/api/tenants/{uuid.uuid4()}/hierarchy", headers=headers)
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert foreign.json() == missing.json()

    own = await client.get(f"/api/tenants/{tenant_a.id}/hierarchy", headers=headers)
    assert own.status_code == 200, own.text
    assert own.json()["tenant_id"] == str(tenant_a.id)

    other_headers = await auth_headers(client, owner_b)
    other_own = await client.get(f"/api/tenants/{tenant_b.id}/hierarchy", headers=other_headers)
    assert other_own.status_code == 200
    assert other_own.json()["organization_id"] == str(tenant_b.organization_id)
    assert other_own.json()["organization_id"] != own.json()["organization_id"]


async def test_environment_ids_do_not_cross_tenants(client, db, tenant_a, tenant_b, owner_a):
    headers = await auth_headers(client, owner_a)
    foreign = (
        await db.execute(select(Environment).where(Environment.tenant_id == tenant_b.id))
    ).scalar_one()
    via_own_path = await client.get(
        f"/api/tenants/{tenant_a.id}/environments/{foreign.id}",
        headers=headers,
    )
    via_their_path = await client.get(
        f"/api/tenants/{tenant_b.id}/environments/{foreign.id}",
        headers=headers,
    )
    missing = await client.get(
        f"/api/tenants/{tenant_a.id}/environments/{uuid.uuid4()}",
        headers=headers,
    )
    assert via_own_path.status_code == via_their_path.status_code == missing.status_code == 404
    assert via_own_path.json() == via_their_path.json() == missing.json()


async def test_machine_credentials_stay_inside_the_issuing_tenant(
    client, tenant_a, tenant_b, owner_a
):
    headers = await auth_headers(client, owner_a)
    created = await client.post(
        "/api/api-keys",
        json={
            "name": "tenant writer",
            "scopes": key_service.scopes_from_permissions(
                [Permission.TENANT_READ, Permission.TENANT_UPDATE]
            ),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    secret = created.json()["secret"]
    bearer = {"Authorization": f"Bearer {secret}"}

    own = await client.get(f"/api/tenants/{tenant_a.id}/environments", headers=bearer)
    assert own.status_code == 200, own.text

    foreign = await client.get(f"/api/tenants/{tenant_b.id}/environments", headers=bearer)
    assert foreign.status_code == 404, foreign.text
    assert secret not in foreign.text

    # A key is not a person. Lifecycle writes stay closed even in its own tenant.
    refused = await client.post(
        f"/api/tenants/{tenant_a.id}/suspend",
        headers=bearer,
    )
    assert refused.status_code == 403, refused.text


async def test_organization_tenant_list_does_not_leak_the_other_organization(
    client, tenant_a, tenant_b, owner_a
):
    headers = await auth_headers(client, owner_a)
    foreign = await client.get(
        f"/api/organizations/{tenant_b.organization_id}/tenants",
        headers=headers,
    )
    missing = await client.get(
        f"/api/organizations/{uuid.uuid4()}/tenants",
        headers=headers,
    )
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert foreign.json() == missing.json()
