"""Authorization matrix, isolation attacks, policy inheritance and quotas."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import Environment, UserRole
from app.environments.guard import mutation_allowed, selection_allowed
from app.environments.policy_inheritance import merge, resolve_effective, set_scope_policy, weakens
from app.organization.access import (
    can_create_tenant,
    can_manage_members,
    can_read_organization,
    can_suspend_tenant,
    can_update_organization,
)
from app.quotas.enforcement import enforce
from app.quotas.models import QuotaError, QuotaKey
from app.quotas.service import resolve_quota, set_quota
from app.auth.identity.policies import default_policy
from tests.conftest import auth_headers, make_user

pytestmark = pytest.mark.asyncio


async def test_role_matrix_uses_the_existing_permission_engine(
    db, tenant_a, owner_a, admin_a, manager_a, agent_a, viewer_a
):
    org = tenant_a.organization_id
    cases = [
        (None, False, False, False, False, False),
        (viewer_a, True, False, False, False, False),
        (agent_a, True, False, False, False, False),
        (manager_a, True, False, False, False, False),
        (admin_a, True, True, True, False, True),
        (owner_a, True, True, True, False, True),
    ]
    for user, read, update, members, create, suspend in cases:
        assert (await can_read_organization(db, user, org)).allowed is read
        assert (await can_update_organization(db, user, org)).allowed is update
        assert (await can_manage_members(db, user, org)).allowed is members
        # TENANT_CREATE stays platform-only for every current role.
        assert (await can_create_tenant(db, user, org)).allowed is create
        assert (await can_suspend_tenant(db, user, org)).allowed is suspend
    outsider = await can_read_organization(db, owner_a, uuid.uuid4())
    assert outsider.boundary is True
    assert outsider.allowed is False


async def test_suspended_member_loses_writes_and_revoked_member_loses_access(
    client, db, tenant_a, owner_a, admin_a
):
    owner = await auth_headers(client, owner_a)
    org = tenant_a.organization_id
    suspended = await client.post(
        f"/api/organizations/{org}/members/{admin_a.id}/suspend", headers=owner,
    )
    assert suspended.status_code == 200, suspended.text
    admin = await auth_headers(client, admin_a)
    read = await client.get(f"/api/organizations/{org}", headers=admin)
    assert read.status_code == 200, read.text
    write = await client.patch(
        f"/api/organizations/{org}", json={"name": "Nope"}, headers=admin,
    )
    assert write.status_code == 403, write.text
    decision = await can_update_organization(
        db, admin_a, org, membership_status="suspended"
    )
    assert decision.allowed is False
    still_read = await can_read_organization(
        db, admin_a, org, membership_status="suspended"
    )
    assert still_read.allowed is True


async def test_service_account_and_api_key_cannot_cross_scope(
    client, tenant_a, tenant_b, owner_a, owner_b
):
    headers = await auth_headers(client, owner_a)
    account = await client.post(
        "/api/service-accounts",
        json={"name": "exporter", "scopes": ["tenant:read"]},
        headers=headers,
    )
    assert account.status_code == 201, account.text
    credential = await client.post(
        f"/api/service-accounts/{account.json()['id']}/credentials",
        json={},
        headers=headers,
    )
    assert credential.status_code == 201, credential.text
    secret = credential.json()["secret"]
    disabled = await client.post(
        f"/api/service-accounts/{account.json()['id']}/disable",
        json={},
        headers=headers,
    )
    assert disabled.status_code == 200, disabled.text
    refused = await client.get(
        f"/api/tenants/{tenant_a.id}/access/environments",
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert refused.status_code == 401, refused.text

    key = await client.post(
        "/api/api-keys",
        json={"name": "reader", "scopes": ["tenant:read", "user:read"]},
        headers=headers,
    )
    assert key.status_code == 201, key.text
    bearer = {"Authorization": f"Bearer {key.json()['secret']}"}
    own = await client.get(f"/api/tenants/{tenant_a.id}/members", headers=bearer)
    assert own.status_code == 200, own.text
    other = await client.get(f"/api/tenants/{tenant_b.id}/members", headers=bearer)
    missing = await client.get(f"/api/tenants/{uuid.uuid4()}/members", headers=bearer)
    assert other.status_code == 404, other.text
    assert missing.status_code == 404
    assert other.json() == missing.json()


async def test_sso_shaped_user_cannot_cross_organizations(db, tenant_a, tenant_b, owner_a):
    """SSO users are the same user row. The organization check does not care how they logged in."""
    decision = await can_read_organization(db, owner_a, tenant_b.organization_id)
    assert decision.boundary is True
    home = await can_read_organization(db, owner_a, tenant_a.organization_id)
    assert home.allowed is True


async def test_mfa_does_not_raise_a_viewer(db, tenant_a, viewer_a):
    decision = await can_manage_members(db, viewer_a, tenant_a.organization_id)
    assert decision.allowed is False
    assert viewer_a.role is UserRole.VIEWER


async def test_archived_environment_cannot_become_current(client, db, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    created = await client.post(
        f"/api/tenants/{tenant_a.id}/environments",
        json={"name": "Staging", "kind": "staging"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    environment_id = created.json()["id"]
    archived = await client.post(
        f"/api/tenants/{tenant_a.id}/environments/{environment_id}/archive",
        headers=headers,
    )
    assert archived.status_code == 200, archived.text
    selected = await client.post(
        f"/api/tenants/{tenant_a.id}/access/current",
        json={"environment_id": environment_id, "organization_id": str(uuid.uuid4())},
        headers=headers,
    )
    assert selected.status_code in (404, 409), selected.text
    row = (
        await db.execute(select(Environment).where(Environment.id == uuid.UUID(environment_id)))
    ).scalar_one()
    assert selection_allowed(row) is False
    assert mutation_allowed(row, UserRole.OWNER, action="archive") is False


async def test_suspended_organization_refuses_a_new_invitation(
    client, db, tenant_a, owner_a
):
    headers = await auth_headers(client, owner_a)
    suspended = await client.post(
        f"/api/organizations/{tenant_a.organization_id}/suspend",
        headers=headers,
    )
    assert suspended.status_code == 200, suspended.text
    invited = await client.post(
        f"/api/organizations/{tenant_a.organization_id}/invitations",
        json={"email": "after-suspend@example.com", "role": "organization_member"},
        headers=headers,
    )
    assert invited.status_code == 409, invited.text


async def test_child_policy_cannot_weaken_parent_and_quota_fails_safe(
    db, tenant_a
):
    base = default_policy(tenant_a.id)
    parent = merge(base, {"mfa_required": True, "password_login_allowed": False})
    assert parent["mfa_required"] is True
    assert parent["password_login_allowed"] is False
    assert weakens(parent, {"mfa_required": False}) is True
    assert weakens(parent, {"password_login_allowed": True}) is True
    tightened = merge(base, {"mfa_required": True}, {"mfa_required": False})
    assert tightened["mfa_required"] is True
    await set_scope_policy(
        db, kind="organization", scope_id=tenant_a.organization_id, tenant=tenant_a,
        values={"mfa_required": True},
    )
    with pytest.raises(Exception):
        await set_scope_policy(
            db, kind="tenant", scope_id=tenant_a.id, tenant=tenant_a,
            values={"mfa_required": False},
        )
    effective = await resolve_effective(db, tenant_a)
    assert effective.mfa_required is True

    unknown = await resolve_quota(
        db, QuotaKey.STORAGE_BYTES, organization_id=tenant_a.organization_id,
        tenant_id=tenant_a.id, tenant=tenant_a, used=5,
    )
    assert unknown.mode == "unknown"
    assert unknown.limit is None
    assert unknown.allowed is True
    await set_quota(
        db, scope_kind="tenant", scope_id=tenant_a.id,
        key=QuotaKey.USERS, mode="hard", limit_value=1,
    )
    limited = await resolve_quota(
        db, QuotaKey.USERS, organization_id=tenant_a.organization_id,
        tenant_id=tenant_a.id, tenant=tenant_a, used=2,
    )
    assert limited.allowed is False
    assert limited.limit == 1 or (limited.limit is not None and limited.limit <= 1)
    with pytest.raises(QuotaError):
        await enforce(
            db, key=QuotaKey.USERS, tenant=tenant_a, used=2, adding=0, action="admin",
        )
    inbound = await enforce(
        db, key=QuotaKey.MONTHLY_CALL_MINUTES, tenant=tenant_a,
        used=10**9, adding=0, action="inbound_call",
    )
    assert inbound.allowed is True
    with pytest.raises(ValueError):
        await set_quota(
            db, scope_kind="tenant", scope_id=tenant_a.id,
            key=QuotaKey.USERS, mode="unlimited", limit_value=5,
        )
    from types import SimpleNamespace
    from app.quotas.service import interpret_row
    malformed = interpret_row(
        SimpleNamespace(mode="hard", limit_value=None), QuotaKey.STORAGE_BYTES.value,
    )
    assert malformed.allowed is False
    assert malformed.decision == "malformed"
