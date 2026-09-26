"""Environment lifecycle. Archive is a status. Promotion is a plan, not a deploy."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.db.models import Environment
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def _create(client, headers, tenant_id, kind):
    response = await client.post(
        f"/api/tenants/{tenant_id}/environments",
        json={"name": kind.title(), "kind": kind},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_suspend_archive_and_production_lock(client, db, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    development = await _create(client, headers, tenant_a.id, "development")
    staging = await _create(client, headers, tenant_a.id, "staging")

    duplicate = await client.post(
        f"/api/tenants/{tenant_a.id}/environments",
        json={"name": "Again", "kind": "production"},
        headers=headers,
    )
    assert duplicate.status_code == 409, duplicate.text

    suspended = await client.post(
        f"/api/tenants/{tenant_a.id}/environments/{staging['id']}/suspend",
        headers=headers,
    )
    assert suspended.status_code == 200, suspended.text
    again = await client.post(
        f"/api/tenants/{tenant_a.id}/environments/{staging['id']}/suspend",
        headers=headers,
    )
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "suspended"

    archived = await client.post(
        f"/api/tenants/{tenant_a.id}/environments/{staging['id']}/archive",
        headers=headers,
    )
    assert archived.status_code == 200, archived.text
    still = await db.get(Environment, uuid.UUID(staging["id"]))
    assert still is not None
    assert still.tenant_id == tenant_a.id

    production = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant_a.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()
    refused = await client.post(
        f"/api/tenants/{tenant_a.id}/environments/{production.id}/archive",
        headers=headers,
    )
    assert refused.status_code == 409, refused.text
    assert development["kind"] == "development"


async def test_promotion_plan_does_not_copy_or_deploy(client, db, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    tenant_a.crm_api_key = "do-not-copy"
    await db.commit()
    development = await _create(client, headers, tenant_a.id, "development")
    before = await db.scalar(
        select(func.count()).select_from(Environment).where(Environment.tenant_id == tenant_a.id)
    )

    plan = await client.post(
        f"/api/tenants/{tenant_a.id}/environments/{development['id']}/promotion-plan",
        json={"target_kind": "staging"},
        headers=headers,
    )
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["copies_secrets"] is False
    assert body["copies_configuration"] is False
    assert body["executes_deployment"] is False
    assert body["overwrites_production"] is False
    assert "crm_api_key" in body["withheld_fields"]

    skip = await client.post(
        f"/api/tenants/{tenant_a.id}/environments/{development['id']}/promotion-plan",
        json={"target_kind": "production"},
        headers=headers,
    )
    assert skip.status_code == 409, skip.text

    after = await db.scalar(
        select(func.count()).select_from(Environment).where(Environment.tenant_id == tenant_a.id)
    )
    assert after == before
    await db.refresh(tenant_a)
    assert tenant_a.crm_api_key == "do-not-copy"


async def test_cross_tenant_environment_is_not_found(client, tenant_a, tenant_b, owner_a, owner_b):
    owner = await auth_headers(client, owner_a)
    other = await auth_headers(client, owner_b)
    created = await _create(client, owner, tenant_a.id, "development")

    foreign = await client.get(
        f"/api/tenants/{tenant_b.id}/environments/{created['id']}",
        headers=other,
    )
    assert foreign.status_code == 404, foreign.text

    stolen = await client.post(
        f"/api/tenants/{tenant_a.id}/environments/{created['id']}/promotion-plan",
        json={"target_kind": "staging", "target_tenant_id": str(tenant_b.id)},
        headers=owner,
    )
    assert stolen.status_code == 404, stolen.text


async def test_suspended_tenant_cannot_create_environment(client, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    suspended = await client.post(f"/api/tenants/{tenant_a.id}/suspend", headers=headers)
    assert suspended.status_code == 200, suspended.text
    created = await client.post(
        f"/api/tenants/{tenant_a.id}/environments",
        json={"name": "Dev", "kind": "development"},
        headers=headers,
    )
    assert created.status_code == 409, created.text
    listed = await client.get(f"/api/tenants/{tenant_a.id}/environments", headers=headers)
    assert listed.status_code == 200, listed.text
