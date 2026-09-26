"""Environment resource API authorization."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import Environment
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def _production(db, tenant) -> Environment:
    return (await db.execute(
        select(Environment).where(
            Environment.tenant_id == tenant.id, Environment.kind == "production",
        )
    )).scalar_one()


async def test_owner_can_read_and_create_and_viewer_cannot_write(
    client, db, tenant_a, owner_a, viewer_a
):
    production = await _production(db, tenant_a)
    owner = await auth_headers(client, owner_a)
    listed = await client.get(
        f"/api/tenants/{tenant_a.id}/environments/{production.id}/resources",
        headers=owner,
    )
    assert listed.status_code == 200, listed.text
    assert "lead" in listed.json()["resources"]

    created = await client.post(
        f"/api/tenants/{tenant_a.id}/environments/{production.id}/resources/leads",
        json={"name": "Ada", "phone": "+15551230001"},
        headers=owner,
    )
    assert created.status_code == 201, created.text
    assert created.json()["environment_id"] == str(production.id)

    viewer = await auth_headers(client, viewer_a)
    refused = await client.post(
        f"/api/tenants/{tenant_a.id}/environments/{production.id}/resources/leads",
        json={"name": "No", "phone": "+15551230002"},
        headers=viewer,
    )
    assert refused.status_code == 403, refused.text
    readable = await client.get(
        f"/api/tenants/{tenant_a.id}/environments/{production.id}/resources/lead",
        headers=viewer,
    )
    assert readable.status_code == 200, readable.text


async def test_cross_tenant_and_missing_environment_look_the_same(
    client, db, tenant_a, tenant_b, owner_a
):
    foreign = await _production(db, tenant_b)
    headers = await auth_headers(client, owner_a)
    other = await client.get(
        f"/api/tenants/{tenant_b.id}/environments/{foreign.id}/resources",
        headers=headers,
    )
    missing = await client.get(
        f"/api/tenants/{tenant_a.id}/environments/{uuid.uuid4()}/resources",
        headers=headers,
    )
    assert other.status_code == 404
    assert missing.status_code == 404
    assert other.json() == missing.json()


async def test_suspended_environment_rejects_a_write_and_export_is_paged(
    client, db, tenant_a, owner_a
):
    production = await _production(db, tenant_a)
    headers = await auth_headers(client, owner_a)
    production.status = "suspended"
    await db.commit()
    refused = await client.post(
        f"/api/tenants/{tenant_a.id}/environments/{production.id}/resources/leads",
        json={"name": "Late", "phone": "+15551230003"},
        headers=headers,
    )
    assert refused.status_code == 409, refused.text
    exported = await client.get(
        f"/api/tenants/{tenant_a.id}/environments/{production.id}/exports/lead?limit=1",
        headers=headers,
    )
    assert exported.status_code == 200, exported.text
    assert exported.json()["limit"] == 1
