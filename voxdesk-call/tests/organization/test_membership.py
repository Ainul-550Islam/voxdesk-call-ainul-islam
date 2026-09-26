"""Organization membership lifecycle and isolation."""

from __future__ import annotations

import importlib.util
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import select

from app.db.models import (
    AuditAction,
    AuditLog,
    OrganizationMembership,
    TenantMembership,
    UserRole,
)
from app.organization.access import can_manage_members, can_read_organization
from app.organization.roles import resolve_organization_role
from tests.conftest import auth_headers, make_user

pytestmark = pytest.mark.asyncio


def test_conceptual_roles_are_the_existing_enum():
    assert resolve_organization_role("organization_owner") is UserRole.OWNER
    assert resolve_organization_role("organization_admin") is UserRole.ADMIN
    assert resolve_organization_role("organization_member") is UserRole.AGENT
    assert resolve_organization_role("organization_viewer") is UserRole.VIEWER
    assert resolve_organization_role("manager") is UserRole.MANAGER
    with pytest.raises(Exception):
        resolve_organization_role("superadmin")


async def test_insert_hook_creates_one_membership_per_user(db, tenant_a, owner_a, admin_a):
    org_rows = (
        await db.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == tenant_a.organization_id
            )
        )
    ).scalars().all()
    assert {row.user_id for row in org_rows} >= {owner_a.id, admin_a.id}
    assert all(row.status == "active" for row in org_rows)
    tenant_rows = (
        await db.execute(
            select(TenantMembership).where(TenantMembership.tenant_id == tenant_a.id)
        )
    ).scalars().all()
    assert {row.user_id for row in tenant_rows} >= {owner_a.id, admin_a.id}


async def test_owner_can_update_and_suspend_and_last_owner_is_protected(
    client, db, tenant_a, owner_a, admin_a
):
    headers = await auth_headers(client, owner_a)
    org = tenant_a.organization_id
    updated = await client.patch(
        f"/api/organizations/{org}/members/{admin_a.id}",
        json={"role": "organization_viewer"},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["role"] == "viewer"

    suspended = await client.post(
        f"/api/organizations/{org}/members/{admin_a.id}/suspend",
        headers=headers,
    )
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["status"] == "suspended"
    restored = await client.post(
        f"/api/organizations/{org}/members/{admin_a.id}/reactivate",
        headers=headers,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["status"] == "active"

    self_grant = await client.patch(
        f"/api/organizations/{org}/members/{owner_a.id}",
        json={"role": "organization_admin"},
        headers=headers,
    )
    assert self_grant.status_code == 403, self_grant.text

    demote_last = await client.patch(
        f"/api/organizations/{org}/members/{owner_a.id}",
        json={"role": "viewer"},
        headers=await auth_headers(client, admin_a),
    )
    assert demote_last.status_code in (403, 409), demote_last.text

    entries = (
        await db.execute(
            select(AuditLog).where(AuditLog.action == AuditAction.MEMBERSHIP_UPDATED)
        )
    ).scalars().all()
    assert entries
    assert "token" not in entries[-1].detail


async def test_viewer_cannot_invite_and_member_cannot_self_promote(
    client, db, tenant_a, owner_a, viewer_a, agent_a
):
    org = tenant_a.organization_id
    viewer = await auth_headers(client, viewer_a)
    refused = await client.post(
        f"/api/organizations/{org}/invitations",
        json={"email": "new-person@example.com", "role": "organization_admin"},
        headers=viewer,
    )
    assert refused.status_code == 403, refused.text
    decision = await can_manage_members(db, viewer_a, org)
    assert decision.allowed is False
    read = await can_read_organization(db, viewer_a, org)
    assert read.allowed is True

    agent = await auth_headers(client, agent_a)
    escalate = await client.patch(
        f"/api/organizations/{org}/members/{agent_a.id}",
        json={"role": "organization_owner"},
        headers=agent,
    )
    assert escalate.status_code == 403, escalate.text


async def test_cross_organization_membership_is_not_found(
    client, tenant_a, tenant_b, owner_a, owner_b
):
    headers = await auth_headers(client, owner_a)
    foreign = await client.get(
        f"/api/organizations/{tenant_b.organization_id}/members",
        headers=headers,
    )
    missing = await client.get(
        f"/api/organizations/{uuid.uuid4()}/members",
        headers=headers,
    )
    assert foreign.status_code == 404, foreign.text
    assert missing.status_code == 404
    assert foreign.json() == missing.json()
    other = await auth_headers(client, owner_b)
    sneak = await client.patch(
        f"/api/organizations/{tenant_a.organization_id}/members/{owner_a.id}",
        json={"role": "viewer", "organization_id": str(tenant_b.organization_id)},
        headers=other,
    )
    assert sneak.status_code == 404, sneak.text


async def test_revoked_membership_is_rejected_by_existing_tenant_api(
    client, db, tenant_a, owner_a, admin_a
):
    owner = await auth_headers(client, owner_a)
    revoked = await client.post(
        f"/api/organizations/{tenant_a.organization_id}/members/{admin_a.id}/revoke",
        headers=owner,
    )
    assert revoked.status_code == 200, revoked.text
    admin = await auth_headers(client, admin_a)
    me = await client.get("/auth/me", headers=admin)
    assert me.status_code == 403, me.text
    leads = await client.get(f"/api/tenants/{tenant_a.id}/leads", headers=admin)
    assert leads.status_code == 403, leads.text


def test_membership_backfill_is_empty_safe_and_idempotent(tmp_path):
    path = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "alembic" / "versions" / "0018_organization_memberships_quotas.py"
    )
    spec = importlib.util.spec_from_file_location("m0018", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'members.db'}")
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    org_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE organization_memberships ("
            "id VARCHAR(36) PRIMARY KEY, organization_id VARCHAR(36), user_id VARCHAR(36), "
            "role VARCHAR(16), status VARCHAR(16), created_at DATETIME, updated_at DATETIME, "
            "invited_at DATETIME, accepted_at DATETIME, suspended_at DATETIME, revoked_at DATETIME)"
        ))
        conn.execute(sa.text(
            "CREATE TABLE tenant_memberships ("
            "id VARCHAR(36) PRIMARY KEY, tenant_id VARCHAR(36), user_id VARCHAR(36), "
            "role VARCHAR(16), status VARCHAR(16), created_at DATETIME, updated_at DATETIME, "
            "invited_at DATETIME, accepted_at DATETIME, suspended_at DATETIME, revoked_at DATETIME)"
        ))
        conn.execute(sa.text(
            "CREATE TABLE users (id VARCHAR(36) PRIMARY KEY, role VARCHAR(16), tenant_id VARCHAR(36))"
        ))
        conn.execute(sa.text(
            "CREATE TABLE tenants (id VARCHAR(36) PRIMARY KEY, organization_id VARCHAR(36))"
        ))
        assert module.backfill_memberships(conn) == 0
        conn.execute(
            sa.text("INSERT INTO tenants (id, organization_id) VALUES (:id, :org)"),
            {"id": str(tenant_id), "org": str(org_id)},
        )
        conn.execute(
            sa.text("INSERT INTO users (id, role, tenant_id) VALUES (:id, 'OWNER', :tenant)"),
            {"id": str(user_id), "tenant": str(tenant_id)},
        )
        assert module.backfill_memberships(conn) == 2
        assert module.backfill_memberships(conn) == 0
        stored = conn.execute(sa.text(
            "SELECT user_id, role FROM organization_memberships"
        )).one()
        assert stored[0] == str(user_id)
        assert stored[1] == "OWNER"
        assert conn.execute(sa.text("SELECT id FROM users")).scalar() == str(user_id)
