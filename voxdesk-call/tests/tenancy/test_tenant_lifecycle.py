"""Tenant lifecycle. Suspension is a status, not a delete, and login still works."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import AuditAction, AuditLog, Call
from tests.conftest import auth_headers, make_call

pytestmark = pytest.mark.asyncio


async def test_suspend_is_idempotent_and_login_still_works(client, db, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    call = await make_call(db, tenant_a)

    first = await client.post(f"/api/tenants/{tenant_a.id}/suspend", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["lifecycle_status"] == "suspended"
    assert first.json()["is_active"] is True

    second = await client.post(f"/api/tenants/{tenant_a.id}/suspend", headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["lifecycle_status"] == "suspended"

    me = await client.get("/auth/me", headers=await auth_headers(client, owner_a))
    assert me.status_code == 200, me.text

    still = await db.get(Call, call.id)
    assert still is not None
    assert still.tenant_id == tenant_a.id

    entries = (
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_a.id,
                    AuditLog.action == AuditAction.TENANT_LIFECYCLE_CHANGED,
                )
            )
        )
        .scalars()
        .all()
    )
    transitions = [row for row in entries if row.detail.get("operation") == "transition"]
    assert len(transitions) == 1


async def test_read_only_blocks_writes_and_deleted_is_terminal(
    client, db, tenant_a, owner_a, admin_a
):
    owner = await auth_headers(client, owner_a)
    admin = await auth_headers(client, admin_a)

    frozen = await client.post(f"/api/tenants/{tenant_a.id}/read-only", headers=owner)
    assert frozen.status_code == 200, frozen.text

    rename = await client.patch(
        f"/api/tenants/{tenant_a.id}/profile",
        json={"name": "Should Not Stick"},
        headers=admin,
    )
    assert rename.status_code == 409, rename.text

    staging = await client.post(
        f"/api/tenants/{tenant_a.id}/environments",
        json={"name": "Staging", "kind": "staging"},
        headers=owner,
    )
    assert staging.status_code == 409, staging.text

    reads = await client.get(f"/api/tenants/{tenant_a.id}/environments", headers=admin)
    assert reads.status_code == 200, reads.text

    restored = await client.post(f"/api/tenants/{tenant_a.id}/restore", headers=owner)
    assert restored.status_code == 200, restored.text
    assert restored.json()["lifecycle_status"] == "active"

    deleted = await client.post(
        f"/api/tenants/{tenant_a.id}/lifecycle",
        json={"status": "deleted"},
        headers=owner,
    )
    assert deleted.status_code == 200, deleted.text
    refused = await client.post(f"/api/tenants/{tenant_a.id}/restore", headers=owner)
    assert refused.status_code == 409, refused.text
    assert await db.get(type(tenant_a), tenant_a.id) is not None


async def test_unknown_status_and_viewer_are_rejected(client, tenant_a, owner_a, viewer_a):
    owner = await auth_headers(client, owner_a)
    unknown = await client.post(
        f"/api/tenants/{tenant_a.id}/lifecycle",
        json={"status": "exploded"},
        headers=owner,
    )
    assert unknown.status_code == 422, unknown.text

    viewer = await auth_headers(client, viewer_a)
    refused = await client.post(f"/api/tenants/{tenant_a.id}/suspend", headers=viewer)
    assert refused.status_code == 403, refused.text
    assert (await client.get("/api/tenants/not-a-uuid/hierarchy")).status_code in (401, 422)


async def test_cross_tenant_lifecycle_is_not_found(client, tenant_a, tenant_b, owner_a):
    headers = await auth_headers(client, owner_a)
    foreign = await client.post(f"/api/tenants/{tenant_b.id}/suspend", headers=headers)
    assert foreign.status_code == 404, foreign.text
    forged = await client.get(
        f"/api/tenants/{tenant_a.id}/hierarchy",
        params={"claimed_tenant_id": str(tenant_b.id)},
        headers=headers,
    )
    assert forged.status_code == 404, forged.text


async def test_deleted_tenant_row_remains(client, db, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    deleted = await client.post(
        f"/api/tenants/{tenant_a.id}/lifecycle",
        json={"status": "deleted"},
        headers=headers,
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["lifecycle_status"] == "deleted"
    db.expire(tenant_a)
    await db.refresh(tenant_a)
    assert tenant_a.lifecycle_status == "deleted"
