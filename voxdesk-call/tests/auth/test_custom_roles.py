"""Tenant-defined roles (PART 16, P16-01).

The feature is a permission *union*, so the tests are about the two ways a union
can go wrong:

1. **Escalation** — an admin writing a role that holds something they do not, or
   assigning a role they could not have written. Both are refused by
   `custom_roles.assert_grantable`, and both are tested through the HTTP surface
   because that is the surface an attacker has.
2. **Silent widening** — a grant that reaches a subject it should not: another
   tenant, a suspended membership keeping a write permission, a deactivated role
   still granting, an API key gaining something beyond its scopes.

The happy path is tested end-to-end as well: a viewer with a custom role can
actually do the thing, because "the union is computed" is not evidence that any
route checks it.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401
from app.auth import custom_roles as policy
from app.auth.dependencies import TenantContext
from app.auth.identity.models import SCIMGroupMapping, SSOConnection
from app.auth.permissions import Permission
from app.auth.rbac import can_assign_permissions, effective_permissions
from app.db.models import Tenant, UserRole
from app.db.role_models import CustomRole, CustomRoleAssignment
from tests.conftest import auth_headers, failure_detail, make_tenant, make_user

pytestmark = pytest.mark.asyncio

ROLES = "/api/auth/custom-roles"
CATALOGUE = "/api/auth/permission-catalogue"
EFFECTIVE = "/api/auth/effective-permissions"


@pytest.fixture(autouse=True)
def _clean_grant_cache():
    """The grant cache is process-global; a stale entry must not leak across tests."""
    policy.invalidate_cache()
    yield
    policy.invalidate_cache()


async def _create_role(client, headers, **payload) -> dict:
    body = {
        "name": "QA reviewer",
        "description": "Read QA scores and finalise reviews.",
        "permissions": [Permission.QA_READ.value, Permission.QA_WRITE.value],
    }
    body.update(payload)
    response = await client.post(ROLES, json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------- vocabulary ---


async def test_every_permission_has_a_module_so_the_editor_cannot_hide_one():
    assert policy.ungrouped_permissions() == []
    rows = policy.catalogue()
    seen = {pid for row in rows for pid in row["permissions"]}  # type: ignore[index]
    assert seen == {permission.value for permission in Permission}
    assert len(rows) > 20


async def test_an_unknown_permission_id_is_refused_rather_than_dropped():
    with pytest.raises(policy.CustomRoleError):
        policy.parse_permissions([Permission.QA_READ.value, "qaa:read"])
    with pytest.raises(policy.CustomRoleError):
        policy.parse_permissions(["call:destruct"])


async def test_the_union_is_additive_and_deny_by_default():
    viewer = effective_permissions(UserRole.VIEWER)
    assert Permission.QA_WRITE not in viewer
    widened = effective_permissions(UserRole.VIEWER, [Permission.QA_WRITE])
    assert Permission.QA_WRITE in widened
    # Nothing that was there before can be removed by asking for a smaller set.
    assert viewer <= widened
    assert effective_permissions(UserRole.VIEWER, []) == viewer


async def test_platform_permissions_can_never_be_bundled():
    assert not can_assign_permissions(UserRole.OWNER, [Permission.TENANT_CREATE])
    with pytest.raises(policy.CustomRoleError):
        policy.assert_grantable(
            effective_permissions(UserRole.OWNER), [Permission.TENANT_CREATE.value]
        )


async def test_an_actor_cannot_bundle_a_permission_it_does_not_hold():
    admin = effective_permissions(UserRole.ADMIN)
    # An admin holds TEAM_MANAGEMENT but not the owner-only billing write.
    assert Permission.BILLING_WRITE not in admin
    with pytest.raises(policy.CustomRoleError) as exc:
        policy.assert_grantable(admin, [Permission.BILLING_WRITE.value])
    assert "you do not hold" in str(exc.value)


# -------------------------------------------------------------------- CRUD ---


async def test_create_list_patch_delete(client, db, owner_a):
    headers = await auth_headers(client, owner_a)
    role = await _create_role(client, headers)
    assert role["permissions"] == sorted([Permission.QA_READ.value, Permission.QA_WRITE.value])
    assert role["modules"] == {"qa": 2}

    listing = await client.get(ROLES, headers=headers)
    assert listing.status_code == 200
    assert [row["name"] for row in listing.json()["roles"]] == ["QA reviewer"]
    assert listing.json()["subject_types"] == ["user", "service_account", "scim_group"]

    patched = await client.patch(
        f"{ROLES}/{role['id']}",
        json={"description": "Read only now", "permissions": [Permission.QA_READ.value]},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["permissions"] == [Permission.QA_READ.value]

    removed = await client.delete(f"{ROLES}/{role['id']}", headers=headers)
    assert removed.status_code == 204
    assert (await client.get(ROLES, headers=headers)).json()["roles"] == []


async def test_a_duplicate_name_is_a_409_not_a_second_role(client, db, owner_a):
    headers = await auth_headers(client, owner_a)
    await _create_role(client, headers)
    clash = await client.post(
        ROLES, json={"name": "QA reviewer", "permissions": []}, headers=headers
    )
    assert clash.status_code == 409
    assert failure_detail(clash) == "name_taken"


async def test_an_admin_cannot_write_a_role_above_itself(client, db, tenant_a):
    admin = await make_user(db, tenant_a, UserRole.ADMIN)
    headers = await auth_headers(client, admin)

    refused = await client.post(
        ROLES,
        json={"name": "Sneaky", "permissions": [Permission.BILLING_WRITE.value]},
        headers=headers,
    )
    assert refused.status_code == 403, refused.text
    assert failure_detail(refused) == "escalation_refused"

    allowed = await client.post(
        ROLES,
        json={"name": "Analyst", "permissions": [Permission.ANALYTICS_READ.value]},
        headers=headers,
    )
    assert allowed.status_code == 201, allowed.text


async def test_a_manager_without_role_change_permission_cannot_create_roles(client, db, tenant_a):
    manager = await make_user(db, tenant_a, UserRole.MANAGER)
    headers = await auth_headers(client, manager)
    response = await client.post(ROLES, json={"name": "x", "permissions": []}, headers=headers)
    assert response.status_code == 403
    assert "user:role_change" in response.text


# -------------------------------------------------------------- isolation ---


async def test_another_tenants_role_is_a_404_not_a_403(client, db, owner_a):
    headers = await auth_headers(client, owner_a )
    role = await _create_role(client, headers)

    other_tenant = await make_tenant(db, "Other Tenant")
    other_owner = await make_user(db, other_tenant, UserRole.OWNER)
    other_headers = await auth_headers(client, other_owner)

    assert (await client.patch(f"{ROLES}/{role['id']}", json={"name": "hijack"}, headers=other_headers)).status_code == 404
    assert (await client.delete(f"{ROLES}/{role['id']}", headers=other_headers)).status_code == 404
    assert (await client.get(f"{ROLES}/{role['id']}/assignments", headers=other_headers)).status_code == 404
    # And the role is untouched.
    rows = (await client.get(ROLES, headers=headers)).json()["roles"]
    assert rows[0]["name"] == "QA reviewer"


async def test_an_assignment_cannot_name_a_subject_from_another_tenant(client, db, owner_a):
    headers = await auth_headers(client, owner_a)
    role = await _create_role(client, headers)

    other_tenant = await make_tenant(db, "Stranger Tenant")
    stranger = await make_user(db, other_tenant, UserRole.VIEWER)

    response = await client.post(
        f"{ROLES}/{role['id']}/assignments",
        json={"subject_type": "user", "subject_id": str(stranger.id)},
        headers=headers,
    )
    assert response.status_code == 404
    assert failure_detail(response) == "subject_not_found"


async def test_a_subject_id_that_is_not_a_uuid_is_a_422(client, db, owner_a):
    headers = await auth_headers(client, owner_a)
    role = await _create_role(client, headers)
    response = await client.post(
        f"{ROLES}/{role['id']}/assignments",
        json={"subject_type": "user", "subject_id": "not-a-uuid"},
        headers=headers,
    )
    assert response.status_code == 422


# ------------------------------------------------------- grants that work ---


async def test_a_viewer_gains_a_write_permission_only_through_its_role(client, db, tenant_a):
    """End to end: the route check really consults the union."""
    owner = await make_user(db, tenant_a, UserRole.OWNER)
    owner_headers = await auth_headers(client, owner)
    viewer = await make_user(db, tenant_a, UserRole.VIEWER)
    viewer_headers = await auth_headers(client, viewer)

    rule = {
        "name": "Error rate",
        "metric": "error_rate",
        "comparator": "gt",
        "threshold": 5,
    }
    # Denied by the built-in role.
    denied = await client.post("/api/alerts/rules", json=rule, headers=viewer_headers)
    assert denied.status_code == 403

    role = await _create_role(
        client,
        owner_headers,
        name="Alert editor",
        permissions=[Permission.ANALYTICS_READ.value, Permission.TENANT_UPDATE.value],
    )
    granted = await client.post(
        f"{ROLES}/{role['id']}/assignments",
        json={"subject_type": "user", "subject_id": str(viewer.id)},
        headers=owner_headers,
    )
    assert granted.status_code == 201, granted.text

    allowed = await client.post("/api/alerts/rules", json=rule, headers=viewer_headers)
    assert allowed.status_code == 201, allowed.text

    # Removing the grant takes the permission away again — no residual cache.
    removed = await client.delete(
        f"{ROLES}/{role['id']}/assignments/{granted.json()['id']}", headers=owner_headers
    )
    assert removed.status_code == 204
    again = await client.post(
        "/api/alerts/rules", json={**rule, "name": "Second"}, headers=viewer_headers
    )
    assert again.status_code == 403


async def test_a_deactivated_role_grants_nothing(client, db, tenant_a):
    owner = await make_user(db, tenant_a, UserRole.OWNER)
    headers = await auth_headers(client, owner)
    viewer = await make_user(db, tenant_a, UserRole.VIEWER)

    role = await _create_role(client, headers, name="Sleeping", permissions=[Permission.QA_WRITE.value])
    await client.post(
        f"{ROLES}/{role['id']}/assignments",
        json={"subject_type": "user", "subject_id": str(viewer.id)},
        headers=headers,
    )
    assert (await client.get(f"{EFFECTIVE}?user_id={viewer.id}", headers=headers)).json()[
        "custom_permissions"
    ] == [Permission.QA_WRITE.value]

    await client.patch(f"{ROLES}/{role['id']}", json={"is_active": False}, headers=headers)
    assert (await client.get(f"{EFFECTIVE}?user_id={viewer.id}", headers=headers)).json()[
        "custom_permissions"
    ] == []


async def test_a_scim_group_grant_reaches_its_members(client, db, tenant_a):
    owner = await make_user(db, tenant_a, UserRole.OWNER)
    headers = await auth_headers(client, owner)
    member = await make_user(db, tenant_a, UserRole.VIEWER)
    outsider = await make_user(db, tenant_a, UserRole.VIEWER)

    connection = SSOConnection(
        tenant_id=tenant_a.id, name="Okta", slug=f"okta-{uuid.uuid4().hex[:6]}", protocol="oidc"
    )
    db.add(connection)
    await db.commit()
    await db.refresh(connection)

    group = SCIMGroupMapping(
        tenant_id=tenant_a.id,
        connection_id=connection.id,
        external_id="grp-qa",
        display_name="QA team",
        member_user_ids=[str(member.id)],
    )
    db.add(group)
    await db.commit()
    await db.refresh(group)

    role = await _create_role(client, headers, name="Group QA", permissions=[Permission.QA_WRITE.value])
    assigned = await client.post(
        f"{ROLES}/{role['id']}/assignments",
        json={"subject_type": "scim_group", "subject_id": str(group.id)},
        headers=headers,
    )
    assert assigned.status_code == 201, assigned.text

    inside = (await client.get(f"{EFFECTIVE}?user_id={member.id}", headers=headers)).json()
    outside = (await client.get(f"{EFFECTIVE}?user_id={outsider.id}", headers=headers)).json()
    assert Permission.QA_WRITE.value in inside["custom_permissions"]
    assert inside["custom_roles"][0]["name"] == "Group QA"
    assert outside["custom_permissions"] == []


async def test_a_service_account_grant_is_loaded_for_its_own_subject(client, db, tenant_a):
    owner = await make_user(db, tenant_a, UserRole.OWNER)
    headers = await auth_headers(client, owner)
    account = (
        await client.post(
            "/api/service-accounts",
            json={"name": "nightly", "scopes": [Permission.ANALYTICS_READ.value]},
            headers=headers,
        )
    ).json()

    role = await _create_role(client, headers, name="Nightly", permissions=[Permission.QA_READ.value])
    assigned = await client.post(
        f"{ROLES}/{role['id']}/assignments",
        json={"subject_type": "service_account", "subject_id": account["id"]},
        headers=headers,
    )
    assert assigned.status_code == 201, assigned.text

    _held, permissions = await policy.grants_for_user(
        db, tenant_a.id, user_id=None, service_account_id=uuid.UUID(account["id"])
    )
    assert Permission.QA_READ in permissions


# -------------------------------------------------- scope cannot be widened ---


async def test_a_machine_scope_still_narrows_a_custom_role(db, tenant_a):
    """A custom role adds to the role, never to the key's scopes."""
    owner = await make_user(db, tenant_a, UserRole.OWNER)
    context = TenantContext(
        user=owner,
        tenant=tenant_a,
        auth_method="service_account",
        scopes=frozenset({Permission.ANALYTICS_READ.value}),
        custom_permissions=frozenset({Permission.QA_WRITE, Permission.ANALYTICS_READ}),
    )
    assert context.can(Permission.ANALYTICS_READ) is True
    assert context.can(Permission.QA_WRITE) is False


async def test_a_suspended_membership_keeps_only_reads_from_its_roles(db, tenant_a):
    owner = await make_user(db, tenant_a, UserRole.OWNER)
    context = TenantContext(
        user=owner,
        tenant=tenant_a,
        membership_status="suspended",
        custom_permissions=frozenset({Permission.QA_WRITE, Permission.ANALYTICS_READ}),
    )
    assert context.can(Permission.ANALYTICS_READ) is True
    assert context.can(Permission.QA_WRITE) is False


async def test_the_cache_is_invalidated_by_a_write(client, db, owner_a):
    headers = await auth_headers(client, owner_a)
    tenant = await db.get(Tenant, owner_a.tenant_id)
    assert tenant is not None
    role = await _create_role(client, headers, name="Cached", permissions=[Permission.QA_WRITE.value])
    viewer = await make_user(db, tenant, UserRole.VIEWER)

    assigned = await client.post(
        f"{ROLES}/{role['id']}/assignments",
        json={"subject_type": "user", "subject_id": str(viewer.id)},
        headers=headers,
    )
    assert assigned.status_code == 201
    grants = await policy.load_grant_map(db, owner_a.tenant_id)
    assert ("user", str(viewer.id)) in grants

    removed = await client.delete(
        f"{ROLES}/{role['id']}/assignments/{assigned.json()['id']}", headers=headers
    )
    assert removed.status_code == 204
    after = await policy.load_grant_map(db, owner_a.tenant_id)
    assert ("user", str(viewer.id)) not in after


async def test_rows_are_written_minimal_and_tenant_scoped(db, owner_a):
    """The row itself: sorted permission ids, the tenant on both tables."""
    tenant = await db.get(Tenant, owner_a.tenant_id)
    role = CustomRole(
        tenant_id=tenant.id,
        name="Row check",
        permissions=[Permission.QA_WRITE.value],
        created_by_user_id=owner_a.id,
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)
    assert role.permission_ids() == [Permission.QA_WRITE.value]

    db.add(
        CustomRoleAssignment(
            tenant_id=tenant.id,
            role_id=role.id,
            subject_type="user",
            subject_id=str(owner_a.id),
        )
    )
    await db.commit()
    stored = await db.scalar(
        select(CustomRoleAssignment).where(CustomRoleAssignment.role_id == role.id)
    )
    assert stored is not None and stored.tenant_id == tenant.id
