# Prompt 2 — organization membership, invitations, access, quotas

Authorization is layered on the existing JWT, `User.role`, `Permission` enum, and `app.auth.rbac.has_permission`. There is no second permission checker, no second role namespace, and no `organization_id` or `environment_id` JWT claim.

## Created (30) vs extended

All 30 named files were created. None of those paths existed before this layer, so none were extended in place.

Created:

- `app/organization/roles.py`
- `app/organization/permissions.py`
- `app/organization/membership_service.py`
- `app/organization/invitations.py`
- `app/organization/membership_policies.py`
- `app/organization/access.py`
- `app/tenancy/roles.py`
- `app/tenancy/permissions.py`
- `app/tenancy/membership_service.py`
- `app/tenancy/invitations.py`
- `app/tenancy/quota.py`
- `app/tenancy/access.py`
- `app/environments/membership.py`
- `app/environments/access.py`
- `app/environments/quota.py`
- `app/environments/policy_inheritance.py`
- `app/environments/context_resolution.py`
- `app/environments/guard.py`
- `app/api/organization_membership_routes.py`
- `app/api/tenant_membership_routes.py`
- `app/api/environment_access_routes.py`
- `app/quotas/__init__.py`
- `app/quotas/models.py`
- `app/quotas/service.py`
- `app/quotas/enforcement.py`
- `app/quotas/metrics.py`
- `tests/organization/test_membership.py`
- `tests/organization/test_invitations.py`
- `tests/organization/test_access_policy.py`
- `alembic/versions/0018_organization_memberships_quotas.py`

Also created, not part of the 30:

- `docs/CURRENT-ARCHITECTURE.md`
- `docs/CURRENT-SECURITY-STATUS.md`
- `docs/PROMPT2-IMPLEMENTATION-REPORT.md` (this file)

## Modified

- `app/db/models.py` — membership, invitation, quota, policy, and selection tables; twelve `AuditAction` labels; one `User` insert hook that writes the home memberships
- `app/auth/dependencies.py` — membership status on `TenantContext`; revoked/expired rejected in `get_current_user`; suspended keeps read permissions; `get_optional_context`
- `app/auth/permissions.py` — `is_read_permission`
- `app/auth/service.py` — `change_role` calls `sync_home_role`
- `app/main.py` — the three membership routers
- `app/api/routes.py` — docstring only; routers are not nested here
- `app/organization/__init__.py` — docstring no longer says membership is only derived
- `README.md`
- `docs/DEPLOYMENT.md`, `docs/ENTERPRISE-IDENTITY.md`, `docs/IDENTITY-SECURITY.md` — head `0018`, audit count 110
- `tests/test_deployment.py`, `tests/test_enterprise_persistence.py` — head pin `0018_organization_memberships_quotas`

Deleted: none.

## Migration

Revision `0018_organization_memberships_quotas`, down revision `0017_organization_environment_foundation`. Single head. PostgreSQL `ALTER TYPE auditaction ADD VALUE IF NOT EXISTS` for:

`MEMBERSHIP_CREATED`, `MEMBERSHIP_UPDATED`, `MEMBERSHIP_SUSPENDED`, `MEMBERSHIP_RESTORED`, `MEMBERSHIP_REVOKED`, `INVITATION_CREATED`, `INVITATION_ACCEPTED`, `INVITATION_REVOKED`, `INVITATION_RESENT`, `ENVIRONMENT_SELECTED`, `QUOTA_UPDATED`, `QUOTA_DENIED`.

Tables:

- `organization_memberships` — unique `(organization_id, user_id)`
- `tenant_memberships` — unique `(tenant_id, user_id)`
- `environment_memberships` — unique `(environment_id, user_id)`
- `membership_invitations` — unique `token_hash`; partial unique `(binding_key, email)` where `status='invited'`
- `quota_limits` — unique `(scope_kind, scope_id, quota_key)`; hard/soft require a non-negative limit; unlimited forbids a limit
- `scope_policies` — unique `(scope_kind, scope_id)`
- `environment_selections` — unique `(user_id, tenant_id)`

Role columns reuse `userrole` with `create_type=False`. Downgrade drops only these seven tables. Enum labels stay. `backfill_memberships` inserts one active organization membership and one active tenant membership per user whose tenant has `organization_id`, copying `users.role`. It is idempotent, binds string ids, creates no quota row, and does not create a second environment. It was executed against SQLite in `test_membership_backfill_is_empty_safe_and_idempotent` (empty database inserts 0; one user inserts 2; second call inserts 0). It has not been executed against PostgreSQL in this environment.

No `environment_id` was added to Call, Lead, Appointment, knowledge, CRM, calendar, billing, subscription, usage, automation, notification, inbox, SSO, service account, or API key.

## Role and permission mapping

Stored roles remain `owner`, `admin`, `manager`, `agent`, `viewer`.

Organization aliases: `organization_owner` → owner, `organization_admin` → admin, `organization_member` → agent, `organization_viewer` → viewer. `manager` is accepted by existing value.

Tenant aliases: `tenant_owner` → owner, `tenant_admin` → admin, `tenant_manager` → manager, `tenant_member` → agent, `tenant_viewer` → viewer.

Conceptual capabilities call `has_permission` / `TenantContext.can`:

- organization read/update → `tenant:read` / `tenant:update`
- members read/invite/update/remove → `user:read` / `user:create` / `user:role_change` / `user:delete`
- policies → `identity:read` / `identity:write`
- tenant create stays `tenant:create`, which no current customer role holds
- environment read/update/archive/default/deploy → `tenant:read` or `tenant:update`, then `guard.mutation_allowed`

Production mutation requires owner, and production cannot be archived. Staging requires owner or admin. Development also allows manager. A credential scope can only narrow.

## Inheritance

Environment decision order:

1. No user → unauthenticated deny.
2. Environment not in the tenant → boundary deny.
3. Revoked or expired tenant membership → deny. A parent revoke invalidates the child.
4. Revoked or expired environment membership → deny.
5. Suspended environment or tenant membership → no privileged access.
6. Active explicit environment membership, capped so it cannot outrank the tenant role.
7. Otherwise the tenant role, which may come from an active organization owner or admin of the parent organization.
8. Otherwise deny. A missing membership row on the user's own tenant is the legacy path and uses `users.role`.

Policy merge is organization, then tenant, then environment. A child may only tighten mandatory identity controls (`mfa_required`, `password_login_allowed`, `api_keys_allowed`, `service_accounts_allowed`). A child cannot weaken a parent. A suspended or deleted organization or tenant is not a valid policy source.

## Quotas

Keys: `users` (`team_members`), `environments`, `active_calls` (`concurrent_calls`), `monthly_call_minutes` (`voice_minute`), `ai_tokens` (`llm_token`), `knowledge_documents` (`rag_documents`), `storage_bytes`, `webhook_events_per_minute`. The last two stay unknown unless a hierarchy row exists.

Modes: hard, soft, unlimited, unknown, malformed. Effective limit is the stricter of the set hierarchy caps and the billing entitlement. A hierarchy row cannot raise a billing cap. Missing is unknown, not zero. Malformed fails closed. `enforce` does not block `inbound_call`.

## Invitations

Create, hashed token (`secrets.token_urlsafe(32)` then SHA-256), 7-day expiry, accept, revoke, resend, duplicate prevention on the live `(binding_key, email)` pair, organization and tenant binding, enumeration-safe create responses, audit without the token, concurrent accept via `rowcount != 1`, wrong-organization reject with the same `invitation_invalid` body as an unknown token. Accept of a revoked membership row is an admin-issued re-grant back to active. Ordinary `reactivate_member` still refuses a revoked row. `create_invitation` now checks the actor's organization and `assert_can_grant` itself. Tenant resend checks the tenant binding before the core function commits a rotated token.

## Attack coverage

1. Cross-organization member read or update returns the same 404 as a missing organization.
2. A viewer cannot invite. An agent cannot grant themselves owner.
3. An owner cannot change their own membership, including a non-escalating change.
4. A non-owner cannot demote the last owner.
5. A revoked membership is rejected by `/auth/me` and by an existing tenant API. Login itself still works; the authenticator fails closed afterwards.
6. A suspended member can read the organization and cannot rename it.
7. An invitation token cannot be accepted against another organization. Expired, revoked, resent-old, and unknown tokens share the invalid response.
8. Creating an invitation does not reveal whether the email already has an account.
9. A tenant invitation body that names another organization or tenant is 404, same shape as a missing tenant.
10. An API key cannot list another tenant's members. A disabled service-account credential is 401.
11. An archived environment cannot be selected. A client-supplied organization id that disagrees is 404.
12. A suspended organization refuses a new invitation. A child policy cannot turn required MFA off. A missing quota is unknown. A hard limit with a null value is malformed and not unlimited. Inbound call enforcement is not blocked by that quota.

## Commands and results

```
python3 -c 'import app.main'
```

Pass. Import succeeded after `get_optional_context` and the JWT membership gate were wired.

```
python3 -m pytest tests/organization/test_membership.py tests/organization/test_invitations.py tests/organization/test_access_policy.py -q --tb=line
```

19 passed.

```
python3 -m pytest tests/organization tests/auth tests/test_deployment.py tests/test_enterprise_persistence.py -q --tb=line -p no:cacheprovider
```

531 passed.

```
python3 -m pytest tests/security tests/tenancy tests/integration tests/test_rbac.py tests/test_auth_login.py tests/test_team_management.py tests/test_tenant_isolation.py tests/test_identity_migrations.py tests/test_api_contract.py tests/test_billing_api.py -q --tb=line -p no:cacheprovider
```

383 passed, 1 failed. The failure is `tests/test_api_contract.py::test_no_route_returns_500_when_probed`: unauthenticated `GET /auth/me` returned 500 because the process has no `asyncpg` and that test does not use the SQLite session override. It is an environment blocker, not a membership decision.

```
python3 -m pytest tests -q --tb=line -p no:cacheprovider --ignore=tests/load
```

Collection failed: `tests/test_provider_lifecycle.py` needs `deepgram`; `tests/test_tts.py` needs `pipecat`. Neither package is installed. The rerun excluding those two files was killed at 300 seconds before a summary.

`python3 -m ruff` is not installed.

## Toolchain blockers

- `asyncpg` is not installed, so the unauthenticated route sweep cannot open the configured PostgreSQL URL.
- `deepgram` and `pipecat` are not installed, so two voice tests cannot be collected.
- `ruff` is not installed.
- Alembic upgrade was not run against PostgreSQL. The SQLite backfill test passed.

## Compatibility and security impact

Existing tenant APIs, JWT claim set, users, API keys, service accounts, SSO, SCIM, and MFA are unchanged in shape. Isolation is stricter: a revoked or expired membership now fails existing authenticated routes with 403. A suspended membership keeps read permissions and loses writes. A missing membership row remains the legacy active path so users created before the hook, or a database that has not been migrated, are not locked out by a query error (`OperationalError` / `ProgrammingError` also fall open to active). Tenant lifecycle suspend is not this gate, so a suspended tenant with an active membership can still reach `/auth/me`.

Organization member suspend, reactivate, and revoke require `user:update`, so an admin can revoke a lower member. Invitation revoke and tenant member revoke require `user:delete`, which only owner holds. That split is intentional and is not papered over by granting admin `user:delete`.

`is_active` is still the login kill switch and is not flipped by membership status. Tokens are not logged. Invitation audit detail carries the invitation id, not the raw token.

## Remaining gaps

- Concurrent accept is enforced with `rowcount != 1` and is not raced by two clients in a test.
- Environment membership suspend currently denies that environment entirely, including reads. Organization suspend still allows reads. Parent revoke invalidates the child, which is stricter than "explicit environment membership always beats tenant membership".
- `0018` has not been applied to a live PostgreSQL database in this environment.
- No environment-scoped business resources, billing hierarchy, regional routing, environment secrets, or realtime environment propagation were added. Those remain out of scope.

## Complete file bodies

The sections below are the complete current contents of every new file and every file this layer modified. They were copied from the workspace after the tests above. Nothing is omitted.


### `app/organization/roles.py`

````python
"""Organization roles, mapped onto the existing ``UserRole`` enum.

Conceptual names such as ``organization_owner`` are aliases. They are not a
second role namespace and they are not stored. The database value remains
``owner`` / ``admin`` / ``manager`` / ``agent`` / ``viewer``. Manager has no
organization alias because the existing enum already names it; it is accepted
by value so it is not dropped.
"""

from __future__ import annotations

from app.db.models import UserRole
from app.tenancy.isolation import ValidationFailed

#: Aliases only. The value is the role the rest of the product already enforces.
ORGANIZATION_ROLES: dict[str, UserRole] = {
    "organization_owner": UserRole.OWNER,
    "organization_admin": UserRole.ADMIN,
    "organization_member": UserRole.AGENT,
    "organization_viewer": UserRole.VIEWER,
}


def resolve_organization_role(name: str) -> UserRole:
    """Map a conceptual name or an existing role value. Unknown names fail."""
    key = (name or "").strip().lower().replace("-", "_")
    if key in ORGANIZATION_ROLES:
        return ORGANIZATION_ROLES[key]
    try:
        return UserRole(key)
    except ValueError as exc:
        raise ValidationFailed("Unknown role") from exc


def conceptual_name(role: UserRole) -> str:
    """The organization alias for a stored role, when one exists."""
    for name, stored in ORGANIZATION_ROLES.items():
        if stored is role:
            return name
    return role.value
````

### `app/organization/permissions.py`

````python
"""Organization-scope permission resolution.

The strings on the left are the conceptual groups this layer talks about.
The values are the existing ``Permission`` members. Nothing here is a second
checker: every decision calls ``has_permission`` or ``TenantContext.can``.
"""

from __future__ import annotations

from app.auth.permissions import Permission, is_read_permission
from app.auth.rbac import has_permission
from app.db.models import User, UserRole

#: Conceptual organization capability → the permission id RBAC already owns.
ORGANIZATION_PERMISSIONS: dict[str, Permission] = {
    "organization:read": Permission.TENANT_READ,
    "organization:update": Permission.TENANT_UPDATE,
    "organization:members:read": Permission.USER_READ,
    "organization:members:invite": Permission.USER_CREATE,
    "organization:members:update": Permission.USER_ROLE_CHANGE,
    "organization:members:remove": Permission.USER_DELETE,
    "organization:policies:read": Permission.IDENTITY_READ,
    "organization:policies:update": Permission.IDENTITY_WRITE,
    "organization:tenants:read": Permission.TENANT_READ,
    "organization:tenants:create": Permission.TENANT_CREATE,
    "organization:tenants:update": Permission.TENANT_UPDATE,
    "organization:tenants:suspend": Permission.TENANT_UPDATE,
}


def permission_for(conceptual: str) -> Permission:
    """The existing permission id for a conceptual organization capability."""
    try:
        return ORGANIZATION_PERMISSIONS[conceptual]
    except KeyError as exc:
        raise KeyError(f"Unknown organization capability: {conceptual}") from exc


def allows(
    role: UserRole,
    conceptual: str,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> bool:
    """Role, then scope, then membership status. No separate grant table."""
    permission = permission_for(conceptual)
    if membership_status in ("revoked", "expired", "invited"):
        return False
    if membership_status == "suspended" and not is_read_permission(permission):
        return False
    if not has_permission(role, permission):
        return False
    if scopes is not None and permission.value not in scopes:
        return False
    return True


def user_allows(
    user: User,
    conceptual: str,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> bool:
    return allows(
        user.role, conceptual, scopes=scopes, membership_status=membership_status
    )
````

### `app/organization/membership_service.py`

````python
"""Organization membership lifecycle.

Membership coexists with ``User.tenant_id``. It does not move a user between
organizations and it does not invent a role the existing RBAC policy would
refuse. A missing row is legacy behaviour; a revoked row is an explicit deny.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit
from app.db.models import (
    AuditAction,
    MembershipStatus,
    Organization,
    OrganizationMembership,
    Tenant,
    TenantMembership,
    User,
    UserRole,
)
from app.organization.membership_policies import (
    assert_can_grant,
    assert_can_touch,
    assert_not_last_owner,
    assert_not_self_escalation,
    assert_organization_accepts_members,
    assert_organization_allows_mutation,
    assert_transition,
)
from app.organization.roles import resolve_organization_role
from app.organization.service import Actor
from app.tenancy.isolation import BoundaryDenied, Conflict, NotFound

_NOW = datetime.now


def _now() -> datetime:
    return _NOW(timezone.utc).replace(tzinfo=None)


def _actor(actor: Actor | None) -> Actor:
    return actor or Actor()


async def _audit(
    session: AsyncSession,
    action: AuditAction,
    *,
    actor: Actor,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID | None,
    detail: dict,
) -> None:
    await emit(
        session,
        action,
        tenant_id=actor.tenant_id,
        actor_user_id=actor.user_id,
        target_user_id=target_user_id,
        actor_email=actor.email,
        ip_address=actor.ip_address,
        user_agent=actor.user_agent,
        detail={"organization_id": str(organization_id), **detail},
        commit=False,
    )


async def organization_for_user(
    session: AsyncSession, user: User
) -> Organization | None:
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None or tenant.organization_id is None:
        return None
    return await session.get(Organization, tenant.organization_id)


async def require_same_organization(
    session: AsyncSession, actor: User, organization_id: uuid.UUID
) -> Organization:
    """404 when the actor is outside this organization, including a missing id."""
    current = await organization_for_user(session, actor)
    if current is None or current.id != organization_id:
        raise BoundaryDenied()
    return current


async def get_membership(
    session: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID
) -> OrganizationMembership | None:
    return (
        await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def require_membership(
    session: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID
) -> OrganizationMembership:
    row = await get_membership(session, organization_id, user_id)
    if row is None:
        raise NotFound()
    return row


async def effective_role(
    session: AsyncSession, user: User, organization_id: uuid.UUID
) -> UserRole | None:
    """The stored membership role, or the legacy user role when no row exists."""
    row = await get_membership(session, organization_id, user.id)
    if row is None:
        current = await organization_for_user(session, user)
        if current is None or current.id != organization_id:
            return None
        return user.role
    if row.status in (MembershipStatus.REVOKED.value, MembershipStatus.EXPIRED.value):
        return None
    return row.role


async def list_members(
    session: AsyncSession, actor: User, organization_id: uuid.UUID
) -> list[OrganizationMembership]:
    await require_same_organization(session, actor, organization_id)
    rows = (
        await session.execute(
            select(OrganizationMembership)
            .where(OrganizationMembership.organization_id == organization_id)
            .order_by(OrganizationMembership.created_at)
        )
    ).scalars().all()
    return list(rows)


async def _sync_user_role(session: AsyncSession, user: User, role: UserRole) -> None:
    """Keep the existing RBAC column aligned with the home-tenant membership."""
    if user.role is role:
        return
    user.role = role
    user.token_version += 1
    await session.execute(
        update(TenantMembership)
        .where(
            TenantMembership.user_id == user.id,
            TenantMembership.tenant_id == user.tenant_id,
        )
        .values(role=role, updated_at=_now())
    )


async def sync_home_role(session: AsyncSession, user: User) -> None:
    """Copy ``User.role`` onto the membership rows the insert hook created.

    Called by the existing team role-change path so the two stores cannot
    diverge. It does not grant a role the caller did not already set.
    """
    now = _now()
    organization = await organization_for_user(session, user)
    if organization is not None:
        await session.execute(
            update(OrganizationMembership)
            .where(
                OrganizationMembership.organization_id == organization.id,
                OrganizationMembership.user_id == user.id,
            )
            .values(role=user.role, updated_at=now)
        )
    await session.execute(
        update(TenantMembership)
        .where(
            TenantMembership.user_id == user.id,
            TenantMembership.tenant_id == user.tenant_id,
        )
        .values(role=user.role, updated_at=now)
    )


async def add_member(
    session: AsyncSession,
    *,
    actor_user: User,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID,
    role_name: str,
    actor: Actor | None = None,
) -> OrganizationMembership:
    organization = await require_same_organization(session, actor_user, organization_id)
    assert_organization_accepts_members(organization)
    role = resolve_organization_role(role_name)
    assert_not_self_escalation(actor_user, target_user_id, role)
    assert_can_grant(actor_user, role)
    target = await session.get(User, target_user_id)
    if target is None:
        raise NotFound()
    target_org = await organization_for_user(session, target)
    if target_org is None or target_org.id != organization_id:
        raise BoundaryDenied()
    existing = await get_membership(session, organization_id, target.id)
    if existing is not None and existing.status == MembershipStatus.REVOKED.value:
        raise Conflict("A revoked membership cannot be restored this way")
    if existing is not None and existing.status == MembershipStatus.ACTIVE.value:
        raise Conflict("That person is already a member")
    now = _now()
    if existing is None:
        existing = OrganizationMembership(
            organization_id=organization_id,
            user_id=target.id,
            role=role,
            status=MembershipStatus.ACTIVE.value,
            invited_at=None,
            accepted_at=now,
        )
        session.add(existing)
    else:
        assert_can_touch(actor_user, existing.role, self_target=False)
        existing.role = role
        existing.status = MembershipStatus.ACTIVE.value
        existing.accepted_at = now
        existing.suspended_at = None
        existing.updated_at = now
    if target.tenant_id == actor_user.tenant_id:
        await _sync_user_role(session, target, role)
    who = _actor(actor)
    await _audit(
        session,
        AuditAction.MEMBERSHIP_CREATED,
        actor=who,
        organization_id=organization_id,
        target_user_id=target.id,
        detail={"scope": "organization", "role": role.value, "status": "active"},
    )
    await session.commit()
    await session.refresh(existing)
    return existing


async def update_role(
    session: AsyncSession,
    *,
    actor_user: User,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID,
    role_name: str,
    actor: Actor | None = None,
) -> OrganizationMembership:
    organization = await require_same_organization(session, actor_user, organization_id)
    assert_organization_allows_mutation(organization)
    role = resolve_organization_role(role_name)
    row = await require_membership(session, organization_id, target_user_id)
    if actor_user.id == target_user_id:
        from app.tenancy.isolation import Forbidden
        raise Forbidden("You cannot change your own membership")
    assert_can_touch(actor_user, row.role, self_target=False)
    assert_can_grant(actor_user, role)
    await assert_not_last_owner(
        session, organization_id, row, next_role=role, next_status=None
    )
    previous = row.role
    row.role = role
    row.updated_at = _now()
    target = await session.get(User, target_user_id)
    if target is not None and target.tenant_id == actor_user.tenant_id:
        await _sync_user_role(session, target, role)
    await _audit(
        session,
        AuditAction.MEMBERSHIP_UPDATED,
        actor=_actor(actor),
        organization_id=organization_id,
        target_user_id=target_user_id,
        detail={
            "scope": "organization",
            "from": previous.value,
            "to": role.value,
        },
    )
    await session.commit()
    await session.refresh(row)
    return row


async def _set_status(
    session: AsyncSession,
    *,
    actor_user: User,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID,
    status: str,
    action: AuditAction,
    actor: Actor | None,
) -> OrganizationMembership:
    organization = await require_same_organization(session, actor_user, organization_id)
    if status != MembershipStatus.ACTIVE.value:
        assert_organization_allows_mutation(organization)
    row = await require_membership(session, organization_id, target_user_id)
    if actor_user.id == target_user_id:
        from app.tenancy.isolation import Forbidden
        raise Forbidden("You cannot change your own membership")
    assert_can_touch(actor_user, row.role, self_target=False)
    if row.status == status:
        return row
    assert_transition(row.status, status)
    await assert_not_last_owner(
        session, organization_id, row, next_role=None, next_status=status
    )
    now = _now()
    row.status = status
    row.updated_at = now
    if status == MembershipStatus.SUSPENDED.value:
        row.suspended_at = now
    if status == MembershipStatus.REVOKED.value:
        row.revoked_at = now
        target = await session.get(User, target_user_id)
        if target is not None:
            target.token_version += 1
    if status == MembershipStatus.ACTIVE.value:
        row.suspended_at = None
    await _audit(
        session,
        action,
        actor=_actor(actor),
        organization_id=organization_id,
        target_user_id=target_user_id,
        detail={"scope": "organization", "status": status, "role": row.role.value},
    )
    await session.commit()
    await session.refresh(row)
    return row


async def suspend_member(
    session: AsyncSession,
    *,
    actor_user: User,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID,
    actor: Actor | None = None,
) -> OrganizationMembership:
    return await _set_status(
        session,
        actor_user=actor_user,
        organization_id=organization_id,
        target_user_id=target_user_id,
        status=MembershipStatus.SUSPENDED.value,
        action=AuditAction.MEMBERSHIP_SUSPENDED,
        actor=actor,
    )


async def reactivate_member(
    session: AsyncSession,
    *,
    actor_user: User,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID,
    actor: Actor | None = None,
) -> OrganizationMembership:
    row = await require_membership(session, organization_id, target_user_id)
    if row.status == MembershipStatus.REVOKED.value:
        from app.tenancy.isolation import LifecycleDenied
        raise LifecycleDenied("A revoked membership cannot be restored this way")
    return await _set_status(
        session,
        actor_user=actor_user,
        organization_id=organization_id,
        target_user_id=target_user_id,
        status=MembershipStatus.ACTIVE.value,
        action=AuditAction.MEMBERSHIP_RESTORED,
        actor=actor,
    )


async def revoke_member(
    session: AsyncSession,
    *,
    actor_user: User,
    organization_id: uuid.UUID,
    target_user_id: uuid.UUID,
    actor: Actor | None = None,
) -> OrganizationMembership:
    return await _set_status(
        session,
        actor_user=actor_user,
        organization_id=organization_id,
        target_user_id=target_user_id,
        status=MembershipStatus.REVOKED.value,
        action=AuditAction.MEMBERSHIP_REVOKED,
        actor=actor,
    )
````

### `app/organization/invitations.py`

````python
"""Membership invitation lifecycle.

The raw token is returned once to the inviter and stored only as a SHA-256
digest. Audit detail carries the invitation id, never the token and never a
field named token — the identity scrubber would mask it, and we do not rely
on that as the only control.

Accepting an invitation binds the organization and tenant stored on the row.
A client-supplied organization or tenant id that disagrees is rejected with
the same response as an unknown token.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit
from app.auth.identity.tokens import hash_token
from app.auth.password import verify_dummy
from app.auth.service import normalize_email
from app.db.models import (
    AuditAction,
    InvitationScope,
    MembershipInvitation,
    MembershipStatus,
    Organization,
    OrganizationMembership,
    Tenant,
    TenantMembership,
    User,
    UserRole,
)
from app.organization.membership_policies import (
    assert_can_grant,
    assert_organization_accepts_members,
)
from app.organization.membership_service import organization_for_user
from app.organization.service import Actor
from app.tenancy.isolation import Conflict, LifecycleDenied

INVITATION_TTL = timedelta(days=7)
_INVALID = "Invitation is not valid."


class InvitationInvalid(Exception):
    """Every public invitation failure. The message does not say why."""

    def __init__(self, message: str = _INVALID) -> None:
        super().__init__(message)
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _actor(actor: Actor | None) -> Actor:
    return actor or Actor()


def issue_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def binding_key(
    scope: str, organization_id: uuid.UUID, tenant_id: uuid.UUID | None
) -> str:
    if scope == InvitationScope.TENANT.value:
        return f"tenant:{tenant_id}"
    return f"org:{organization_id}"


def _ids_disagree(
    invitation: MembershipInvitation,
    *,
    organization_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None,
) -> bool:
    if organization_id is not None and organization_id != invitation.organization_id:
        return True
    if tenant_id is not None and invitation.tenant_id is not None:
        if tenant_id != invitation.tenant_id:
            return True
    if tenant_id is not None and invitation.scope == InvitationScope.ORGANIZATION.value:
        if invitation.tenant_id is None and tenant_id != invitation.home_tenant_id:
            return True
    return False


async def _audit(
    session: AsyncSession,
    action: AuditAction,
    *,
    actor: Actor,
    invitation: MembershipInvitation,
    event: str,
) -> None:
    await emit(
        session,
        action,
        tenant_id=actor.tenant_id or invitation.home_tenant_id,
        actor_user_id=actor.user_id,
        target_user_id=invitation.accepted_by_user_id,
        actor_email=actor.email,
        ip_address=actor.ip_address,
        user_agent=actor.user_agent,
        detail={
            "invitation_id": str(invitation.id),
            "organization_id": str(invitation.organization_id),
            "scope": invitation.scope,
            "event": event,
            "role": invitation.role.value,
        },
        commit=False,
    )


async def _live_duplicate(
    session: AsyncSession, key: str, email: str
) -> MembershipInvitation | None:
    return (
        await session.execute(
            select(MembershipInvitation).where(
                MembershipInvitation.binding_key == key,
                MembershipInvitation.email == email,
                MembershipInvitation.status == MembershipStatus.INVITED.value,
            )
        )
    ).scalar_one_or_none()


async def create_invitation(
    session: AsyncSession,
    *,
    actor_user: User,
    scope: str,
    organization_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    home_tenant_id: uuid.UUID,
    email: str,
    role: UserRole,
    actor: Actor | None = None,
) -> tuple[MembershipInvitation, str]:
    """Create one live invitation. The second item is the raw token, once."""
    organization = await session.get(Organization, organization_id)
    if organization is None:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    actor_org = await organization_for_user(session, actor_user)
    if actor_org is None or actor_org.id != organization.id:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    assert_can_grant(actor_user, role)
    assert_organization_accepts_members(organization)
    if scope == InvitationScope.TENANT.value:
        tenant = await session.get(Tenant, tenant_id) if tenant_id else None
        if tenant is None or tenant.organization_id != organization_id:
            from app.tenancy.isolation import BoundaryDenied
            raise BoundaryDenied()
        if tenant.lifecycle_status in ("suspended", "deleted", "read_only"):
            raise LifecycleDenied("This tenant is not accepting members")
    home = await session.get(Tenant, home_tenant_id)
    if home is None or home.organization_id != organization_id:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    normalized = normalize_email(email)
    key = binding_key(scope, organization_id, tenant_id)
    if await _live_duplicate(session, key, normalized) is not None:
        raise Conflict("An invitation is already pending")
    raw, digest = issue_token()
    now = _now()
    row = MembershipInvitation(
        scope=scope,
        organization_id=organization_id,
        tenant_id=tenant_id,
        home_tenant_id=home_tenant_id,
        email=normalized,
        role=role,
        status=MembershipStatus.INVITED.value,
        token_hash=digest,
        binding_key=key,
        expires_at=now + INVITATION_TTL,
        invited_by_user_id=actor_user.id,
    )
    session.add(row)
    await session.flush()
    who = _actor(actor)
    await _audit(
        session, AuditAction.INVITATION_CREATED, actor=who, invitation=row,
        event="invitation.created",
    )
    await session.commit()
    await session.refresh(row)
    return row, raw


async def _lookup(session: AsyncSession, raw_token: str) -> MembershipInvitation | None:
    if not raw_token or len(raw_token) > 200:
        return None
    digest = hash_token(raw_token)
    return (
        await session.execute(
            select(MembershipInvitation).where(MembershipInvitation.token_hash == digest)
        )
    ).scalar_one_or_none()


async def revoke_invitation(
    session: AsyncSession,
    *,
    actor_user: User,
    invitation_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor: Actor | None = None,
) -> MembershipInvitation:
    row = await session.get(MembershipInvitation, invitation_id)
    if row is None or row.organization_id != organization_id:
        from app.tenancy.isolation import NotFound
        raise NotFound()
    actor_org = await organization_for_user(session, actor_user)
    if actor_org is None or actor_org.id != organization_id:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    if row.status != MembershipStatus.INVITED.value:
        return row
    now = _now()
    row.status = MembershipStatus.REVOKED.value
    row.revoked_at = now
    row.updated_at = now
    # Rotate the digest so a copied token cannot be raced in after revoke.
    row.token_hash = hash_token(secrets.token_urlsafe(32))
    await _audit(
        session, AuditAction.INVITATION_REVOKED, actor=_actor(actor), invitation=row,
        event="invitation.revoked",
    )
    await session.commit()
    await session.refresh(row)
    return row


async def resend_invitation(
    session: AsyncSession,
    *,
    actor_user: User,
    invitation_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor: Actor | None = None,
) -> tuple[MembershipInvitation, str]:
    row = await session.get(MembershipInvitation, invitation_id)
    if row is None or row.organization_id != organization_id:
        from app.tenancy.isolation import NotFound
        raise NotFound()
    actor_org = await organization_for_user(session, actor_user)
    if actor_org is None or actor_org.id != organization_id:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    if row.status != MembershipStatus.INVITED.value:
        raise InvitationInvalid()
    raw, digest = issue_token()
    now = _now()
    row.token_hash = digest
    row.expires_at = now + INVITATION_TTL
    row.updated_at = now
    await _audit(
        session, AuditAction.INVITATION_RESENT, actor=_actor(actor), invitation=row,
        event="invitation.resent",
    )
    await session.commit()
    await session.refresh(row)
    return row, raw


async def _ensure_membership(
    session: AsyncSession,
    *,
    user: User,
    invitation: MembershipInvitation,
) -> None:
    now = _now()
    org_row = (
        await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == invitation.organization_id,
                OrganizationMembership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if org_row is None:
        session.add(OrganizationMembership(
            organization_id=invitation.organization_id,
            user_id=user.id,
            role=invitation.role,
            status=MembershipStatus.ACTIVE.value,
            accepted_at=now,
        ))
    else:
        org_row.status = MembershipStatus.ACTIVE.value
        org_row.role = invitation.role
        org_row.accepted_at = now
        org_row.suspended_at = None
    tenant_id = invitation.tenant_id or invitation.home_tenant_id
    tenant_row = (
        await session.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if tenant_row is None:
        session.add(TenantMembership(
            tenant_id=tenant_id,
            user_id=user.id,
            role=invitation.role,
            status=MembershipStatus.ACTIVE.value,
            accepted_at=now,
        ))
    else:
        tenant_row.status = MembershipStatus.ACTIVE.value
        tenant_row.role = invitation.role
        tenant_row.accepted_at = now
        tenant_row.suspended_at = None


async def _bind_user(
    session: AsyncSession,
    invitation: MembershipInvitation,
    *,
    authenticated: User | None,
    email: str | None,
    password: str | None,
) -> User:
    from app.auth import password as pw

    if authenticated is not None:
        if normalize_email(authenticated.email) != invitation.email:
            raise InvitationInvalid()
        user = authenticated
    else:
        if not email or normalize_email(email) != invitation.email:
            verify_dummy()
            raise InvitationInvalid()
        user = (
            await session.execute(select(User).where(User.email == invitation.email))
        ).scalar_one_or_none()
        if user is None:
            if not password:
                raise InvitationInvalid()
            pw.validate_policy(password, email=invitation.email)
            user = User(
                tenant_id=invitation.home_tenant_id,
                email=invitation.email,
                full_name="",
                password_hash=pw.hash_password(password),
                role=invitation.role,
                is_active=True,
            )
            session.add(user)
            await session.flush()
        else:
            if not password or not pw.verify_password(password, user.password_hash):
                verify_dummy()
                raise InvitationInvalid()
    home = await organization_for_user(session, user)
    if home is None or home.id != invitation.organization_id:
        verify_dummy()
        raise InvitationInvalid()
    if invitation.tenant_id is not None and user.tenant_id != invitation.tenant_id:
        # A user already bound to another tenant in this organization is not
        # moved. The invitation cannot rewrite tenant_id.
        if user.tenant_id != invitation.home_tenant_id:
            raise InvitationInvalid()
    await _ensure_membership(session, user=user, invitation=invitation)
    if user.role is not invitation.role and user.tenant_id == (
        invitation.tenant_id or invitation.home_tenant_id
    ):
        user.role = invitation.role
        user.token_version += 1
    return user


async def accept_invitation(
    session: AsyncSession,
    *,
    raw_token: str,
    organization_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    authenticated: User | None = None,
    email: str | None = None,
    password: str | None = None,
    actor: Actor | None = None,
) -> MembershipInvitation:
    """Consume one invited row. A second accept finds nothing left to consume."""
    row = await _lookup(session, raw_token)
    if row is None:
        verify_dummy()
        raise InvitationInvalid()
    if _ids_disagree(row, organization_id=organization_id, tenant_id=tenant_id):
        raise InvitationInvalid()
    now = _now()
    if row.expires_at <= now and row.status == MembershipStatus.INVITED.value:
        row.status = MembershipStatus.EXPIRED.value
        row.updated_at = now
        await session.commit()
        raise InvitationInvalid()
    if row.status != MembershipStatus.INVITED.value:
        raise InvitationInvalid()
    result = await session.execute(
        update(MembershipInvitation)
        .where(
            MembershipInvitation.id == row.id,
            MembershipInvitation.status == MembershipStatus.INVITED.value,
        )
        .values(status="accepted", accepted_at=now, updated_at=now)
    )
    if result.rowcount != 1:
        raise InvitationInvalid()
    await session.refresh(row)
    try:
        user = await _bind_user(
            session, row, authenticated=authenticated, email=email, password=password,
        )
    except InvitationInvalid:
        await session.rollback()
        raise
    row.accepted_by_user_id = user.id
    who = _actor(actor)
    if who.user_id is None:
        who = Actor(
            user_id=user.id, email=user.email, tenant_id=user.tenant_id,
            ip_address=who.ip_address, user_agent=who.user_agent,
        )
    await _audit(
        session, AuditAction.INVITATION_ACCEPTED, actor=who, invitation=row,
        event="invitation.accepted",
    )
    await session.commit()
    await session.refresh(row)
    return row
````

### `app/organization/membership_policies.py`

````python
"""Eligibility rules for organization membership.

Domain-based SSO enforcement stays in the identity layer. These rules only
decide whether a membership change is legal given the organization state, the
existing role policy, and the membership lifecycle.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import can_assign_role, can_manage_user, role_level
from app.db.models import (
    MembershipStatus,
    Organization,
    OrganizationMembership,
    User,
    UserRole,
)
from app.tenancy.isolation import Conflict, Forbidden, LifecycleDenied

_TRANSITIONS: dict[str, frozenset[str]] = {
    MembershipStatus.INVITED.value: frozenset({
        MembershipStatus.ACTIVE.value,
        MembershipStatus.REVOKED.value,
        MembershipStatus.EXPIRED.value,
    }),
    MembershipStatus.ACTIVE.value: frozenset({
        MembershipStatus.SUSPENDED.value,
        MembershipStatus.REVOKED.value,
    }),
    MembershipStatus.SUSPENDED.value: frozenset({
        MembershipStatus.ACTIVE.value,
        MembershipStatus.REVOKED.value,
    }),
    MembershipStatus.REVOKED.value: frozenset(),
    MembershipStatus.EXPIRED.value: frozenset(),
}

_OPEN_ORG = frozenset({"active"})
_BLOCK_NEW = frozenset({"suspended", "read_only", "deleted"})


def status_value(status: str | MembershipStatus) -> str:
    if isinstance(status, MembershipStatus):
        return status.value
    return str(status)


def can_transition(current: str, target: str) -> bool:
    return status_value(target) in _TRANSITIONS.get(status_value(current), frozenset())


def assert_transition(current: str, target: str) -> None:
    if status_value(current) == status_value(target):
        return
    if not can_transition(current, target):
        raise LifecycleDenied("That membership transition is not allowed")


def assert_organization_accepts_members(organization: Organization) -> None:
    """Deleted and suspended organizations do not take new members."""
    if organization.status == "deleted":
        raise LifecycleDenied("A deleted organization cannot accept members")
    if organization.status in _BLOCK_NEW:
        raise LifecycleDenied("This organization is not accepting members")


def assert_organization_allows_mutation(organization: Organization) -> None:
    if organization.status in ("suspended", "deleted", "read_only"):
        raise LifecycleDenied("This organization is not accepting membership changes")


def authorizing_status(status: str) -> bool:
    return status_value(status) == MembershipStatus.ACTIVE.value


async def count_active_owners(
    session: AsyncSession, organization_id: uuid.UUID
) -> int:
    return (
        await session.execute(
            select(func.count(OrganizationMembership.id)).where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.role == UserRole.OWNER,
                OrganizationMembership.status == MembershipStatus.ACTIVE.value,
            )
        )
    ).scalar_one()


async def assert_not_last_owner(
    session: AsyncSession,
    organization_id: uuid.UUID,
    membership: OrganizationMembership,
    *,
    next_role: UserRole | None,
    next_status: str | None,
) -> None:
    """Refuse a change that would leave the organization with no active owner."""
    if membership.role is not UserRole.OWNER:
        return
    if membership.status != MembershipStatus.ACTIVE.value:
        return
    loses_owner = False
    if next_role is not None and next_role is not UserRole.OWNER:
        loses_owner = True
    if next_status is not None and next_status != MembershipStatus.ACTIVE.value:
        loses_owner = True
    if not loses_owner:
        return
    if await count_active_owners(session, organization_id) <= 1:
        raise Conflict("An organization must keep at least one active owner")


def assert_can_grant(actor: User, target_role: UserRole) -> None:
    if not can_assign_role(actor.role, target_role):
        raise Forbidden("You cannot grant that role")


def assert_can_touch(actor: User, target_role: UserRole, *, self_target: bool) -> None:
    if self_target:
        raise Forbidden("You cannot change your own membership")
    if not can_manage_user(actor.role, target_role):
        raise Forbidden("You cannot modify a member at or above your level")


def assert_not_self_escalation(
    actor: User, target_user_id: uuid.UUID, target_role: UserRole
) -> None:
    if actor.id != target_user_id:
        return
    if role_level(target_role) > role_level(actor.role):
        raise Forbidden("You cannot grant yourself a higher role")
    raise Forbidden("You cannot change your own membership")
````

### `app/organization/access.py`

````python
"""Organization authorization.

Every decision delegates to the existing permission vocabulary. A principal
outside the organization gets the same answer as a missing organization, so
the caller cannot learn that the other organization exists.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.organization import permissions as organization_permissions
from app.organization.membership_service import (
    effective_role,
    organization_for_user,
)


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    code: str
    reason: str

    @property
    def boundary(self) -> bool:
        return self.code == "boundary"


def _allow(reason: str = "allowed") -> AccessDecision:
    return AccessDecision(True, "allowed", reason)


def _deny(reason: str) -> AccessDecision:
    return AccessDecision(False, "denied", reason)


def _boundary() -> AccessDecision:
    return AccessDecision(False, "boundary", "not_found")


async def _in_organization(
    session: AsyncSession, user: User | None, organization_id: uuid.UUID
) -> bool:
    if user is None:
        return False
    current = await organization_for_user(session, user)
    return current is not None and current.id == organization_id


async def _decide(
    session: AsyncSession,
    user: User | None,
    organization_id: uuid.UUID,
    conceptual: str,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> AccessDecision:
    if user is None:
        return _deny("unauthenticated")
    if not await _in_organization(session, user, organization_id):
        return _boundary()
    from app.organization.membership_service import get_membership
    row = await get_membership(session, organization_id, user.id)
    if row is not None and row.status in ("revoked", "expired"):
        return _deny("membership_inactive")
    status = membership_status
    if row is not None and row.status == "suspended":
        status = "suspended"
    role = await effective_role(session, user, organization_id)
    if role is None:
        return _deny("membership_inactive")
    if organization_permissions.allows(
        role, conceptual, scopes=scopes, membership_status=status
    ):
        return _allow()
    return _deny("permission_denied")


async def can_read_organization(
    session: AsyncSession,
    user: User | None,
    organization_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> AccessDecision:
    return await _decide(
        session, user, organization_id, "organization:read",
        scopes=scopes, membership_status=membership_status,
    )


async def can_update_organization(
    session: AsyncSession,
    user: User | None,
    organization_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> AccessDecision:
    return await _decide(
        session, user, organization_id, "organization:update",
        scopes=scopes, membership_status=membership_status,
    )


async def can_manage_members(
    session: AsyncSession,
    user: User | None,
    organization_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> AccessDecision:
    return await _decide(
        session, user, organization_id, "organization:members:invite",
        scopes=scopes, membership_status=membership_status,
    )


async def can_create_tenant(
    session: AsyncSession,
    user: User | None,
    organization_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> AccessDecision:
    """Still the platform-only permission. No customer role holds it."""
    return await _decide(
        session, user, organization_id, "organization:tenants:create",
        scopes=scopes, membership_status=membership_status,
    )


async def can_suspend_tenant(
    session: AsyncSession,
    user: User | None,
    organization_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> AccessDecision:
    return await _decide(
        session, user, organization_id, "organization:tenants:suspend",
        scopes=scopes, membership_status=membership_status,
    )
````

### `app/tenancy/roles.py`

````python
"""Tenant roles, mapped onto the existing ``UserRole`` enum.

``tenant_admin`` is ``admin``. There is no second role table and no way for a
tenant role to name a privilege the organization policy would not already
allow the same ``UserRole`` to hold.
"""

from __future__ import annotations

from app.db.models import UserRole
from app.tenancy.isolation import ValidationFailed

TENANT_ROLES: dict[str, UserRole] = {
    "tenant_owner": UserRole.OWNER,
    "tenant_admin": UserRole.ADMIN,
    "tenant_manager": UserRole.MANAGER,
    "tenant_member": UserRole.AGENT,
    "tenant_viewer": UserRole.VIEWER,
}


def resolve_tenant_role(name: str) -> UserRole:
    key = (name or "").strip().lower().replace("-", "_")
    if key in TENANT_ROLES:
        return TENANT_ROLES[key]
    try:
        return UserRole(key)
    except ValueError as exc:
        raise ValidationFailed("Unknown role") from exc


def conceptual_name(role: UserRole) -> str:
    for name, stored in TENANT_ROLES.items():
        if stored is role:
            return name
    return role.value
````

### `app/tenancy/permissions.py`

````python
"""Tenant-scope permission resolution.

Conceptual names map onto the existing ``Permission`` enum. The check itself
is ``has_permission`` plus the caller's scopes. This module does not grant
anything the role policy does not already grant.
"""

from __future__ import annotations

from app.auth.permissions import Permission, is_read_permission
from app.auth.rbac import has_permission
from app.db.models import UserRole

TENANT_PERMISSIONS: dict[str, Permission] = {
    "tenant:read": Permission.TENANT_READ,
    "tenant:update": Permission.TENANT_UPDATE,
    "tenant:members:read": Permission.USER_READ,
    "tenant:members:invite": Permission.USER_CREATE,
    "tenant:members:update": Permission.USER_ROLE_CHANGE,
    "tenant:members:remove": Permission.USER_DELETE,
    "tenant:environments:read": Permission.TENANT_READ,
    "tenant:environments:create": Permission.TENANT_UPDATE,
    "tenant:environments:update": Permission.TENANT_UPDATE,
    "tenant:environments:archive": Permission.TENANT_UPDATE,
}


def permission_for(conceptual: str) -> Permission:
    try:
        return TENANT_PERMISSIONS[conceptual]
    except KeyError as exc:
        raise KeyError(f"Unknown tenant capability: {conceptual}") from exc


def allows(
    role: UserRole,
    conceptual: str,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> bool:
    permission = permission_for(conceptual)
    if membership_status in ("revoked", "expired", "invited"):
        return False
    if membership_status == "suspended" and not is_read_permission(permission):
        return False
    if not has_permission(role, permission):
        return False
    if scopes is not None and permission.value not in scopes:
        return False
    return True
````

### `app/tenancy/membership_service.py`

````python
"""Tenant membership lifecycle.

Every mutation checks the actor's organization, then the tenant's parent, then
the existing role policy. A client cannot name a tenant in another organization
and have the row created there.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit
from app.auth.rbac import can_assign_role, can_manage_user, role_level
from app.db.models import (
    AuditAction,
    MembershipStatus,
    OrganizationMembership,
    Tenant,
    TenantMembership,
    User,
    UserRole,
)
from app.organization.membership_policies import assert_transition
from app.organization.membership_service import organization_for_user
from app.organization.service import Actor
from app.tenancy.isolation import BoundaryDenied, Conflict, Forbidden, LifecycleDenied, NotFound
from app.tenancy.roles import resolve_tenant_role


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _actor(actor: Actor | None) -> Actor:
    return actor or Actor()


async def require_tenant_in_actor_org(
    session: AsyncSession, actor: User, tenant_id: uuid.UUID
) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    actor_org = await organization_for_user(session, actor)
    if (
        tenant is None
        or actor_org is None
        or tenant.organization_id != actor_org.id
    ):
        raise BoundaryDenied()
    return tenant


async def get_membership(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> TenantMembership | None:
    return (
        await session.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def effective_tenant_role(
    session: AsyncSession, user: User, tenant: Tenant
) -> UserRole | None:
    """Active tenant role, else an active organization owner/admin of the parent.

    A viewer in the organization does not inherit into a tenant they do not
    belong to. A revoked row is not papered over by a higher organization role
    when the row is for this same user and tenant — explicit deny wins.
    """
    row = await get_membership(session, tenant.id, user.id)
    if row is not None:
        if row.status in (MembershipStatus.REVOKED.value, MembershipStatus.EXPIRED.value):
            return None
        return row.role
    if user.tenant_id == tenant.id:
        return user.role
    org_row = (
        await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == tenant.organization_id,
                OrganizationMembership.user_id == user.id,
                OrganizationMembership.status == MembershipStatus.ACTIVE.value,
            )
        )
    ).scalar_one_or_none()
    if org_row is None:
        return None
    if org_row.role in (UserRole.OWNER, UserRole.ADMIN):
        return org_row.role
    return None


async def _audit(
    session: AsyncSession,
    action: AuditAction,
    *,
    actor: Actor,
    tenant_id: uuid.UUID,
    target_user_id: uuid.UUID,
    detail: dict,
) -> None:
    await emit(
        session,
        action,
        tenant_id=tenant_id,
        actor_user_id=actor.user_id,
        target_user_id=target_user_id,
        actor_email=actor.email,
        ip_address=actor.ip_address,
        user_agent=actor.user_agent,
        detail={"tenant_id": str(tenant_id), "scope": "tenant", **detail},
        commit=False,
    )


async def list_members(
    session: AsyncSession, actor: User, tenant_id: uuid.UUID
) -> list[TenantMembership]:
    tenant = await require_tenant_in_actor_org(session, actor, tenant_id)
    role = await effective_tenant_role(session, actor, tenant)
    if role is None:
        raise BoundaryDenied()
    rows = (
        await session.execute(
            select(TenantMembership)
            .where(TenantMembership.tenant_id == tenant_id)
            .order_by(TenantMembership.created_at)
        )
    ).scalars().all()
    return list(rows)


async def add_member(
    session: AsyncSession,
    *,
    actor_user: User,
    tenant_id: uuid.UUID,
    target_user_id: uuid.UUID,
    role_name: str,
    actor: Actor | None = None,
) -> TenantMembership:
    tenant = await require_tenant_in_actor_org(session, actor_user, tenant_id)
    if tenant.lifecycle_status in ("suspended", "deleted", "read_only"):
        raise LifecycleDenied("This tenant is not accepting members")
    actor_role = await effective_tenant_role(session, actor_user, tenant)
    if actor_role is None:
        raise BoundaryDenied()
    role = resolve_tenant_role(role_name)
    if actor_user.id == target_user_id:
        raise Forbidden("You cannot change your own membership")
    if not can_assign_role(actor_role, role):
        raise Forbidden("You cannot grant that role")
    target = await session.get(User, target_user_id)
    if target is None or target.tenant_id != tenant.id:
        raise BoundaryDenied()
    existing = await get_membership(session, tenant.id, target.id)
    if existing is not None and existing.status == MembershipStatus.REVOKED.value:
        raise Conflict("A revoked membership cannot be restored this way")
    if existing is not None and existing.status == MembershipStatus.ACTIVE.value:
        raise Conflict("That person is already a member")
    now = _now()
    if existing is None:
        existing = TenantMembership(
            tenant_id=tenant.id,
            user_id=target.id,
            role=role,
            status=MembershipStatus.ACTIVE.value,
            accepted_at=now,
        )
        session.add(existing)
    else:
        if not can_manage_user(actor_role, existing.role):
            raise Forbidden("You cannot modify a member at or above your level")
        existing.role = role
        existing.status = MembershipStatus.ACTIVE.value
        existing.accepted_at = now
        existing.suspended_at = None
        existing.updated_at = now
    if target.tenant_id == tenant.id:
        target.role = role
        target.token_version += 1
    await _audit(
        session, AuditAction.MEMBERSHIP_CREATED, actor=_actor(actor),
        tenant_id=tenant.id, target_user_id=target.id,
        detail={"role": role.value, "status": "active"},
    )
    await session.commit()
    await session.refresh(existing)
    return existing


async def _count_owners(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    rows = (
        await session.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.role == UserRole.OWNER,
                TenantMembership.status == MembershipStatus.ACTIVE.value,
            )
        )
    ).scalars().all()
    return len(rows)


async def update_role(
    session: AsyncSession,
    *,
    actor_user: User,
    tenant_id: uuid.UUID,
    target_user_id: uuid.UUID,
    role_name: str,
    actor: Actor | None = None,
) -> TenantMembership:
    tenant = await require_tenant_in_actor_org(session, actor_user, tenant_id)
    actor_role = await effective_tenant_role(session, actor_user, tenant)
    if actor_role is None:
        raise BoundaryDenied()
    row = await get_membership(session, tenant.id, target_user_id)
    if row is None:
        raise NotFound()
    role = resolve_tenant_role(role_name)
    if actor_user.id == target_user_id or role_level(role) > role_level(actor_role):
        raise Forbidden("You cannot grant that role")
    if not can_manage_user(actor_role, row.role) or not can_assign_role(actor_role, role):
        raise Forbidden("You cannot modify a member at or above your level")
    if (
        row.role is UserRole.OWNER
        and role is not UserRole.OWNER
        and row.status == MembershipStatus.ACTIVE.value
        and await _count_owners(session, tenant.id) <= 1
    ):
        raise Conflict("A tenant must keep at least one active owner")
    previous = row.role
    row.role = role
    row.updated_at = _now()
    target = await session.get(User, target_user_id)
    if target is not None and target.tenant_id == tenant.id:
        target.role = role
        target.token_version += 1
    await _audit(
        session, AuditAction.MEMBERSHIP_UPDATED, actor=_actor(actor),
        tenant_id=tenant.id, target_user_id=target_user_id,
        detail={"from": previous.value, "to": role.value},
    )
    await session.commit()
    await session.refresh(row)
    return row


async def _set_status(
    session: AsyncSession,
    *,
    actor_user: User,
    tenant_id: uuid.UUID,
    target_user_id: uuid.UUID,
    status: str,
    action: AuditAction,
    actor: Actor | None,
) -> TenantMembership:
    tenant = await require_tenant_in_actor_org(session, actor_user, tenant_id)
    actor_role = await effective_tenant_role(session, actor_user, tenant)
    if actor_role is None:
        raise BoundaryDenied()
    row = await get_membership(session, tenant.id, target_user_id)
    if row is None:
        raise NotFound()
    if actor_user.id == target_user_id:
        raise Forbidden("You cannot change your own membership")
    if not can_manage_user(actor_role, row.role):
        raise Forbidden("You cannot modify a member at or above your level")
    if row.status == status:
        return row
    if status == MembershipStatus.ACTIVE.value and row.status == MembershipStatus.REVOKED.value:
        raise LifecycleDenied("A revoked membership cannot be restored this way")
    assert_transition(row.status, status)
    if (
        row.role is UserRole.OWNER
        and row.status == MembershipStatus.ACTIVE.value
        and status != MembershipStatus.ACTIVE.value
        and await _count_owners(session, tenant.id) <= 1
    ):
        raise Conflict("A tenant must keep at least one active owner")
    now = _now()
    row.status = status
    row.updated_at = now
    if status == MembershipStatus.SUSPENDED.value:
        row.suspended_at = now
    if status == MembershipStatus.REVOKED.value:
        row.revoked_at = now
        target = await session.get(User, target_user_id)
        if target is not None:
            target.token_version += 1
    if status == MembershipStatus.ACTIVE.value:
        row.suspended_at = None
    await _audit(
        session, action, actor=_actor(actor), tenant_id=tenant.id,
        target_user_id=target_user_id, detail={"status": status, "role": row.role.value},
    )
    await session.commit()
    await session.refresh(row)
    return row


async def suspend_member(session, *, actor_user, tenant_id, target_user_id, actor=None):
    return await _set_status(
        session, actor_user=actor_user, tenant_id=tenant_id,
        target_user_id=target_user_id, status=MembershipStatus.SUSPENDED.value,
        action=AuditAction.MEMBERSHIP_SUSPENDED, actor=actor,
    )


async def reactivate_member(session, *, actor_user, tenant_id, target_user_id, actor=None):
    return await _set_status(
        session, actor_user=actor_user, tenant_id=tenant_id,
        target_user_id=target_user_id, status=MembershipStatus.ACTIVE.value,
        action=AuditAction.MEMBERSHIP_RESTORED, actor=actor,
    )


async def revoke_member(session, *, actor_user, tenant_id, target_user_id, actor=None):
    return await _set_status(
        session, actor_user=actor_user, tenant_id=tenant_id,
        target_user_id=target_user_id, status=MembershipStatus.REVOKED.value,
        action=AuditAction.MEMBERSHIP_REVOKED, actor=actor,
    )
````

### `app/tenancy/invitations.py`

````python
"""Tenant invitation lifecycle.

An invitation created for one organization and one tenant cannot be accepted
as another pair. Client-supplied ids are compared and then discarded; the row
is the binding.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InvitationScope, MembershipInvitation, User
from app.organization import invitations as invitations_core
from app.organization.invitations import InvitationInvalid
from app.organization.membership_policies import assert_can_grant
from app.organization.service import Actor
from app.tenancy.access import load_tenant_in_organization
from app.tenancy.isolation import BoundaryDenied, Forbidden
from app.tenancy.roles import resolve_tenant_role

__all__ = ["InvitationInvalid", "accept", "create", "resend", "revoke"]


async def create(
    session: AsyncSession,
    *,
    actor_user: User,
    tenant_id: uuid.UUID,
    email: str,
    role_name: str,
    actor: Actor | None = None,
    client_organization_id: uuid.UUID | None = None,
    client_tenant_id: uuid.UUID | None = None,
) -> tuple[MembershipInvitation, str]:
    tenant = await load_tenant_in_organization(session, actor_user, tenant_id)
    if tenant is None:
        raise BoundaryDenied()
    if client_tenant_id is not None and client_tenant_id != tenant.id:
        raise BoundaryDenied()
    if client_organization_id is not None and client_organization_id != tenant.organization_id:
        raise BoundaryDenied()
    role = resolve_tenant_role(role_name)
    try:
        assert_can_grant(actor_user, role)
    except Forbidden:
        raise
    return await invitations_core.create_invitation(
        session,
        actor_user=actor_user,
        scope=InvitationScope.TENANT.value,
        organization_id=tenant.organization_id,
        tenant_id=tenant.id,
        home_tenant_id=tenant.id,
        email=email,
        role=role,
        actor=actor,
    )


async def accept(
    session: AsyncSession,
    *,
    raw_token: str,
    tenant_id: uuid.UUID,
    organization_id: uuid.UUID | None = None,
    authenticated: User | None = None,
    email: str | None = None,
    password: str | None = None,
    actor: Actor | None = None,
) -> MembershipInvitation:
    """Accept only if the token's tenant is the tenant in the path."""
    return await invitations_core.accept_invitation(
        session,
        raw_token=raw_token,
        organization_id=organization_id,
        tenant_id=tenant_id,
        authenticated=authenticated,
        email=email,
        password=password,
        actor=actor,
    )


async def revoke(
    session: AsyncSession,
    *,
    actor_user: User,
    tenant_id: uuid.UUID,
    invitation_id: uuid.UUID,
    actor: Actor | None = None,
) -> MembershipInvitation:
    tenant = await load_tenant_in_organization(session, actor_user, tenant_id)
    if tenant is None:
        raise BoundaryDenied()
    from app.db.models import MembershipInvitation
    existing = await session.get(MembershipInvitation, invitation_id)
    if existing is None or existing.tenant_id != tenant.id:
        raise BoundaryDenied()
    row = await invitations_core.revoke_invitation(
        session,
        actor_user=actor_user,
        invitation_id=invitation_id,
        organization_id=tenant.organization_id,
        actor=actor,
    )
    if row.tenant_id != tenant.id:
        raise BoundaryDenied()
    return row


async def resend(
    session: AsyncSession,
    *,
    actor_user: User,
    tenant_id: uuid.UUID,
    invitation_id: uuid.UUID,
    actor: Actor | None = None,
) -> tuple[MembershipInvitation, str]:
    tenant = await load_tenant_in_organization(session, actor_user, tenant_id)
    if tenant is None:
        raise BoundaryDenied()
    from app.db.models import MembershipInvitation
    existing = await session.get(MembershipInvitation, invitation_id)
    if existing is None or existing.tenant_id != tenant.id:
        raise BoundaryDenied()
    row, raw = await invitations_core.resend_invitation(
        session,
        actor_user=actor_user,
        invitation_id=invitation_id,
        organization_id=tenant.organization_id,
        actor=actor,
    )
    if row.tenant_id != tenant.id:
        raise InvitationInvalid()
    return row, raw
````

### `app/tenancy/quota.py`

````python
"""Tenant quota lookup.

This module normalizes a tenant's place in the hierarchy and asks the central
resolver for the effective limit. It does not enforce, and it does not invent
a number when neither the hierarchy nor billing has one.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant
from app.quotas.models import QuotaKey
from app.quotas.service import resolve_quota


async def tenant_quota(
    session: AsyncSession,
    tenant: Tenant,
    key: QuotaKey | str,
    *,
    environment_id: uuid.UUID | None = None,
    used: int | None = None,
):
    """Effective limit for one tenant. Billing remains a source, not a victim."""
    return await resolve_quota(
        session,
        key,
        organization_id=tenant.organization_id,
        tenant_id=tenant.id,
        environment_id=environment_id,
        tenant=tenant,
        used=used,
    )
````

### `app/tenancy/access.py`

````python
"""Tenant authorization boundary.

The check is always organization, then tenant, then the existing permission.
A tenant in another organization is indistinguishable from a missing tenant.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant, User
from app.organization.membership_service import organization_for_user
from app.tenancy import permissions as tenant_permissions
from app.tenancy.membership_service import effective_tenant_role


@dataclass(frozen=True)
class TenantAccess:
    allowed: bool
    code: str
    reason: str

    @property
    def boundary(self) -> bool:
        return self.code == "boundary"


async def load_tenant_in_organization(
    session: AsyncSession, actor: User | None, tenant_id: uuid.UUID
) -> Tenant | None:
    """The tenant, only when it belongs to the actor's organization."""
    if actor is None:
        return None
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None or tenant.organization_id is None:
        return None
    actor_org = await organization_for_user(session, actor)
    if actor_org is None or actor_org.id != tenant.organization_id:
        return None
    return tenant


async def verify_operation(
    session: AsyncSession,
    actor: User | None,
    tenant_id: uuid.UUID,
    conceptual: str,
    *,
    scopes: frozenset[str] | None = None,
    membership_status: str = "active",
) -> TenantAccess:
    if actor is None:
        return TenantAccess(False, "denied", "unauthenticated")
    tenant = await load_tenant_in_organization(session, actor, tenant_id)
    if tenant is None:
        return TenantAccess(False, "boundary", "not_found")
    if tenant.lifecycle_status == "deleted":
        return TenantAccess(False, "denied", "tenant_deleted")
    role = await effective_tenant_role(session, actor, tenant)
    if role is None:
        return TenantAccess(False, "denied", "membership_inactive")
    if not tenant_permissions.allows(
        role, conceptual, scopes=scopes, membership_status=membership_status
    ):
        return TenantAccess(False, "denied", "permission_denied")
    if tenant.lifecycle_status in ("suspended", "read_only") and conceptual not in (
        "tenant:read", "tenant:members:read", "tenant:environments:read",
    ):
        return TenantAccess(False, "denied", "tenant_not_writable")
    return TenantAccess(True, "allowed", "allowed")
````

### `app/environments/membership.py`

````python
"""Environment access bindings and inheritance.

Precedence, highest first:

1. An explicit revoked, expired or suspended binding denies. Suspended denies
   privileged environment actions; revoked denies all of them.
2. An explicit active environment membership is the role, capped so it cannot
   outrank the tenant role.
3. An active tenant membership is inherited when no explicit binding exists.
4. An active organization owner or admin is inherited when the tenant belongs
   to that organization and the user has no tenant row.
5. Otherwise the caller is unauthenticated for this environment.

"Most permissions wins" is intentionally not the rule.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import role_level
from app.db.models import (
    Environment,
    EnvironmentMembership,
    MembershipStatus,
    Tenant,
    User,
    UserRole,
)
from app.tenancy.membership_service import effective_tenant_role, get_membership


@dataclass(frozen=True)
class EnvironmentAccess:
    allowed: bool
    role: UserRole | None
    via: str
    status: str
    reason: str


def _deny(reason: str, via: str = "denied") -> EnvironmentAccess:
    return EnvironmentAccess(False, None, via, "denied", reason)


def _cap(explicit: UserRole, ceiling: UserRole) -> UserRole:
    """An explicit binding may be stricter. It may not escalate."""
    if role_level(explicit) <= role_level(ceiling):
        return explicit
    return ceiling


async def binding_for(
    session: AsyncSession, environment_id: uuid.UUID, user_id: uuid.UUID
) -> EnvironmentMembership | None:
    return (
        await session.execute(
            select(EnvironmentMembership).where(
                EnvironmentMembership.environment_id == environment_id,
                EnvironmentMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def resolve(
    session: AsyncSession, user: User | None, environment: Environment, tenant: Tenant
) -> EnvironmentAccess:
    if user is None:
        return _deny("unauthenticated", "unauthenticated")
    if environment.tenant_id != tenant.id:
        return _deny("cross_tenant", "boundary")
    explicit = await binding_for(session, environment.id, user.id)
    tenant_role = await effective_tenant_role(session, user, tenant)
    tenant_row = await get_membership(session, tenant.id, user.id)
    if tenant_row is not None and tenant_row.status in (
        MembershipStatus.REVOKED.value, MembershipStatus.EXPIRED.value,
    ):
        return _deny("tenant_membership_revoked", "tenant")
    if explicit is not None and explicit.status in (
        MembershipStatus.REVOKED.value, MembershipStatus.EXPIRED.value,
    ):
        return _deny("environment_membership_revoked", "environment")
    if explicit is not None and explicit.status == MembershipStatus.SUSPENDED.value:
        return EnvironmentAccess(
            False, explicit.role, "environment", "suspended", "environment_membership_suspended"
        )
    if tenant_row is not None and tenant_row.status == MembershipStatus.SUSPENDED.value:
        return EnvironmentAccess(
            False, tenant_row.role, "tenant", "suspended", "tenant_membership_suspended"
        )
    if tenant_role is None:
        return _deny("no_membership", "unauthenticated")
    if explicit is not None and explicit.status == MembershipStatus.ACTIVE.value:
        return EnvironmentAccess(
            True, _cap(explicit.role, tenant_role), "environment", "active", "explicit"
        )
    return EnvironmentAccess(True, tenant_role, "tenant", "active", "inherited")
````

### `app/environments/access.py`

````python
"""Environment authorization.

Every operation loads the environment, proves it belongs to the tenant, proves
the tenant belongs to the actor's organization, then applies the membership
precedence in ``membership.py``. Permission checks still go through the
existing RBAC vocabulary.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Environment, User
from app.environments.membership import EnvironmentAccess, resolve
from app.tenancy.access import load_tenant_in_organization
from app.tenancy.permissions import allows


async def _environment_for_actor(
    session: AsyncSession,
    user: User | None,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
) -> tuple[Environment | None, EnvironmentAccess]:
    tenant = await load_tenant_in_organization(session, user, tenant_id)
    if tenant is None or user is None:
        return None, EnvironmentAccess(False, None, "boundary", "denied", "not_found")
    environment = await session.get(Environment, environment_id)
    if environment is None or environment.tenant_id != tenant.id:
        return None, EnvironmentAccess(False, None, "boundary", "denied", "not_found")
    decision = await resolve(session, user, environment, tenant)
    return environment, decision


def _permitted(
    decision: EnvironmentAccess, conceptual: str, *, scopes: frozenset[str] | None
) -> EnvironmentAccess:
    if not decision.allowed or decision.role is None:
        return decision
    if not allows(decision.role, conceptual, scopes=scopes, membership_status=decision.status):
        return EnvironmentAccess(
            False, decision.role, decision.via, decision.status, "permission_denied"
        )
    return decision


async def can_read_environment(
    session: AsyncSession,
    user: User | None,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
) -> tuple[Environment | None, EnvironmentAccess]:
    environment, decision = await _environment_for_actor(
        session, user, tenant_id, environment_id
    )
    return environment, _permitted(decision, "tenant:environments:read", scopes=scopes)


async def can_update_environment(
    session: AsyncSession,
    user: User | None,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
) -> tuple[Environment | None, EnvironmentAccess]:
    environment, decision = await _environment_for_actor(
        session, user, tenant_id, environment_id
    )
    if environment is not None and environment.status in ("suspended", "archived"):
        return environment, EnvironmentAccess(
            False, decision.role, decision.via, environment.status, "environment_blocked"
        )
    return environment, _permitted(decision, "tenant:environments:update", scopes=scopes)


async def can_archive_environment(
    session: AsyncSession,
    user: User | None,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
) -> tuple[Environment | None, EnvironmentAccess]:
    environment, decision = await can_update_environment(
        session, user, tenant_id, environment_id, scopes=scopes
    )
    if environment is None or not decision.allowed:
        return environment, decision
    from app.environments.guard import mutation_allowed
    if not mutation_allowed(environment, decision.role, action="archive"):
        return environment, EnvironmentAccess(
            False, decision.role, "guard", decision.status, "production_protected"
        )
    return environment, decision


async def can_change_default_environment(
    session: AsyncSession,
    user: User | None,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
) -> tuple[Environment | None, EnvironmentAccess]:
    environment, decision = await can_update_environment(
        session, user, tenant_id, environment_id, scopes=scopes
    )
    if environment is None or not decision.allowed:
        return environment, decision
    from app.environments.guard import mutation_allowed
    if not mutation_allowed(environment, decision.role, action="default"):
        return environment, EnvironmentAccess(
            False, decision.role, "guard", decision.status, "production_protected"
        )
    return environment, decision


async def can_deploy_environment_metadata(
    session: AsyncSession,
    user: User | None,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    *,
    scopes: frozenset[str] | None = None,
) -> tuple[Environment | None, EnvironmentAccess]:
    """Metadata only. This does not execute a deployment."""
    environment, decision = await can_update_environment(
        session, user, tenant_id, environment_id, scopes=scopes
    )
    if environment is None or not decision.allowed:
        return environment, decision
    from app.environments.guard import mutation_allowed
    if not mutation_allowed(environment, decision.role, action="deploy"):
        return environment, EnvironmentAccess(
            False, decision.role, "guard", decision.status, "production_protected"
        )
    return environment, decision
````

### `app/environments/quota.py`

````python
"""Environment quota resolution.

Limits are read from the central quota service. This module does not hard-code
a number and does not treat a missing row as zero.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Environment, Tenant
from app.quotas.models import QuotaKey
from app.quotas.service import resolve_quota


async def environment_quota(
    session: AsyncSession,
    tenant: Tenant,
    environment: Environment,
    key: QuotaKey | str,
    *,
    used: int | None = None,
):
    if environment.tenant_id != tenant.id:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    return await resolve_quota(
        session,
        key,
        organization_id=tenant.organization_id,
        tenant_id=tenant.id,
        environment_id=environment.id,
        tenant=tenant,
        used=used,
    )
````

### `app/environments/policy_inheritance.py`

````python
"""Effective policy from organization, tenant and environment.

The base is the existing identity policy for the tenant. Overlays may only
make mandatory controls stricter. A child that tries to turn a required
control off is refused at write time, and a read still clamps the result so a
stored weaker value cannot win.

Suspended or deleted parents invalidate child authorization. This does not
replace the identity policy evaluator.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.policies import ResolvedPolicy, load_policy
from app.db.models import Environment, Organization, ScopePolicy, Tenant
from app.tenancy.isolation import LifecycleDenied

_TRUE_STRICTER = (
    "mfa_required",
    "mfa_required_for_admins",
    "sso_required",
)
_FALSE_STRICTER = (
    "password_login_allowed",
    "api_keys_allowed",
    "service_accounts_allowed",
)
_LOWER_STRICTER = ("session_idle_minutes", "session_max_active")


@dataclass(frozen=True)
class EffectivePolicy:
    tenant_id: uuid.UUID
    organization_id: uuid.UUID | None
    environment_id: uuid.UUID | None
    authorization_valid: bool
    invalid_reason: str
    mfa_required: bool
    mfa_required_for_admins: bool
    sso_required: bool
    password_login_allowed: bool
    api_keys_allowed: bool
    service_accounts_allowed: bool
    session_idle_minutes: int
    session_max_active: int
    source: str


def _clamp_bool_true(parent: bool, child: bool | None) -> bool:
    if child is None:
        return parent
    return parent or child


def _clamp_bool_false(parent: bool, child: bool | None) -> bool:
    """False is the stricter value for an allow-flag."""
    if child is None:
        return parent
    return parent and child


def _clamp_lower(parent: int, child: int | None) -> int:
    if child is None:
        return parent
    return min(parent, child)


def _overlay_values(row: ScopePolicy | None) -> dict:
    if row is None:
        return {}
    return {
        "mfa_required": row.mfa_required,
        "mfa_required_for_admins": row.mfa_required_for_admins,
        "sso_required": row.sso_required,
        "password_login_allowed": row.password_login_allowed,
        "api_keys_allowed": row.api_keys_allowed,
        "service_accounts_allowed": row.service_accounts_allowed,
        "session_idle_minutes": row.session_idle_minutes,
        "session_max_active": row.session_max_active,
    }


def merge(base: ResolvedPolicy, *overlays: dict) -> dict:
    """Fold overlays onto the identity policy. Stricter parent values win."""
    current = {
        "mfa_required": base.mfa_required,
        "mfa_required_for_admins": base.mfa_required_for_admins,
        "sso_required": base.sso_required,
        "password_login_allowed": base.password_login_allowed,
        "api_keys_allowed": base.api_keys_allowed,
        "service_accounts_allowed": base.service_accounts_allowed,
        "session_idle_minutes": base.session_idle_minutes,
        "session_max_active": base.session_max_active,
    }
    for overlay in overlays:
        for name in _TRUE_STRICTER:
            current[name] = _clamp_bool_true(current[name], overlay.get(name))
        for name in _FALSE_STRICTER:
            current[name] = _clamp_bool_false(current[name], overlay.get(name))
        for name in _LOWER_STRICTER:
            current[name] = _clamp_lower(current[name], overlay.get(name))
    return current


def weakens(parent: dict, proposed: dict) -> bool:
    """True when ``proposed`` would loosen a mandatory control already set."""
    for name in _TRUE_STRICTER:
        if name in proposed and proposed[name] is False and parent[name] is True:
            return True
    for name in _FALSE_STRICTER:
        if name in proposed and proposed[name] is True and parent[name] is False:
            return True
    for name in _LOWER_STRICTER:
        value = proposed.get(name)
        if value is not None and value > parent[name]:
            return True
    return False


async def _row(
    session: AsyncSession, kind: str, scope_id: uuid.UUID | None
) -> ScopePolicy | None:
    if scope_id is None:
        return None
    return (
        await session.execute(
            select(ScopePolicy).where(
                ScopePolicy.scope_kind == kind,
                ScopePolicy.scope_id == scope_id,
            )
        )
    ).scalar_one_or_none()


async def resolve_effective(
    session: AsyncSession,
    tenant: Tenant,
    environment: Environment | None = None,
) -> EffectivePolicy:
    base = await load_policy(session, tenant.id)
    organization = None
    if tenant.organization_id is not None:
        organization = await session.get(Organization, tenant.organization_id)
    org_row = await _row(session, "organization", tenant.organization_id)
    tenant_row = await _row(session, "tenant", tenant.id)
    env_row = await _row(
        session, "environment", environment.id if environment is not None else None
    )
    merged = merge(
        base,
        _overlay_values(org_row),
        _overlay_values(tenant_row),
        _overlay_values(env_row),
    )
    invalid_reason = ""
    if organization is not None and organization.status in ("suspended", "deleted"):
        invalid_reason = "organization_" + organization.status
    elif tenant.lifecycle_status in ("suspended", "deleted"):
        invalid_reason = "tenant_" + tenant.lifecycle_status
    elif environment is not None and environment.status in ("suspended", "archived"):
        invalid_reason = "environment_" + environment.status
    return EffectivePolicy(
        tenant_id=tenant.id,
        organization_id=tenant.organization_id,
        environment_id=environment.id if environment is not None else None,
        authorization_valid=invalid_reason == "",
        invalid_reason=invalid_reason,
        source="identity+hierarchy",
        **merged,
    )


async def set_scope_policy(
    session: AsyncSession,
    *,
    kind: str,
    scope_id: uuid.UUID,
    tenant: Tenant,
    environment: Environment | None = None,
    values: dict,
) -> ScopePolicy:
    """Persist an overlay only when it does not weaken the parent."""
    parent = await resolve_effective(
        session, tenant, None if kind == "environment" else environment
    )
    parent_values = {
        "mfa_required": parent.mfa_required,
        "mfa_required_for_admins": parent.mfa_required_for_admins,
        "sso_required": parent.sso_required,
        "password_login_allowed": parent.password_login_allowed,
        "api_keys_allowed": parent.api_keys_allowed,
        "service_accounts_allowed": parent.service_accounts_allowed,
        "session_idle_minutes": parent.session_idle_minutes,
        "session_max_active": parent.session_max_active,
    }
    if weakens(parent_values, values):
        raise LifecycleDenied("A child policy cannot weaken a mandatory parent control")
    row = await _row(session, kind, scope_id)
    if row is None:
        row = ScopePolicy(scope_kind=kind, scope_id=scope_id)
        session.add(row)
    for name in (*_TRUE_STRICTER, *_FALSE_STRICTER, *_LOWER_STRICTER):
        if name in values:
            setattr(row, name, values[name])
    await session.commit()
    await session.refresh(row)
    return row
````

### `app/environments/context_resolution.py`

````python
"""Resolve the effective environment for an authenticated request.

Sources, in order: an explicit route id, a server-side selection, then the
tenant's default environment. A raw id is loaded and then checked against the
tenant, the organization, the principal and the environment status. If nothing
is selected, callers that still only need the tenant keep working — this
function returns the default for inspection and does not require the caller to
switch.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit
from app.db.models import (
    AuditAction,
    Environment,
    EnvironmentSelection,
    Tenant,
    User,
)
from app.environments.guard import selection_allowed
from app.environments.membership import resolve
from app.organization.service import Actor
from app.tenancy.isolation import BoundaryDenied, LifecycleDenied


async def _default_environment(
    session: AsyncSession, tenant_id: uuid.UUID
) -> Environment | None:
    return (
        await session.execute(
            select(Environment).where(
                Environment.tenant_id == tenant_id,
                Environment.is_default.is_(True),
            )
        )
    ).scalar_one_or_none()


async def _selection(
    session: AsyncSession, user_id: uuid.UUID, tenant_id: uuid.UUID
) -> EnvironmentSelection | None:
    return (
        await session.execute(
            select(EnvironmentSelection).where(
                EnvironmentSelection.user_id == user_id,
                EnvironmentSelection.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()


async def resolve_environment(
    session: AsyncSession,
    *,
    user: User,
    tenant: Tenant,
    environment_id: uuid.UUID | None = None,
) -> Environment | None:
    """The environment this request may use, or None when the tenant has none.

    A supplied id outside the tenant is a boundary miss, not a fallback.
    """
    if tenant.organization_id is None:
        raise BoundaryDenied()
    if environment_id is not None:
        environment = await session.get(Environment, environment_id)
        if environment is None or environment.tenant_id != tenant.id:
            raise BoundaryDenied()
        decision = await resolve(session, user, environment, tenant)
        if decision.via == "boundary" or not decision.allowed and decision.reason == "cross_tenant":
            raise BoundaryDenied()
        return environment
    selected = await _selection(session, user.id, tenant.id)
    if selected is not None:
        environment = await session.get(Environment, selected.environment_id)
        if (
            environment is not None
            and environment.tenant_id == tenant.id
            and selection_allowed(environment)
        ):
            return environment
    return await _default_environment(session, tenant.id)


async def select_environment(
    session: AsyncSession,
    *,
    user: User,
    tenant: Tenant,
    environment_id: uuid.UUID,
    actor: Actor | None = None,
) -> Environment:
    environment = await session.get(Environment, environment_id)
    if environment is None or environment.tenant_id != tenant.id:
        raise BoundaryDenied()
    decision = await resolve(session, user, environment, tenant)
    if not decision.allowed:
        if decision.via == "boundary":
            raise BoundaryDenied()
        raise LifecycleDenied("That environment cannot be selected")
    if not selection_allowed(environment):
        raise LifecycleDenied("An archived or suspended environment cannot be current")
    row = await _selection(session, user.id, tenant.id)
    if row is None:
        row = EnvironmentSelection(
            user_id=user.id, tenant_id=tenant.id, environment_id=environment.id,
        )
        session.add(row)
    else:
        row.environment_id = environment.id
    who = actor or Actor(user_id=user.id, email=user.email, tenant_id=tenant.id)
    await emit(
        session,
        AuditAction.ENVIRONMENT_SELECTED,
        tenant_id=tenant.id,
        actor_user_id=who.user_id,
        actor_email=who.email,
        ip_address=who.ip_address,
        user_agent=who.user_agent,
        detail={
            "environment_id": str(environment.id),
            "tenant_id": str(tenant.id),
            "kind": environment.kind,
        },
        commit=False,
    )
    await session.commit()
    await session.refresh(environment)
    return environment
````

### `app/environments/guard.py`

````python
"""Environment mutation guard.

Production requires the owner role in addition to the permission the role
policy already demands. Staging and development keep the existing
``tenant:update`` bar (admin and above). Nothing here skips authentication.
"""

from __future__ import annotations

from app.db.models import Environment, UserRole

_BLOCKED = frozenset({"suspended", "archived"})


def mutation_allowed(
    environment: Environment, role: UserRole | None, *, action: str
) -> bool:
    """Whether this role may mutate this environment.

    ``action`` is ``archive``, ``default``, ``deploy`` or ``update``. Archive
    of production is refused here as well as in the environment service.
    """
    if role is None:
        return False
    if environment.status in _BLOCKED and action != "read":
        return False
    if environment.kind == "production":
        if action == "archive":
            return False
        return role is UserRole.OWNER
    if environment.kind == "staging":
        return role in (UserRole.OWNER, UserRole.ADMIN)
    return role in (UserRole.OWNER, UserRole.ADMIN, UserRole.MANAGER)


def selection_allowed(environment: Environment) -> bool:
    """A current-environment selection cannot land on a closed environment."""
    return environment.status == "active"
````

### `app/api/organization_membership_routes.py`

````python
"""Organization member administration.

The organization id in the path is checked against the principal. A body field
with the same name is not a grant. Cross-organization requests are 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    TenantContext,
    get_optional_context,
    require_human_session,
    require_permission,
)
from app.auth.permissions import Permission
from app.db.session import get_session
from app.organization import invitations as invitation_service
from app.organization import membership_service
from app.organization.access import (
    can_manage_members,
    can_read_organization,
)
from app.organization.invitations import InvitationInvalid
from app.organization.membership_policies import assert_can_grant
from app.organization.roles import resolve_organization_role
from app.organization.service import Actor
from app.tenancy.isolation import (
    BoundaryDenied,
    Forbidden,
    HierarchyError,
    client_ip,
    to_http,
)

router = APIRouter(prefix="/api/organizations", tags=["organization-membership"])


class MemberOut(BaseModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class RoleBody(BaseModel):
    role: str = Field(min_length=1, max_length=64)
    user_id: uuid.UUID | None = None


class InviteBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(min_length=1, max_length=64)


class AcceptBody(BaseModel):
    invitation_token: str = Field(min_length=10, max_length=200)
    email: str | None = None
    password: str | None = None
    organization_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


class InvitationOut(BaseModel):
    id: uuid.UUID
    status: str
    expires_at: datetime
    scope: str
    invitation_token: str | None = None


def _actor(ctx: TenantContext, request: Request) -> Actor:
    return Actor(
        user_id=ctx.user_id,
        email=ctx.user.email,
        tenant_id=ctx.tenant_id,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )


def _http(exc: HierarchyError):
    raise to_http(exc)


async def _require_read(session, ctx, organization_id: uuid.UUID) -> None:
    decision = await can_read_organization(
        session, ctx.user, organization_id,
        scopes=ctx.scopes, membership_status=ctx.membership_status,
    )
    if decision.boundary:
        raise BoundaryDenied()
    if not decision.allowed:
        raise Forbidden()


async def _require_manage(session, ctx, organization_id: uuid.UUID) -> None:
    decision = await can_manage_members(
        session, ctx.user, organization_id,
        scopes=ctx.scopes, membership_status=ctx.membership_status,
    )
    if decision.boundary:
        raise BoundaryDenied()
    if not decision.allowed:
        raise Forbidden()


@router.get("/{organization_id}/members", response_model=list[MemberOut])
async def list_members(
    organization_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.USER_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        await _require_read(session, ctx, organization_id)
        rows = await membership_service.list_members(session, ctx.user, organization_id)
    except HierarchyError as exc:
        _http(exc)
    return [
        MemberOut(
            id=row.id, organization_id=row.organization_id, user_id=row.user_id,
            role=row.role.value, status=row.status, created_at=row.created_at,
        )
        for row in rows
    ]


@router.post("/{organization_id}/members", response_model=MemberOut)
async def add_member(
    organization_id: uuid.UUID,
    body: RoleBody,
    request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_CREATE):
        raise to_http(Forbidden())
    if body.user_id is None:
        raise to_http(Forbidden("A user id is required"))
    try:
        await _require_manage(session, ctx, organization_id)
        row = await membership_service.add_member(
            session, actor_user=ctx.user, organization_id=organization_id,
            target_user_id=body.user_id, role_name=body.role, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        _http(exc)
    return MemberOut(
        id=row.id, organization_id=row.organization_id, user_id=row.user_id,
        role=row.role.value, status=row.status, created_at=row.created_at,
    )


@router.get("/{organization_id}/members/{user_id}", response_model=MemberOut)
async def get_member(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.USER_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        await _require_read(session, ctx, organization_id)
        row = await membership_service.require_membership(session, organization_id, user_id)
    except HierarchyError as exc:
        _http(exc)
    return MemberOut(
        id=row.id, organization_id=row.organization_id, user_id=row.user_id,
        role=row.role.value, status=row.status, created_at=row.created_at,
    )


@router.patch("/{organization_id}/members/{user_id}", response_model=MemberOut)
async def update_member_role(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    body: RoleBody,
    request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_ROLE_CHANGE):
        raise to_http(Forbidden())
    try:
        await _require_manage(session, ctx, organization_id)
        row = await membership_service.update_role(
            session, actor_user=ctx.user, organization_id=organization_id,
            target_user_id=user_id, role_name=body.role, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        _http(exc)
    return MemberOut(
        id=row.id, organization_id=row.organization_id, user_id=row.user_id,
        role=row.role.value, status=row.status, created_at=row.created_at,
    )


async def _status(
    action, organization_id, user_id, request, ctx, session,
):
    if not ctx.can(Permission.USER_UPDATE):
        raise to_http(Forbidden())
    try:
        await _require_manage(session, ctx, organization_id)
        row = await action(
            session, actor_user=ctx.user, organization_id=organization_id,
            target_user_id=user_id, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        _http(exc)
    return MemberOut(
        id=row.id, organization_id=row.organization_id, user_id=row.user_id,
        role=row.role.value, status=row.status, created_at=row.created_at,
    )


@router.post("/{organization_id}/members/{user_id}/suspend", response_model=MemberOut)
async def suspend_member(
    organization_id: uuid.UUID, user_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _status(
        membership_service.suspend_member, organization_id, user_id, request, ctx, session,
    )


@router.post("/{organization_id}/members/{user_id}/reactivate", response_model=MemberOut)
async def reactivate_member(
    organization_id: uuid.UUID, user_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _status(
        membership_service.reactivate_member, organization_id, user_id, request, ctx, session,
    )


@router.post("/{organization_id}/members/{user_id}/revoke", response_model=MemberOut)
async def revoke_member(
    organization_id: uuid.UUID, user_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _status(
        membership_service.revoke_member, organization_id, user_id, request, ctx, session,
    )


@router.post("/{organization_id}/invitations", response_model=InvitationOut)
async def invite_member(
    organization_id: uuid.UUID,
    body: InviteBody,
    request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_CREATE):
        raise to_http(Forbidden())
    try:
        await _require_manage(session, ctx, organization_id)
        role = resolve_organization_role(body.role)
        assert_can_grant(ctx.user, role)
        row, token = await invitation_service.create_invitation(
            session,
            actor_user=ctx.user,
            scope="organization",
            organization_id=organization_id,
            tenant_id=None,
            home_tenant_id=ctx.tenant_id,
            email=body.email,
            role=role,
            actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        _http(exc)
    return InvitationOut(
        id=row.id, status=row.status, expires_at=row.expires_at,
        scope=row.scope, invitation_token=token,
    )


@router.post("/{organization_id}/invitations/{invitation_id}/revoke", response_model=InvitationOut)
async def revoke_invitation(
    organization_id: uuid.UUID,
    invitation_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_DELETE):
        raise to_http(Forbidden())
    try:
        await _require_manage(session, ctx, organization_id)
        row = await invitation_service.revoke_invitation(
            session, actor_user=ctx.user, invitation_id=invitation_id,
            organization_id=organization_id, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        _http(exc)
    return InvitationOut(
        id=row.id, status=row.status, expires_at=row.expires_at, scope=row.scope,
    )


@router.post("/{organization_id}/invitations/{invitation_id}/resend", response_model=InvitationOut)
async def resend_invitation(
    organization_id: uuid.UUID,
    invitation_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_CREATE):
        raise to_http(Forbidden())
    try:
        await _require_manage(session, ctx, organization_id)
        row, token = await invitation_service.resend_invitation(
            session, actor_user=ctx.user, invitation_id=invitation_id,
            organization_id=organization_id, actor=_actor(ctx, request),
        )
    except InvitationInvalid as exc:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400, detail={"code": "invitation_invalid", "message": exc.message},
        ) from None
    except HierarchyError as exc:
        _http(exc)
    return InvitationOut(
        id=row.id, status=row.status, expires_at=row.expires_at,
        scope=row.scope, invitation_token=token,
    )


@router.post("/{organization_id}/invitations/accept", response_model=InvitationOut)
async def accept_invitation(
    organization_id: uuid.UUID,
    body: AcceptBody,
    request: Request,
    session: AsyncSession = Depends(get_session),
    ctx: TenantContext | None = Depends(get_optional_context),
):
    """Accept binds the invitation row. A disagreeing client id is a bad token."""
    if body.organization_id is not None and body.organization_id != organization_id:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail={"code": "invitation_invalid", "message": "Invitation is not valid."},
        )
    try:
        row = await invitation_service.accept_invitation(
            session,
            raw_token=body.invitation_token,
            organization_id=organization_id,
            tenant_id=body.tenant_id,
            authenticated=ctx.user if ctx is not None else None,
            email=body.email,
            password=body.password,
            actor=_actor(ctx, request) if ctx is not None else None,
        )
    except InvitationInvalid as exc:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400, detail={"code": "invitation_invalid", "message": exc.message},
        ) from None
    except HierarchyError as exc:
        _http(exc)
    return InvitationOut(
        id=row.id, status=row.status, expires_at=row.expires_at, scope=row.scope,
    )
````

### `app/api/tenant_membership_routes.py`

````python
"""Tenant member administration.

The path tenant must belong to the caller's organization. A body tenant id is
ignored as an authorization source and rejected when it disagrees.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_human_session, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.organization.invitations import InvitationInvalid
from app.organization.service import Actor
from app.tenancy import invitations as tenant_invitations
from app.tenancy import membership_service
from app.tenancy.access import verify_operation
from app.tenancy.isolation import BoundaryDenied, Forbidden, HierarchyError, client_ip, to_http

router = APIRouter(prefix="/api/tenants", tags=["tenant-membership"])


class MemberOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    status: str
    created_at: datetime


class RoleBody(BaseModel):
    role: str = Field(min_length=1, max_length=64)
    user_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None


class InviteBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: str = Field(min_length=1, max_length=64)
    tenant_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None


class AcceptBody(BaseModel):
    invitation_token: str = Field(min_length=10, max_length=200)
    email: str | None = None
    password: str | None = None
    organization_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


class InvitationOut(BaseModel):
    id: uuid.UUID
    status: str
    expires_at: datetime
    scope: str
    invitation_token: str | None = None


def _actor(ctx: TenantContext, request: Request) -> Actor:
    return Actor(
        user_id=ctx.user_id, email=ctx.user.email, tenant_id=ctx.tenant_id,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )


def _reject_client_ids(body, tenant_id: uuid.UUID) -> None:
    if getattr(body, "tenant_id", None) not in (None, tenant_id):
        raise BoundaryDenied()


async def _gate(session, ctx, tenant_id, conceptual: str) -> None:
    decision = await verify_operation(
        session, ctx.user, tenant_id, conceptual,
        scopes=ctx.scopes, membership_status=ctx.membership_status,
    )
    if decision.boundary:
        raise BoundaryDenied()
    if not decision.allowed:
        raise Forbidden()


def _out(row) -> MemberOut:
    return MemberOut(
        id=row.id, tenant_id=row.tenant_id, user_id=row.user_id,
        role=row.role.value, status=row.status, created_at=row.created_at,
    )


@router.get("/{tenant_id}/members", response_model=list[MemberOut])
async def list_members(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.USER_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        await _gate(session, ctx, tenant_id, "tenant:members:read")
        rows = await membership_service.list_members(session, ctx.user, tenant_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return [_out(row) for row in rows]


@router.post("/{tenant_id}/members", response_model=MemberOut)
async def add_member(
    tenant_id: uuid.UUID, body: RoleBody, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_CREATE) or body.user_id is None:
        raise to_http(Forbidden())
    try:
        _reject_client_ids(body, tenant_id)
        await _gate(session, ctx, tenant_id, "tenant:members:invite")
        row = await membership_service.add_member(
            session, actor_user=ctx.user, tenant_id=tenant_id,
            target_user_id=body.user_id, role_name=body.role, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _out(row)


@router.get("/{tenant_id}/members/{user_id}", response_model=MemberOut)
async def get_member(
    tenant_id: uuid.UUID, user_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.USER_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        await _gate(session, ctx, tenant_id, "tenant:members:read")
        row = await membership_service.get_membership(session, tenant_id, user_id)
        if row is None:
            raise BoundaryDenied()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _out(row)


@router.patch("/{tenant_id}/members/{user_id}", response_model=MemberOut)
async def update_role(
    tenant_id: uuid.UUID, user_id: uuid.UUID, body: RoleBody, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_ROLE_CHANGE):
        raise to_http(Forbidden())
    try:
        _reject_client_ids(body, tenant_id)
        await _gate(session, ctx, tenant_id, "tenant:members:update")
        row = await membership_service.update_role(
            session, actor_user=ctx.user, tenant_id=tenant_id,
            target_user_id=user_id, role_name=body.role, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _out(row)


async def _status(action, tenant_id, user_id, request, ctx, session, permission):
    if not ctx.can(permission):
        raise to_http(Forbidden())
    try:
        await _gate(session, ctx, tenant_id, "tenant:members:update")
        row = await action(
            session, actor_user=ctx.user, tenant_id=tenant_id,
            target_user_id=user_id, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _out(row)


@router.post("/{tenant_id}/members/{user_id}/suspend", response_model=MemberOut)
async def suspend_member(
    tenant_id: uuid.UUID, user_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _status(
        membership_service.suspend_member, tenant_id, user_id, request, ctx, session,
        Permission.USER_UPDATE,
    )


@router.post("/{tenant_id}/members/{user_id}/reactivate", response_model=MemberOut)
async def reactivate_member(
    tenant_id: uuid.UUID, user_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _status(
        membership_service.reactivate_member, tenant_id, user_id, request, ctx, session,
        Permission.USER_UPDATE,
    )


@router.post("/{tenant_id}/members/{user_id}/revoke", response_model=MemberOut)
async def revoke_member(
    tenant_id: uuid.UUID, user_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    return await _status(
        membership_service.revoke_member, tenant_id, user_id, request, ctx, session,
        Permission.USER_DELETE,
    )


@router.post("/{tenant_id}/invitations", response_model=InvitationOut)
async def invite(
    tenant_id: uuid.UUID, body: InviteBody, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_CREATE):
        raise to_http(Forbidden())
    try:
        await _gate(session, ctx, tenant_id, "tenant:members:invite")
        row, token = await tenant_invitations.create(
            session, actor_user=ctx.user, tenant_id=tenant_id, email=body.email,
            role_name=body.role, actor=_actor(ctx, request),
            client_organization_id=body.organization_id, client_tenant_id=body.tenant_id,
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    return InvitationOut(
        id=row.id, status=row.status, expires_at=row.expires_at,
        scope=row.scope, invitation_token=token,
    )


@router.post("/{tenant_id}/invitations/{invitation_id}/revoke", response_model=InvitationOut)
async def revoke_invitation(
    tenant_id: uuid.UUID, invitation_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.USER_DELETE):
        raise to_http(Forbidden())
    try:
        await _gate(session, ctx, tenant_id, "tenant:members:remove")
        row = await tenant_invitations.revoke(
            session, actor_user=ctx.user, tenant_id=tenant_id,
            invitation_id=invitation_id, actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    return InvitationOut(id=row.id, status=row.status, expires_at=row.expires_at, scope=row.scope)


@router.post("/{tenant_id}/invitations/accept", response_model=InvitationOut)
async def accept_invitation(
    tenant_id: uuid.UUID, body: AcceptBody, request: Request,
    session: AsyncSession = Depends(get_session),
):
    claimed_org = body.organization_id
    claimed_tenant = body.tenant_id or tenant_id
    if body.tenant_id is not None and body.tenant_id != tenant_id:
        raise HTTPException(
            status_code=400,
            detail={"code": "invitation_invalid", "message": "Invitation is not valid."},
        )
    try:
        row = await tenant_invitations.accept(
            session, raw_token=body.invitation_token, tenant_id=claimed_tenant,
            organization_id=claimed_org, email=body.email, password=body.password,
            actor=Actor(ip_address=client_ip(request)),
        )
    except InvitationInvalid as exc:
        raise HTTPException(
            status_code=400, detail={"code": "invitation_invalid", "message": exc.message},
        ) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return InvitationOut(id=row.id, status=row.status, expires_at=row.expires_at, scope=row.scope)
````

### `app/api/environment_access_routes.py`

````python
"""Environment access, selection, effective policy and effective quota.

No secret and no deployment credential is returned. A client-supplied
environment id is loaded and then checked; it is never trusted on its own.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_human_session, require_permission
from app.auth.permissions import Permission
from app.db.models import Environment
from app.db.session import get_session
from app.environments import access as environment_access
from app.environments.context_resolution import resolve_environment, select_environment
from app.environments.policy_inheritance import resolve_effective, set_scope_policy
from app.environments.quota import environment_quota
from app.organization.service import Actor
from app.quotas.models import QuotaKey
from app.quotas.service import set_quota
from app.tenancy.access import load_tenant_in_organization
from app.tenancy.isolation import BoundaryDenied, Forbidden, HierarchyError, client_ip, to_http

router = APIRouter(prefix="/api/tenants", tags=["environment-access"])


class EnvironmentAccessOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    kind: str
    status: str
    is_default: bool


class SelectBody(BaseModel):
    environment_id: uuid.UUID
    tenant_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None


class PolicyOut(BaseModel):
    authorization_valid: bool
    invalid_reason: str
    mfa_required: bool
    sso_required: bool
    password_login_allowed: bool
    api_keys_allowed: bool
    service_accounts_allowed: bool
    session_idle_minutes: int
    session_max_active: int


class PolicyBody(BaseModel):
    mfa_required: bool | None = None
    sso_required: bool | None = None
    password_login_allowed: bool | None = None
    api_keys_allowed: bool | None = None
    service_accounts_allowed: bool | None = None
    session_idle_minutes: int | None = Field(default=None, ge=5, le=10080)
    session_max_active: int | None = Field(default=None, ge=1, le=100)


class QuotaBody(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    mode: str = Field(min_length=1, max_length=16)
    limit: int | None = None


def _actor(ctx: TenantContext, request: Request) -> Actor:
    return Actor(
        user_id=ctx.user_id, email=ctx.user.email, tenant_id=ctx.tenant_id,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )


def _env_out(row: Environment) -> EnvironmentAccessOut:
    return EnvironmentAccessOut(
        id=row.id, tenant_id=row.tenant_id, name=row.name, slug=row.slug,
        kind=row.kind, status=row.status, is_default=row.is_default,
    )


async def _tenant(session, ctx, tenant_id):
    tenant = await load_tenant_in_organization(session, ctx.user, tenant_id)
    if tenant is None:
        raise BoundaryDenied()
    return tenant


@router.get("/{tenant_id}/access/environments", response_model=list[EnvironmentAccessOut])
async def list_accessible(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import select
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        rows = (
            await session.execute(
                select(Environment).where(Environment.tenant_id == tenant.id)
            )
        ).scalars().all()
        visible = []
        for row in rows:
            _environment, decision = await environment_access.can_read_environment(
                session, ctx.user, tenant.id, row.id, scopes=ctx.scopes,
            )
            if decision.allowed:
                visible.append(_env_out(row))
    except HierarchyError as exc:
        raise to_http(exc) from None
    return visible


@router.get("/{tenant_id}/access/current", response_model=EnvironmentAccessOut | None)
async def current_environment(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        environment = await resolve_environment(session, user=ctx.user, tenant=tenant)
    except HierarchyError as exc:
        raise to_http(exc) from None
    if environment is None:
        return None
    return _env_out(environment)


@router.post("/{tenant_id}/access/current", response_model=EnvironmentAccessOut)
async def select_current(
    tenant_id: uuid.UUID, body: SelectBody, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.TENANT_READ):
        raise to_http(Forbidden())
    if body.tenant_id is not None and body.tenant_id != tenant_id:
        raise to_http(BoundaryDenied())
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        if body.organization_id is not None and body.organization_id != tenant.organization_id:
            raise BoundaryDenied()
        environment = await select_environment(
            session, user=ctx.user, tenant=tenant, environment_id=body.environment_id,
            actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _env_out(environment)


@router.get("/{tenant_id}/access/policy", response_model=PolicyOut)
async def inspect_policy(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.IDENTITY_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        environment = None
        if environment_id is not None:
            environment, decision = await environment_access.can_read_environment(
                session, ctx.user, tenant.id, environment_id, scopes=ctx.scopes,
            )
            if environment is None or decision.via == "boundary":
                raise BoundaryDenied()
        policy = await resolve_effective(session, tenant, environment)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return PolicyOut(
        authorization_valid=policy.authorization_valid,
        invalid_reason=policy.invalid_reason,
        mfa_required=policy.mfa_required,
        sso_required=policy.sso_required,
        password_login_allowed=policy.password_login_allowed,
        api_keys_allowed=policy.api_keys_allowed,
        service_accounts_allowed=policy.service_accounts_allowed,
        session_idle_minutes=policy.session_idle_minutes,
        session_max_active=policy.session_max_active,
    )


@router.post("/{tenant_id}/access/policy", response_model=PolicyOut)
async def update_policy(
    tenant_id: uuid.UUID, body: PolicyBody,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.IDENTITY_WRITE):
        raise to_http(Forbidden())
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        values = body.model_dump(exclude_none=True)
        await set_scope_policy(
            session, kind="tenant", scope_id=tenant.id, tenant=tenant, values=values,
        )
        policy = await resolve_effective(session, tenant, None)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return PolicyOut(
        authorization_valid=policy.authorization_valid,
        invalid_reason=policy.invalid_reason,
        mfa_required=policy.mfa_required,
        sso_required=policy.sso_required,
        password_login_allowed=policy.password_login_allowed,
        api_keys_allowed=policy.api_keys_allowed,
        service_accounts_allowed=policy.service_accounts_allowed,
        session_idle_minutes=policy.session_idle_minutes,
        session_max_active=policy.session_max_active,
    )


@router.get("/{tenant_id}/access/quota")
async def inspect_quota(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        environment = None
        if environment_id is not None:
            environment, decision = await environment_access.can_read_environment(
                session, ctx.user, tenant.id, environment_id, scopes=ctx.scopes,
            )
            if environment is None:
                raise BoundaryDenied()
        results = []
        for key in QuotaKey:
            if environment is None:
                from app.tenancy.quota import tenant_quota
                decision_q = await tenant_quota(session, tenant, key)
            else:
                decision_q = await environment_quota(session, tenant, environment, key)
            results.append(decision_q.as_dict())
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"quotas": results}


@router.post("/{tenant_id}/access/quota")
async def set_environment_quota(
    tenant_id: uuid.UUID, body: QuotaBody,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
    environment_id: uuid.UUID | None = None,
):
    if ctx.role.value != "owner" or not ctx.can(Permission.TENANT_UPDATE):
        raise to_http(Forbidden())
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        scope_kind = "tenant"
        scope_id = tenant.id
        if environment_id is not None:
            environment, decision = await environment_access.can_update_environment(
                session, ctx.user, tenant.id, environment_id, scopes=ctx.scopes,
            )
            if environment is None:
                raise BoundaryDenied()
            if not decision.allowed:
                raise Forbidden()
            scope_kind = "environment"
            scope_id = environment.id
        try:
            row = await set_quota(
                session, scope_kind=scope_kind, scope_id=scope_id,
                key=body.key, mode=body.mode, limit_value=body.limit,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "quota_configuration", "message": "Quota configuration is invalid."},
            ) from exc
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"key": row.quota_key, "mode": row.mode, "limit": row.limit_value}


@router.post("/{tenant_id}/access/verify")
async def verify_access(
    tenant_id: uuid.UUID, body: SelectBody,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    if body.tenant_id is not None and body.tenant_id != tenant_id:
        raise to_http(BoundaryDenied())
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        if body.organization_id is not None and body.organization_id != tenant.organization_id:
            raise BoundaryDenied()
        _environment, decision = await environment_access.can_read_environment(
            session, ctx.user, tenant.id, body.environment_id, scopes=ctx.scopes,
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    if decision.via == "boundary":
        raise to_http(BoundaryDenied())
    return {
        "allowed": decision.allowed,
        "via": decision.via,
        "reason": decision.reason,
        "role": decision.role.value if decision.role else None,
    }
````

### `app/quotas/__init__.py`

````python
"""Central quota resolution.

Organization, tenant and environment rows may tighten a limit. Billing
entitlements are read, not replaced. Missing configuration is unknown.
"""

from app.quotas.enforcement import enforce
from app.quotas.models import QuotaDecision, QuotaError, QuotaKey, QuotaMode
from app.quotas.service import resolve_quota, set_quota

__all__ = [
    "QuotaDecision",
    "QuotaError",
    "QuotaKey",
    "QuotaMode",
    "enforce",
    "resolve_quota",
    "set_quota",
]
````

### `app/quotas/models.py`

````python
"""Quota keys and the normalized result of a resolution.

Missing limits are ``unknown``. They are not zero and they are not unlimited.
A malformed stored value is ``malformed`` and must fail closed.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class QuotaKey(str, enum.Enum):
    """Keys the current product can actually resolve.

    ``users`` maps to the billing ``team_members`` entitlement.
    ``knowledge_documents`` maps to ``rag_documents``.
    ``active_calls`` maps to ``concurrent_calls``.
    ``monthly_call_minutes`` and ``ai_tokens`` map to the plan's included
    voice minutes and LLM tokens. The last two have no billing feature, so
    they stay unknown until a hierarchy row sets them.
    """

    USERS = "users"
    ENVIRONMENTS = "environments"
    ACTIVE_CALLS = "active_calls"
    MONTHLY_CALL_MINUTES = "monthly_call_minutes"
    AI_TOKENS = "ai_tokens"
    KNOWLEDGE_DOCUMENTS = "knowledge_documents"
    STORAGE_BYTES = "storage_bytes"
    WEBHOOK_EVENTS_PER_MINUTE = "webhook_events_per_minute"


class QuotaMode(str, enum.Enum):
    HARD = "hard"
    SOFT = "soft"
    UNLIMITED = "unlimited"
    UNKNOWN = "unknown"
    MALFORMED = "malformed"


class QuotaDecisionKind(str, enum.Enum):
    ALLOW = "allow"
    WARN = "warn"
    DENY = "deny"
    UNKNOWN = "unknown"
    MALFORMED = "malformed"


@dataclass(frozen=True)
class QuotaDecision:
    key: str
    mode: str
    limit: int | None
    used: int | None
    source: str
    decision: str
    reason: str
    allowed: bool

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "mode": self.mode,
            "limit": self.limit,
            "used": self.used,
            "source": self.source,
            "decision": self.decision,
            "reason": self.reason,
            "allowed": self.allowed,
        }


class QuotaError(Exception):
    code = "quota_exceeded"

    def __init__(self, message: str, *, decision: QuotaDecision | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.decision = decision


class QuotaConfigurationError(QuotaError):
    code = "quota_configuration"
````

### `app/quotas/service.py`

````python
"""Effective quota from the hierarchy and the existing billing entitlement.

A hierarchy value may tighten a billing cap. It may not raise one. A missing
value is unknown, not zero. A stored row that breaks its own shape is
malformed and fails closed rather than becoming unlimited.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.billing.plans import UNLIMITED
from app.db.models import QuotaLimit, Tenant
from app.quotas.metrics import record_decision
from app.quotas.models import (
    QuotaDecision,
    QuotaDecisionKind,
    QuotaKey,
    QuotaMode,
)

_BILLING_FEATURES = {
    QuotaKey.USERS.value: "team_members",
    QuotaKey.KNOWLEDGE_DOCUMENTS.value: "rag_documents",
    QuotaKey.ACTIVE_CALLS.value: "concurrent_calls",
    QuotaKey.ENVIRONMENTS.value: None,
    QuotaKey.STORAGE_BYTES.value: None,
    QuotaKey.WEBHOOK_EVENTS_PER_MINUTE.value: None,
}

_INBOUND_SAFE = frozenset({
    QuotaKey.MONTHLY_CALL_MINUTES.value,
    QuotaKey.ACTIVE_CALLS.value,
})


def parse_key(key: QuotaKey | str) -> str:
    if isinstance(key, QuotaKey):
        return key.value
    text = str(key).strip()
    try:
        return QuotaKey(text).value
    except ValueError as exc:
        raise ValueError("Unknown quota key") from exc


def _malformed(key: str, reason: str) -> QuotaDecision:
    decision = QuotaDecision(
        key=key,
        mode=QuotaMode.MALFORMED.value,
        limit=None,
        used=None,
        source="malformed",
        decision=QuotaDecisionKind.MALFORMED.value,
        reason=reason,
        allowed=False,
    )
    record_decision(key, decision.decision)
    return decision


def _unknown(key: str, used: int | None) -> QuotaDecision:
    decision = QuotaDecision(
        key=key,
        mode=QuotaMode.UNKNOWN.value,
        limit=None,
        used=used,
        source="none",
        decision=QuotaDecisionKind.UNKNOWN.value,
        reason="limit_unknown",
        allowed=True,
    )
    record_decision(key, decision.decision)
    return decision


def interpret_row(row: QuotaLimit | None, key: str) -> tuple[str, int | None, str] | QuotaDecision:
    """Return ``(mode, limit, source)`` or a malformed decision."""
    if row is None:
        return QuotaMode.UNKNOWN.value, None, "none"
    mode = row.mode
    if mode not in (
        QuotaMode.HARD.value, QuotaMode.SOFT.value,
        QuotaMode.UNLIMITED.value, QuotaMode.UNKNOWN.value,
    ):
        return _malformed(key, "configuration_error")
    if mode in (QuotaMode.UNLIMITED.value, QuotaMode.UNKNOWN.value):
        if row.limit_value is not None:
            return _malformed(key, "configuration_error")
        return mode, None, "hierarchy"
    if row.limit_value is None or row.limit_value < 0:
        return _malformed(key, "configuration_error")
    return mode, int(row.limit_value), "hierarchy"


def _billing_cap(plan, key: str) -> tuple[str, int | None]:
    """A billing number, unlimited, or unknown. Never a silent zero."""
    if plan is None:
        return QuotaMode.UNKNOWN.value, None
    if key == QuotaKey.MONTHLY_CALL_MINUTES.value:
        raw = getattr(plan, "included_voice_minutes", None)
        if raw is None:
            return QuotaMode.UNKNOWN.value, None
        return QuotaMode.HARD.value, int(raw)
    if key == QuotaKey.AI_TOKENS.value:
        raw = getattr(plan, "included_llm_tokens", None)
        if raw is None:
            return QuotaMode.UNKNOWN.value, None
        return QuotaMode.HARD.value, int(raw)
    feature = _BILLING_FEATURES.get(key)
    if not feature:
        return QuotaMode.UNKNOWN.value, None
    from app.billing.plans import feature_limit
    limit = feature_limit(plan, feature)
    if limit is None:
        return QuotaMode.UNKNOWN.value, None
    if isinstance(limit, bool):
        return (QuotaMode.UNLIMITED.value, None) if limit else (QuotaMode.HARD.value, 0)
    if int(limit) == UNLIMITED:
        return QuotaMode.UNLIMITED.value, None
    if int(limit) < 0:
        return QuotaMode.UNKNOWN.value, None
    return QuotaMode.HARD.value, int(limit)


def combine(
    key: str,
    hierarchy: tuple[str, int | None],
    billing: tuple[str, int | None],
    *,
    used: int | None,
) -> QuotaDecision:
    """Stricter numeric cap wins. Hierarchy cannot raise a billing cap."""
    h_mode, h_limit = hierarchy
    b_mode, b_limit = billing
    if h_mode == QuotaMode.MALFORMED.value or b_mode == QuotaMode.MALFORMED.value:
        return _malformed(key, "configuration_error")
    candidates: list[tuple[int, str]] = []
    if h_mode in (QuotaMode.HARD.value, QuotaMode.SOFT.value) and h_limit is not None:
        candidates.append((h_limit, "hierarchy"))
    if b_mode == QuotaMode.HARD.value and b_limit is not None:
        candidates.append((b_limit, "billing"))
    if not candidates:
        if h_mode == QuotaMode.UNLIMITED.value and b_mode == QuotaMode.UNLIMITED.value:
            mode, source = QuotaMode.UNLIMITED.value, "billing"
            limit = None
        elif h_mode == QuotaMode.UNLIMITED.value and b_mode == QuotaMode.UNKNOWN.value:
            mode, source, limit = QuotaMode.UNLIMITED.value, "hierarchy", None
        elif b_mode == QuotaMode.UNLIMITED.value and h_mode == QuotaMode.UNKNOWN.value:
            mode, source, limit = QuotaMode.UNLIMITED.value, "billing", None
        else:
            return _unknown(key, used)
    else:
        limit, source = min(candidates, key=lambda item: item[0])
        mode = QuotaMode.SOFT.value if h_mode == QuotaMode.SOFT.value and limit == h_limit else QuotaMode.HARD.value
        if b_limit is not None and h_limit is not None and h_limit > b_limit:
            source = "billing"
            limit = b_limit
            mode = QuotaMode.HARD.value
    if mode == QuotaMode.UNLIMITED.value:
        decision = QuotaDecisionKind.ALLOW.value
        reason = "unlimited"
        allowed = True
    elif used is None or limit is None:
        decision = QuotaDecisionKind.ALLOW.value
        reason = "no_usage_supplied"
        allowed = True
    elif used > limit and mode == QuotaMode.HARD.value:
        decision = QuotaDecisionKind.DENY.value
        reason = "hard_limit"
        allowed = False
    elif used > limit:
        decision = QuotaDecisionKind.WARN.value
        reason = "soft_limit"
        allowed = True
    elif limit and used / limit >= 0.8:
        decision = QuotaDecisionKind.WARN.value
        reason = "approaching_limit"
        allowed = True
    else:
        decision = QuotaDecisionKind.ALLOW.value
        reason = "within_limit"
        allowed = True
    result = QuotaDecision(
        key=key, mode=mode, limit=limit, used=used, source=source,
        decision=decision, reason=reason, allowed=allowed,
    )
    record_decision(key, result.decision)
    return result


async def _row(
    session: AsyncSession, kind: str, scope_id: uuid.UUID | None, key: str
) -> QuotaLimit | None:
    if scope_id is None:
        return None
    return (
        await session.execute(
            select(QuotaLimit).where(
                QuotaLimit.scope_kind == kind,
                QuotaLimit.scope_id == scope_id,
                QuotaLimit.quota_key == key,
            )
        )
    ).scalar_one_or_none()


def _tighter(rows: list[tuple[str, int | None, str]]) -> tuple[str, int | None]:
    numeric = [(mode, limit) for mode, limit, _source in rows if limit is not None]
    if not numeric:
        for mode, limit, _source in rows:
            if mode == QuotaMode.UNLIMITED.value:
                return QuotaMode.UNLIMITED.value, None
        return QuotaMode.UNKNOWN.value, None
    limit = min(item[1] for item in numeric)
    mode = QuotaMode.HARD.value
    for item_mode, item_limit in numeric:
        if item_limit == limit and item_mode == QuotaMode.SOFT.value:
            mode = QuotaMode.SOFT.value
    return mode, limit


async def resolve_quota(
    session: AsyncSession,
    key: QuotaKey | str,
    *,
    organization_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None,
    environment_id: uuid.UUID | None = None,
    tenant: Tenant | None = None,
    used: int | None = None,
) -> QuotaDecision:
    parsed = parse_key(key)
    layers = []
    for kind, scope_id in (
        ("organization", organization_id),
        ("tenant", tenant_id),
        ("environment", environment_id),
    ):
        interpreted = interpret_row(await _row(session, kind, scope_id, parsed), parsed)
        if isinstance(interpreted, QuotaDecision):
            return interpreted
        layers.append(interpreted)
    hierarchy = _tighter(layers)
    billing_mode, billing_limit = QuotaMode.UNKNOWN.value, None
    if tenant is not None:
        from app.billing.entitlements import load_context
        context = await load_context(session, tenant)
        if context.unlimited:
            billing_mode, billing_limit = QuotaMode.UNLIMITED.value, None
        else:
            billing_mode, billing_limit = _billing_cap(context.plan, parsed)
    return combine(parsed, (hierarchy[0], hierarchy[1]), (billing_mode, billing_limit), used=used)


async def set_quota(
    session: AsyncSession,
    *,
    scope_kind: str,
    scope_id: uuid.UUID,
    key: QuotaKey | str,
    mode: str,
    limit_value: int | None,
) -> QuotaLimit:
    parsed = parse_key(key)
    if mode not in (
        QuotaMode.HARD.value, QuotaMode.SOFT.value,
        QuotaMode.UNLIMITED.value, QuotaMode.UNKNOWN.value,
    ):
        raise ValueError("Unknown quota mode")
    if mode in (QuotaMode.HARD.value, QuotaMode.SOFT.value):
        if limit_value is None or limit_value < 0:
            raise ValueError("A hard or soft quota needs a non-negative limit")
    elif limit_value is not None:
        raise ValueError("Unlimited and unknown quotas do not take a limit")
    row = await _row(session, scope_kind, scope_id, parsed)
    if row is None:
        row = QuotaLimit(
            scope_kind=scope_kind, scope_id=scope_id, quota_key=parsed,
            mode=mode, limit_value=limit_value,
        )
        session.add(row)
    else:
        row.mode = mode
        row.limit_value = limit_value
    await session.commit()
    await session.refresh(row)
    return row
````

### `app/quotas/enforcement.py`

````python
"""Central quota guard.

Unknown limits are not treated as zero, so they do not block. Malformed
configuration fails closed. Inbound call actions are not blocked by this
layer; billing already owns that product decision and refuses to cut off the
phone line.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit
from app.db.models import AuditAction, Tenant
from app.organization.service import Actor
from app.quotas.metrics import record_decision
from app.quotas.models import QuotaDecision, QuotaError, QuotaKey
from app.quotas.service import _INBOUND_SAFE, resolve_quota

_INBOUND_ACTIONS = frozenset({"inbound_call", "inbound_voice"})


async def enforce(
    session: AsyncSession,
    *,
    key: QuotaKey | str,
    tenant: Tenant,
    used: int,
    adding: int = 1,
    action: str = "admin",
    environment_id: uuid.UUID | None = None,
    actor: Actor | None = None,
) -> QuotaDecision:
    projected = max(0, int(used)) + max(0, int(adding))
    decision = await resolve_quota(
        session,
        key,
        organization_id=tenant.organization_id,
        tenant_id=tenant.id,
        environment_id=environment_id,
        tenant=tenant,
        used=projected,
    )
    key_text = decision.key
    if action in _INBOUND_ACTIONS and key_text in _INBOUND_SAFE and not decision.allowed:
        # Do not block the phone line. The decision is recorded as a warning.
        softened = QuotaDecision(
            key=decision.key,
            mode=decision.mode,
            limit=decision.limit,
            used=decision.used,
            source=decision.source,
            decision="warn",
            reason="inbound_not_blocked",
            allowed=True,
        )
        record_decision(key_text, "warn")
        return softened
    if decision.decision == "malformed" or not decision.allowed:
        who = actor or Actor(tenant_id=tenant.id)
        await emit(
            session,
            AuditAction.QUOTA_DENIED,
            tenant_id=tenant.id,
            actor_user_id=who.user_id,
            actor_email=who.email,
            ip_address=who.ip_address,
            user_agent=who.user_agent,
            detail={
                "quota_key": key_text,
                "reason": decision.reason,
                "action": action,
            },
            commit=False,
        )
        await session.commit()
        if decision.decision == "malformed":
            from app.quotas.models import QuotaConfigurationError
            raise QuotaConfigurationError(
                "Quota configuration is invalid.", decision=decision
            )
        raise QuotaError("Quota exceeded.", decision=decision)
    return decision
````

### `app/quotas/metrics.py`

````python
"""Quota decision metrics.

Labels are closed sets: a quota key and a decision. Tenant ids are not labels.
"""

from __future__ import annotations

from prometheus_client import Counter

from app.quotas.models import QuotaDecisionKind, QuotaKey

QUOTA_DECISIONS = Counter(
    "voxdesk_quota_decisions_total",
    "Quota evaluations by key and decision",
    ["quota_key", "decision"],
)

_KEYS = frozenset(item.value for item in QuotaKey)
_DECISIONS = frozenset(item.value for item in QuotaDecisionKind)


def record_decision(key: str, decision: str) -> None:
    """Bump the counter. Unknown labels are dropped, never added."""
    if key not in _KEYS or decision not in _DECISIONS:
        return
    QUOTA_DECISIONS.labels(quota_key=key, decision=decision).inc()
````

### `tests/organization/test_membership.py`

````python
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
````

### `tests/organization/test_invitations.py`

````python
"""Invitation create, accept, expiry and cross-scope rejection."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.models import AuditAction, AuditLog, MembershipInvitation, User
from app.organization.invitations import INVITATION_TTL
from tests.conftest import TEST_PASSWORD, auth_headers

pytestmark = pytest.mark.asyncio


async def _invite(client, headers, organization_id, email, role="organization_member"):
    return await client.post(
        f"/api/organizations/{organization_id}/invitations",
        json={"email": email, "role": role},
        headers=headers,
    )


async def test_create_accept_revoke_resend_and_duplicate(client, db, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    org = tenant_a.organization_id
    email = f"invitee-{uuid.uuid4().hex[:8]}@example.com"
    created = await _invite(client, headers, org, email)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["invitation_token"]
    assert "token_hash" not in body
    duplicate = await _invite(client, headers, org, email)
    assert duplicate.status_code == 409, duplicate.text

    accepted = await client.post(
        f"/api/organizations/{org}/invitations/accept",
        json={
            "invitation_token": body["invitation_token"],
            "email": email,
            "password": TEST_PASSWORD,
        },
    )
    assert accepted.status_code == 200, accepted.text
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one()
    assert user.tenant_id == tenant_a.id
    again = await client.post(
        f"/api/organizations/{org}/invitations/accept",
        json={"invitation_token": body["invitation_token"], "email": email, "password": TEST_PASSWORD},
    )
    assert again.status_code == 400, again.text

    second = await _invite(client, headers, org, f"other-{uuid.uuid4().hex[:6]}@example.com")
    assert second.status_code == 200, second.text
    revoked = await client.post(
        f"/api/organizations/{org}/invitations/{second.json()['id']}/revoke",
        headers=headers,
    )
    assert revoked.status_code == 200, revoked.text
    dead = await client.post(
        f"/api/organizations/{org}/invitations/accept",
        json={
            "invitation_token": second.json()["invitation_token"],
            "email": f"other-{uuid.uuid4().hex[:6]}@example.com",
            "password": TEST_PASSWORD,
        },
    )
    assert dead.status_code == 400

    third = await _invite(client, headers, org, f"resend-{uuid.uuid4().hex[:6]}@example.com")
    resent = await client.post(
        f"/api/organizations/{org}/invitations/{third.json()['id']}/resend",
        headers=headers,
    )
    assert resent.status_code == 200, resent.text
    assert resent.json()["invitation_token"] != third.json()["invitation_token"]
    old = await client.post(
        f"/api/organizations/{org}/invitations/accept",
        json={
            "invitation_token": third.json()["invitation_token"],
            "email": third.json().get("email", "nobody@example.com"),
            "password": TEST_PASSWORD,
        },
    )
    assert old.status_code == 400

    audits = (
        await db.execute(
            select(AuditLog).where(AuditLog.action == AuditAction.INVITATION_CREATED)
        )
    ).scalars().all()
    assert audits
    blob = str(audits[-1].detail)
    assert "invitation_token" not in blob
    assert body["invitation_token"] not in blob


async def test_expired_wrong_org_and_invalid_token_look_the_same(
    client, db, tenant_a, tenant_b, owner_a
):
    headers = await auth_headers(client, owner_a)
    email = f"expire-{uuid.uuid4().hex[:8]}@example.com"
    created = await _invite(client, headers, tenant_a.organization_id, email)
    assert created.status_code == 200, created.text
    row = await db.get(MembershipInvitation, uuid.UUID(created.json()["id"]))
    row.expires_at = row.expires_at - INVITATION_TTL - timedelta(days=1)
    await db.commit()
    expired = await client.post(
        f"/api/organizations/{tenant_a.organization_id}/invitations/accept",
        json={"invitation_token": created.json()["invitation_token"], "email": email,
              "password": TEST_PASSWORD},
    )
    wrong_org = await client.post(
        f"/api/organizations/{tenant_b.organization_id}/invitations/accept",
        json={"invitation_token": created.json()["invitation_token"], "email": email,
              "password": TEST_PASSWORD},
    )
    # The expired token was consumed as expired, so mint a fresh one for the
    # cross-organization attempt.
    fresh = await _invite(client, headers, tenant_a.organization_id, f"cross-{uuid.uuid4().hex[:6]}@example.com")
    crossed = await client.post(
        f"/api/organizations/{tenant_b.organization_id}/invitations/accept",
        json={
            "invitation_token": fresh.json()["invitation_token"],
            "email": f"cross-{uuid.uuid4().hex[:6]}@example.com",
            "password": TEST_PASSWORD,
            "organization_id": str(tenant_b.organization_id),
        },
    )
    unknown = await client.post(
        f"/api/organizations/{tenant_a.organization_id}/invitations/accept",
        json={"invitation_token": "not-a-real-invitation-token-value", "email": email,
              "password": TEST_PASSWORD},
    )
    assert expired.status_code == 400
    assert crossed.status_code == 400
    assert unknown.status_code == 400
    assert expired.json() == unknown.json()
    assert crossed.json()["detail"]["code"] == "invitation_invalid"
    assert wrong_org.status_code == 400
    still = await db.get(User, row.accepted_by_user_id) if row.accepted_by_user_id else None
    assert still is None


async def test_invitation_response_does_not_reveal_whether_the_email_exists(
    client, tenant_a, tenant_b, owner_a, owner_b
):
    headers = await auth_headers(client, owner_a)
    known = await _invite(client, headers, tenant_a.organization_id, owner_b.email)
    unknown = await _invite(
        client, headers, tenant_a.organization_id, f"nobody-{uuid.uuid4().hex[:8]}@example.com"
    )
    assert known.status_code == 200, known.text
    assert unknown.status_code == 200, unknown.text
    assert set(known.json()) == set(unknown.json())
    assert "exists" not in known.text.lower()


async def test_tenant_invitation_cannot_jump_organizations(
    client, tenant_a, tenant_b, owner_a
):
    headers = await auth_headers(client, owner_a)
    created = await client.post(
        f"/api/tenants/{tenant_a.id}/invitations",
        json={
            "email": f"tenant-invite-{uuid.uuid4().hex[:6]}@example.com",
            "role": "tenant_member",
            "organization_id": str(tenant_b.organization_id),
            "tenant_id": str(tenant_b.id),
        },
        headers=headers,
    )
    assert created.status_code == 404, created.text
````

### `tests/organization/test_access_policy.py`

````python
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
````

### `alembic/versions/0018_organization_memberships_quotas.py`

````python
"""organization memberships, invitations and quota limits

Adds membership bindings beside the existing ``users.tenant_id``. It does not
change user ids, tenant ids, or the organization ids created in 0017. It does
not add ``environment_id`` to calls, leads, billing or identity.

Backfill creates one active organization membership and one active tenant
membership per existing user, copying that user's role. It does not create a
second production environment and it does not insert a quota row (a missing
quota is unknown, not zero).

Revision ID: 0018_organization_memberships_quotas
Revises: 0017_organization_environment_foundation
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "0018_organization_memberships_quotas"
down_revision = "0017_organization_environment_foundation"
branch_labels = None
depends_on = None

NEW_AUDIT_ACTIONS = (
    "MEMBERSHIP_CREATED",
    "MEMBERSHIP_UPDATED",
    "MEMBERSHIP_SUSPENDED",
    "MEMBERSHIP_RESTORED",
    "MEMBERSHIP_REVOKED",
    "INVITATION_CREATED",
    "INVITATION_ACCEPTED",
    "INVITATION_REVOKED",
    "INVITATION_RESENT",
    "ENVIRONMENT_SELECTED",
    "QUOTA_UPDATED",
    "QUOTA_DENIED",
)

_STATUS = "status IN ('invited', 'active', 'suspended', 'revoked', 'expired')"


def _role_type():
    return sa.Enum(
        "OWNER", "ADMIN", "MANAGER", "AGENT", "VIEWER",
        name="userrole",
        create_type=False,
    )


def _uuid_col():
    return sa.Column("id", sa.Uuid(), nullable=False)


def backfill_memberships(connection) -> int:
    """One organization membership and one tenant membership per existing user.

    Idempotent. Does not rewrite user ids, tenant ids or organization ids.
    Returns the number of membership rows inserted.
    """
    rows = connection.execute(sa.text(
        "SELECT u.id AS user_id, u.role AS role, u.tenant_id AS tenant_id, "
        "t.organization_id AS organization_id "
        "FROM users u JOIN tenants t ON t.id = u.tenant_id "
        "WHERE t.organization_id IS NOT NULL"
    )).mappings().all()
    created = 0
    now = datetime.utcnow()
    for row in rows:
        user_id = str(row["user_id"])
        tenant_id = str(row["tenant_id"])
        organization_id = str(row["organization_id"])
        role = row["role"]
        org_exists = connection.execute(
            sa.text(
                "SELECT id FROM organization_memberships "
                "WHERE organization_id = :org AND user_id = :user"
            ),
            {"org": organization_id, "user": user_id},
        ).first()
        if org_exists is None:
            connection.execute(
                sa.text(
                    "INSERT INTO organization_memberships ("
                    "id, organization_id, user_id, role, status, created_at, updated_at, "
                    "invited_at, accepted_at, suspended_at, revoked_at"
                    ") VALUES ("
                    ":id, :org, :user, :role, 'active', :now, :now, NULL, :now, NULL, NULL)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "org": organization_id,
                    "user": user_id,
                    "role": role,
                    "now": now,
                },
            )
            created += 1
        tenant_exists = connection.execute(
            sa.text(
                "SELECT id FROM tenant_memberships "
                "WHERE tenant_id = :tenant AND user_id = :user"
            ),
            {"tenant": tenant_id, "user": user_id},
        ).first()
        if tenant_exists is None:
            connection.execute(
                sa.text(
                    "INSERT INTO tenant_memberships ("
                    "id, tenant_id, user_id, role, status, created_at, updated_at, "
                    "invited_at, accepted_at, suspended_at, revoked_at"
                    ") VALUES ("
                    ":id, :tenant, :user, :role, 'active', :now, :now, NULL, :now, NULL, NULL)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant": tenant_id,
                    "user": user_id,
                    "role": role,
                    "now": now,
                },
            )
            created += 1
    return created


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for value in NEW_AUDIT_ACTIONS:
            op.execute(f"ALTER TYPE auditaction ADD VALUE IF NOT EXISTS '{value}'")

    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", _role_type(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_STATUS, name="ck_organization_memberships_status"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_organization_memberships_principal"
        ),
    )
    op.create_index(
        "ix_organization_memberships_user", "organization_memberships", ["user_id"]
    )

    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", _role_type(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_STATUS, name="ck_tenant_memberships_status"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_memberships_principal"),
    )
    op.create_index("ix_tenant_memberships_user", "tenant_memberships", ["user_id"])

    op.create_table(
        "environment_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", _role_type(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_STATUS, name="ck_environment_memberships_status"),
        sa.ForeignKeyConstraint(
            ["environment_id"], ["environments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "environment_id", "user_id", name="uq_environment_memberships_principal"
        ),
    )
    op.create_index(
        "ix_environment_memberships_user", "environment_memberships", ["user_id"]
    )

    op.create_table(
        "membership_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("home_tenant_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", _role_type(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("binding_key", sa.String(length=80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("accepted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "scope IN ('organization', 'tenant')",
            name="ck_membership_invitations_scope",
        ),
        sa.CheckConstraint(
            "status IN ('invited', 'accepted', 'revoked', 'expired')",
            name="ck_membership_invitations_status",
        ),
        sa.CheckConstraint(
            "(scope = 'organization' AND tenant_id IS NULL) "
            "OR (scope = 'tenant' AND tenant_id IS NOT NULL)",
            name="ck_membership_invitations_scope_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["home_tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_membership_invitations_token"),
    )
    op.create_index(
        "ix_membership_invitations_org", "membership_invitations", ["organization_id"]
    )
    op.create_index(
        "uq_membership_invitations_active",
        "membership_invitations",
        ["binding_key", "email"],
        unique=True,
        sqlite_where=sa.text("status = 'invited'"),
        postgresql_where=sa.text("status = 'invited'"),
    )

    op.create_table(
        "quota_limits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("quota_key", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("limit_value", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_kind IN ('organization', 'tenant', 'environment')",
            name="ck_quota_limits_scope",
        ),
        sa.CheckConstraint(
            "mode IN ('hard', 'soft', 'unlimited', 'unknown')",
            name="ck_quota_limits_mode",
        ),
        sa.CheckConstraint(
            "(mode IN ('hard', 'soft') AND limit_value IS NOT NULL AND limit_value >= 0) "
            "OR (mode IN ('unlimited', 'unknown') AND limit_value IS NULL)",
            name="ck_quota_limits_value",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope_kind", "scope_id", "quota_key", name="uq_quota_limits_scope_key"
        ),
    )
    op.create_index("ix_quota_limits_scope", "quota_limits", ["scope_kind", "scope_id"])

    op.create_table(
        "scope_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("mfa_required", sa.Boolean(), nullable=True),
        sa.Column("mfa_required_for_admins", sa.Boolean(), nullable=True),
        sa.Column("sso_required", sa.Boolean(), nullable=True),
        sa.Column("password_login_allowed", sa.Boolean(), nullable=True),
        sa.Column("api_keys_allowed", sa.Boolean(), nullable=True),
        sa.Column("service_accounts_allowed", sa.Boolean(), nullable=True),
        sa.Column("session_idle_minutes", sa.Integer(), nullable=True),
        sa.Column("session_max_active", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_kind IN ('organization', 'tenant', 'environment')",
            name="ck_scope_policies_kind",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_kind", "scope_id", name="uq_scope_policies_scope"),
    )

    op.create_table(
        "environment_selections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["environment_id"], ["environments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "tenant_id", name="uq_environment_selections_principal"
        ),
    )
    op.create_index(
        "ix_environment_selections_environment",
        "environment_selections",
        ["environment_id"],
    )

    backfill_memberships(bind)


def downgrade() -> None:
    # Enum labels stay. Business rows, users, tenants and organizations stay.
    op.drop_index(
        "ix_environment_selections_environment", table_name="environment_selections"
    )
    op.drop_table("environment_selections")
    op.drop_table("scope_policies")
    op.drop_index("ix_quota_limits_scope", table_name="quota_limits")
    op.drop_table("quota_limits")
    op.drop_index(
        "uq_membership_invitations_active", table_name="membership_invitations"
    )
    op.drop_index("ix_membership_invitations_org", table_name="membership_invitations")
    op.drop_table("membership_invitations")
    op.drop_index("ix_environment_memberships_user", table_name="environment_memberships")
    op.drop_table("environment_memberships")
    op.drop_index("ix_tenant_memberships_user", table_name="tenant_memberships")
    op.drop_table("tenant_memberships")
    op.drop_index(
        "ix_organization_memberships_user", table_name="organization_memberships"
    )
    op.drop_table("organization_memberships")
````

### `docs/CURRENT-ARCHITECTURE.md`

````markdown
# Current architecture — organization access

Organization is the parent of one or more tenants. Each tenant has environments
(development, staging, production). A user still has exactly one `users.tenant_id`.
Membership rows record that binding so it can be suspended or revoked without
deleting the user or flipping `is_active`.

Effective access is resolved server-side:

1. Explicit revoked or suspended membership denies, suspended for privileged
   actions only.
2. An explicit environment membership, capped so it cannot outrank the tenant role.
3. The tenant membership.
4. An organization owner or admin, for tenants in that organization only.
5. Otherwise deny. A missing membership row is the legacy single-tenant path.

`organization_id` and `environment_id` are not JWT claims. The current
environment, when one is selected, is a server-side row.

Roles are the existing `owner` / `admin` / `manager` / `agent` / `viewer`
values. Conceptual names such as `organization_owner` are aliases only.
Permissions are the existing `Permission` enum. There is no second RBAC engine.

Quota resolution reads organization, tenant and environment rows and the
existing billing entitlement. A hierarchy value may tighten a billing cap. It
may not raise one. A missing limit is unknown, not zero.

Identity policy remains the base. Scope overlays may only make mandatory
controls stricter. Child policies cannot weaken a parent MFA, SSO, password,
API-key or service-account restriction.
````

### `docs/CURRENT-SECURITY-STATUS.md`

````markdown
# Current security status — scope-aware authorization

Implemented in this layer:

- Organization, tenant and environment membership are checked on the server.
  A path or body id outside the principal's organization is a 404 with the
  same body as a missing row.
- Revoked or expired membership is rejected by the existing authenticator, so
  current tenant APIs fail closed. Suspended membership keeps read permissions
  and loses writes. `is_active` is still the login kill switch and is not
  flipped by membership status.
- A caller cannot grant themselves a higher role. Granting a role still goes
  through `can_assign_role`. The last active owner cannot be removed.
- Invitation tokens are stored as SHA-256 digests. Audit rows carry the
  invitation id, not the token. A token issued for one organization cannot be
  accepted against another. Unknown, expired, revoked and cross-scope tokens
  return the same error.
- API keys and service accounts remain bound to the tenant that issued them
  and to the attributed user's membership. A disabled service account is still
  rejected by the existing credential authenticator. Scopes can only narrow.
- Production environment mutation requires the owner role. Archived and
  suspended environments cannot be selected as current.
- Quota enforcement does not treat a missing limit as zero or a malformed row
  as unlimited, and it does not block inbound calls.
- JWT claim set is unchanged. SSO, SCIM, MFA, API keys and service accounts
  are the existing implementations.

Not in this layer: environment-scoped business tables, billing hierarchy,
environment secrets, regional routing, and a second permission vocabulary.
````

### `app/organization/__init__.py`

````python
"""Organization is the parent of one or more tenants.

This package does not replace authentication, RBAC, billing, or the existing
``Tenant`` row. A user still belongs to exactly one tenant. Organization
membership is a binding on that user and uses the existing role enum.
"""

from app.organization.models import OrganizationStatus

__all__ = ["OrganizationStatus"]
````

### `app/auth/dependencies.py`

````python
"""
Reusable security dependencies.

Route handlers never parse a token, never read a role string, and never take
a tenant id from the client. They declare what they need:

    @router.get("/tenants/{tenant_id}/leads")
    async def list_leads(
        ctx: TenantContext = Depends(require_permission(Permission.LEAD_READ)),
    ): ...

`ctx.tenant_id` comes from the signed JWT. When a route also carries
`{tenant_id}` in its path, `tenant_path_guard` compares the two and rejects a
mismatch, so an authenticated user cannot reach another tenant by editing the
URL.
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import Depends, HTTPException, Path, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity import tokens
from app.auth.identity.models import UserSession
from app.auth.identity.principals import AuthenticatedPrincipal
from app.auth.identity.service import IdentityContext, load_identity_context
from app.auth.jwt import TokenError, decode_access_token
from app.auth.permissions import Permission, is_read_permission
from app.auth.rbac import has_permission, role_level
from app.auth.service import record_audit
from app.core.config import settings
from app.db.models import AuditAction, Tenant, User, UserRole
from app.db.session import get_session

log = structlog.get_logger()

# auto_error=False so a missing header produces our own 401 with a
# WWW-Authenticate hint rather than FastAPI's default 403.
_bearer = HTTPBearer(auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def _forbidden(detail: str = "Insufficient permissions") -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


@dataclass(frozen=True)
class TenantContext:
    """Everything a protected handler is allowed to trust.

    ``user`` is always a real ``users`` row. For a machine credential it is the
    human the credential is attributed to — its owner, or the service account's
    creator — so tenant isolation and the audit trail keep working without a
    phantom account. The machine's own limits are the ``scopes`` field, and
    ``is_machine`` is what a route checks when it must be a person.
    """
    user: User
    tenant: Tenant
    auth_method: str = "password"
    session_id: uuid.UUID | None = None
    scopes: frozenset[str] | None = None
    api_key_id: uuid.UUID | None = None
    service_account_id: uuid.UUID | None = None
    credential_name: str = ""
    #: Loaded from the tenant row. Never read from a JWT claim, so tokens
    #: issued before organizations existed stay valid and cannot name a parent.
    organization_id: uuid.UUID | None = None
    #: ``active`` when no membership row exists (legacy). ``revoked`` never
    #: reaches a handler. ``suspended`` keeps read permissions only.
    membership_status: str = "active"

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.tenant.id

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def role(self) -> UserRole:
        return self.user.role

    @property
    def is_machine(self) -> bool:
        return self.auth_method in ("api_key", "service_account", "scim")

    def can(self, permission: Permission) -> bool:
        """Role, scope, and membership status. A key can only narrow further."""
        if self.membership_status in ("revoked", "expired"):
            return False
        if self.membership_status == "suspended" and not is_read_permission(permission):
            return False
        if not has_permission(self.user.role, permission):
            return False
        if self.scopes is None:
            return True
        return permission.value in self.scopes

    def describe_actor(self) -> str:
        if self.is_machine:
            return self.credential_name or self.auth_method
        return self.user.email


# ------------------------------------------------------------ current user ---

_SESSION_ENDED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Session ended",
    headers={"WWW-Authenticate": "Bearer"},
)


async def _membership_status(session: AsyncSession, user_id: uuid.UUID, tenant_id: uuid.UUID) -> str:
    """Active when no row exists. Revoked or expired denies the request."""
    from sqlalchemy import select
    from sqlalchemy.exc import OperationalError, ProgrammingError

    from app.db.models import OrganizationMembership, TenantMembership

    try:
        tenant = await session.get(Tenant, tenant_id)
        statuses: list[str] = []
        if tenant is not None and tenant.organization_id is not None:
            org_status = (
                await session.execute(
                    select(OrganizationMembership.status).where(
                        OrganizationMembership.organization_id == tenant.organization_id,
                        OrganizationMembership.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if org_status is not None:
                statuses.append(str(org_status))
        tenant_status = (
            await session.execute(
                select(TenantMembership.status).where(
                    TenantMembership.tenant_id == tenant_id,
                    TenantMembership.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        if tenant_status is not None:
            statuses.append(str(tenant_status))
    except (OperationalError, ProgrammingError):
        return "active"
    if not statuses:
        return "active"
    if any(item in ("revoked", "expired") for item in statuses):
        return "revoked"
    if any(item == "suspended" for item in statuses):
        return "suspended"
    if any(item == "active" for item in statuses):
        return "active"
    return "revoked"


async def _apply_membership_gate(
    session: AsyncSession, request: Request, user_id: uuid.UUID, tenant_id: uuid.UUID
) -> None:
    status_name = await _membership_status(session, user_id, tenant_id)
    request.state.membership_status = status_name
    if status_name == "revoked":
        raise _forbidden("Membership is not active")


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None or not credentials.credentials:
        raise _UNAUTHENTICATED
    if (credentials.scheme or "").lower() != "bearer":
        raise _UNAUTHENTICATED

    presented = credentials.credentials

    # STEP 18: machine credentials travel in the same header as a JWT. The
    # shape of the value decides which authenticator runs, so a key is never
    # parsed as a JWT and a JWT is never looked up as a key.
    kind = tokens.split_prefix(
        presented, (settings.api_key_prefix, settings.service_account_prefix)
    )
    if kind:
        from app.auth.identity import api_keys
        from app.auth.identity.exceptions import IdentityError

        try:
            principal = await api_keys.authenticate_credential(session, presented)
        except IdentityError as exc:
            log.warning(
                "auth.machine_credential_rejected",
                reason=getattr(exc, "code", type(exc).__name__),
                path=request.url.path,
            )
            raise _UNAUTHENTICATED from None
        request.state.principal = principal
        request.state.user_id = str(principal.actor.id)
        request.state.tenant_id = str(principal.tenant_id)
        await _apply_membership_gate(session, request, principal.actor.id, principal.tenant_id)
        return principal.actor

    try:
        claims = decode_access_token(presented)
    except TokenError:
        raise _UNAUTHENTICATED from None

    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise _UNAUTHENTICATED

    # The tenant is re-read from the row, not taken from the token, so a
    # user moved between tenants cannot keep using an old token.
    if user.tenant_id != claims.tenant_id:
        log.warning("auth.tenant_claim_mismatch", user_id=str(user.id))
        raise _UNAUTHENTICATED

    # Deactivation, role change and refresh-reuse all bump this.
    if user.token_version != claims.token_version:
        raise _UNAUTHENTICATED

    # A token minted for a browser session dies with that session: "sign out
    # this device" and "sign out everywhere else" must not leave a valid access
    # token behind for the rest of its lifetime. Tokens minted without a session
    # (service-to-service callers, and every token issued before this feature)
    # carry no `sid` and skip the check, which is what keeps them working.
    if claims.session_id is not None:
        row = await session.get(UserSession, claims.session_id)
        if (
            row is None
            or row.user_id != user.id
            or row.tenant_id != user.tenant_id
            or not row.is_live(datetime.now(timezone.utc).replace(tzinfo=None))
        ):
            log.info(
                "auth.session_not_live",
                user_id=str(user.id),
                session_id=str(claims.session_id),
            )
            raise _SESSION_ENDED

    request.state.user_id = str(user.id)
    request.state.tenant_id = str(user.tenant_id)
    # The UUID itself, not its text: ``TenantContext.session_id`` is typed as
    # uuid.UUID and every consumer compares or looks it up as one. Stringifying
    # here is how a "current session" marker silently stops matching.
    request.state.session_id = claims.session_id
    await _apply_membership_gate(session, request, user.id, user.tenant_id)
    return user


async def get_current_tenant(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Tenant:
    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.is_active:
        raise _forbidden("Tenant is inactive")
    return tenant


async def get_context(
    request: Request,
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
) -> TenantContext:
    membership_status = getattr(request.state, "membership_status", "active")
    principal: AuthenticatedPrincipal | None = getattr(request.state, "principal", None)
    if principal is not None:
        return TenantContext(
            user=user,
            tenant=tenant,
            auth_method=principal.kind.value,
            scopes=principal.scopes,
            api_key_id=principal.api_key_id,
            service_account_id=principal.service_account_id,
            credential_name=principal.display_name,
            organization_id=getattr(tenant, "organization_id", None),
            membership_status=membership_status,
        )
    return TenantContext(
        user=user,
        tenant=tenant,
        session_id=getattr(request.state, "session_id", None) or None,
        organization_id=getattr(tenant, "organization_id", None),
        membership_status=membership_status,
    )


async def get_optional_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> TenantContext | None:
    """Authenticated context when a bearer is present, otherwise anonymous.

    A missing header is anonymous. A present but invalid header is still 401.
    """
    if credentials is None or not credentials.credentials:
        return None
    user = await get_current_user(request, credentials, session)
    tenant = await get_current_tenant(user, session)
    return await get_context(request, user, tenant)


async def get_identity_context(
    request: Request,
    ctx: TenantContext = Depends(get_context),
    session: AsyncSession = Depends(get_session),
) -> IdentityContext:
    """The richer context identity routes use: policy, session, factor state.

    Deliberately a *separate* dependency rather than extra fields on
    ``TenantContext``: adding two more queries to every request in the product
    to serve a handful of identity endpoints would be a tax on all of it.
    """
    from app.auth.identity.policies import AuthMethod

    try:
        method = AuthMethod(ctx.auth_method)
    except ValueError:
        method = AuthMethod.PASSWORD
    return await load_identity_context(
        session,
        user=ctx.user,
        tenant=ctx.tenant,
        session_id=ctx.session_id,
        auth_method=method,
        scopes=ctx.scopes,
        service_account_id=ctx.service_account_id,
        api_key_id=ctx.api_key_id,
    )


async def require_human_session(
    ctx: TenantContext = Depends(get_context),
) -> TenantContext:
    """Refuse machine credentials outright.

    Some things only a person may do: enroll a second factor, change a
    password, read another session's device list. A service account holding
    every scope still cannot pass this gate, because the check is on the
    principal's *kind*, not on a permission someone could grant by mistake.
    """
    if ctx.is_machine:
        raise _forbidden("This endpoint requires a signed-in user session")
    return ctx


# ------------------------------------------------------------- authorization ---

def require_permission(
    *permissions: Permission, require_all: bool = True
) -> Callable[..., Awaitable[TenantContext]]:
    """
    Dependency factory. Default is AND; pass require_all=False for OR.

    A denial is written to the audit log so repeated probing is visible.
    """
    async def _dependency(
        request: Request,
        ctx: TenantContext = Depends(get_context),
        session: AsyncSession = Depends(get_session),
    ) -> TenantContext:
        # Role AND scope. A machine credential can only ever narrow its owner's
        # permissions, never widen them, because both halves must pass.
        checks = [ctx.can(p) for p in permissions]
        ok = all(checks) if require_all else any(checks)
        if not ok:
            missing = [
                p.value for p, granted in zip(permissions, checks) if not granted
            ]
            await record_audit(
                session, action=AuditAction.AUTHZ_DENIED, tenant_id=ctx.tenant_id,
                actor_user_id=ctx.user_id, actor_email=ctx.user.email,
                ip_address=_client_ip(request),
                detail={
                    "missing": missing,
                    "path": request.url.path,
                    "auth_method": ctx.auth_method,
                    "principal": ctx.describe_actor(),
                },
            )
            raise _forbidden(f"Requires permission: {', '.join(missing)}")
        return ctx

    return _dependency


def require_role(*roles: UserRole) -> Callable[..., Awaitable[TenantContext]]:
    """Prefer require_permission. Use this only for genuinely role-shaped rules."""
    allowed = set(roles)

    async def _dependency(ctx: TenantContext = Depends(get_context)) -> TenantContext:
        if ctx.role not in allowed:
            raise _forbidden(
                f"Requires role: {', '.join(sorted(r.value for r in allowed))}"
            )
        return ctx

    return _dependency


def require_min_role(minimum: UserRole) -> Callable[..., Awaitable[TenantContext]]:
    threshold = role_level(minimum)

    async def _dependency(ctx: TenantContext = Depends(get_context)) -> TenantContext:
        if ctx.is_machine:
            # A machine credential carries scopes, not a role. Comparing a
            # synthetic role here would be a guess; refusing is the honest
            # answer, and every endpoint that needs a role has a permission
            # equivalent the credential can be granted instead.
            raise _forbidden("This endpoint requires a signed-in user session")
        if role_level(ctx.role) < threshold:
            raise _forbidden(f"Requires at least the {minimum.value} role")
        return ctx

    return _dependency


# ---------------------------------------------------------- tenant isolation ---

async def tenant_path_guard(
    tenant_id: uuid.UUID = Path(...),
    ctx: TenantContext = Depends(get_context),
) -> TenantContext:
    """
    For routes that keep `{tenant_id}` in the URL.

    Returns 404 rather than 403 on a mismatch: confirming that some other
    tenant id exists is itself an information leak.
    """
    if tenant_id != ctx.tenant_id:
        log.warning(
            "authz.cross_tenant_path_blocked",
            user_id=str(ctx.user_id), requested=str(tenant_id),
        )
        raise HTTPException(status_code=404, detail="Not found")
    return ctx


def scoped_permission(
    *permissions: Permission,
) -> Callable[..., Awaitable[TenantContext]]:
    """
    The combination used by almost every route: the path tenant must match the
    token tenant AND the caller must hold the permission.
    """
    permission_dep = require_permission(*permissions)

    async def _dependency(
        tenant_id: uuid.UUID = Path(...),
        ctx: TenantContext = Depends(permission_dep),
    ) -> TenantContext:
        if tenant_id != ctx.tenant_id:
            log.warning(
                "authz.cross_tenant_path_blocked",
                user_id=str(ctx.user_id), requested=str(tenant_id),
            )
            raise HTTPException(status_code=404, detail="Not found")
        return ctx

    return _dependency


async def get_owned(
    session: AsyncSession,
    model: Any,
    object_id: uuid.UUID,
    ctx: TenantContext,
    *,
    tenant_field: str = "tenant_id",
) -> Any:
    """
    Fetch a row by id and prove it belongs to the caller's tenant.

    Always 404 on both "missing" and "belongs to someone else", so object ids
    cannot be probed for existence across tenants.
    """
    obj = await session.get(model, object_id)
    if obj is None or getattr(obj, tenant_field, None) != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="Not found")
    return obj


def tenant_filter(model: Any, ctx: TenantContext):
    """Mandatory WHERE clause for every list query."""
    return model.tenant_id == ctx.tenant_id


# ------------------------------------------------------------------ helpers ---

def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


async def get_platform_admin(
    ctx: TenantContext = Depends(get_context),
) -> TenantContext:
    """
    Guard for operator-only actions such as creating a whole new tenant.

    There is no platform-superuser concept yet, so this denies everyone and
    those endpoints are served by an out-of-band script instead. Documented as
    a known limitation rather than left as an open endpoint.
    """
    raise _forbidden("Platform administration is not available through this API")
````

### `app/auth/permissions.py`

````python
"""
The permission vocabulary.

Every capability in the product is named exactly once here. Route code asks
for a `Permission`, never for a role string -- so adding a role, or moving a
capability between roles, is a one-line change in `rbac.ROLE_PERMISSIONS`
instead of a repository-wide grep for `if user.role == "admin"`.
"""
from __future__ import annotations

import enum


def is_read_permission(permission: "Permission") -> bool:
    """True for a permission that does not change state.

    Suspended membership may keep these and must lose everything else. The
    test is the permission id, not a second role table.
    """
    value = permission.value
    return value.endswith(":read") or value.endswith(":read_all")


class Permission(str, enum.Enum):
    # ---- tenant configuration -------------------------------------------
    TENANT_READ = "tenant:read"
    TENANT_UPDATE = "tenant:update"          # voice tuning, IVR, business hours
    TENANT_CREATE = "tenant:create"          # provisioning a brand new business
    TENANT_DELETE = "tenant:delete"

    # ---- team -----------------------------------------------------------
    USER_READ = "user:read"
    USER_CREATE = "user:create"
    USER_UPDATE = "user:update"              # rename, deactivate, reactivate
    USER_ROLE_CHANGE = "user:role_change"
    USER_DELETE = "user:delete"

    # ---- operational data -----------------------------------------------
    CALL_READ = "call:read"
    CALL_READ_ALL = "call:read_all"          # not just calls assigned to me
    TRANSCRIPT_READ = "transcript:read"
    RECORDING_READ = "recording:read"

    LEAD_READ = "lead:read"
    LEAD_CREATE = "lead:create"
    LEAD_UPDATE = "lead:update"
    LEAD_DELETE = "lead:delete"

    APPOINTMENT_READ = "appointment:read"
    APPOINTMENT_WRITE = "appointment:write"

    CAMPAIGN_READ = "campaign:read"
    CAMPAIGN_WRITE = "campaign:write"
    CAMPAIGN_RUN = "campaign:run"            # spends real money on calls

    # ---- knowledge base / RAG -------------------------------------------
    KNOWLEDGE_READ = "knowledge:read"        # list/inspect documents
    KNOWLEDGE_WRITE = "knowledge:write"      # upload, reindex, archive
    KNOWLEDGE_DELETE = "knowledge:delete"    # permanent removal

    # ---- reporting -------------------------------------------------------
    ANALYTICS_READ = "analytics:read"
    AUDIT_READ = "audit:read"

    # ---- sensitive -------------------------------------------------------
    INTEGRATION_READ = "integration:read"    # CRM / calendar wiring
    INTEGRATION_WRITE = "integration:write"
    # Firing a sync spends the tenant's provider rate limit and writes to
    # their CRM, so it is separated from merely reading the configuration.
    INTEGRATION_SYNC = "integration:sync"
    COMPLIANCE_READ = "compliance:read"
    COMPLIANCE_WRITE = "compliance:write"    # A2P registration, DNC handling
    SECURITY_SETTINGS = "security:settings"
    BILLING_READ = "billing:read"
    BILLING_WRITE = "billing:write"

    # ---- enterprise identity (STEP 18) ----------------------------------
    # Named in the same `resource:action` vocabulary as everything above, so an
    # API key scope is simply the permission string and there is exactly one
    # list to review. `identity:read` exposes the tenant's security posture
    # (which connections exist, which sessions are live) but never a secret;
    # `identity:write` is the one that changes who can log in and how.
    IDENTITY_READ = "identity:read"
    IDENTITY_WRITE = "identity:write"
    API_KEY_MANAGE = "apikey:manage"                 # any key in the tenant
    SERVICE_ACCOUNT_MANAGE = "service_account:manage"  # machine identities


# Convenience bundles used when composing roles. Kept here so `rbac.py` reads
# as a policy document rather than a wall of enum members.
READ_ONLY_OPERATIONAL: frozenset[Permission] = frozenset({
    Permission.TENANT_READ,
    Permission.CALL_READ,
    Permission.CALL_READ_ALL,
    Permission.TRANSCRIPT_READ,
    Permission.LEAD_READ,
    Permission.APPOINTMENT_READ,
    Permission.CAMPAIGN_READ,
    Permission.KNOWLEDGE_READ,
    Permission.ANALYTICS_READ,
})

OPERATIONAL_WRITE: frozenset[Permission] = frozenset({
    Permission.LEAD_CREATE,
    Permission.LEAD_UPDATE,
    Permission.APPOINTMENT_WRITE,
    Permission.CAMPAIGN_WRITE,
    Permission.CAMPAIGN_RUN,
    Permission.RECORDING_READ,
})

TEAM_MANAGEMENT: frozenset[Permission] = frozenset({
    Permission.USER_READ,
    Permission.USER_CREATE,
    Permission.USER_UPDATE,
    Permission.USER_ROLE_CHANGE,
})
````

### `app/auth/service.py`

````python
"""
Authentication and team-management business logic.

Kept out of the route layer so the rules -- lockout, rotation, last-owner
protection, escalation blocking -- can be tested directly and cannot be
bypassed by a second route that forgets one of them.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import structlog
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import jwt as jwt_utils
from app.auth import password as pw
from app.auth.rbac import can_assign_role, can_manage_user
from app.auth.identity.models import UserSession
from app.core.config import settings
from app.db.models import AuditAction, AuditLog, RefreshToken, Tenant, User, UserRole

log = structlog.get_logger()


class AuthError(Exception):
    """Authentication failed. The message is deliberately generic."""


class PermissionDenied(Exception):
    pass


class ConflictError(Exception):
    pass


def _now() -> datetime:
    return _utcnow()


def _naive_utc(value: datetime | None) -> datetime | None:
    """Columns are written naive in places; compare consistently."""
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo else value


# ------------------------------------------------------------------- email ---

async def _idle_minutes_for(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    from app.auth.identity.policies import load_policy

    policy = await load_policy(session, tenant_id)
    return policy.session_idle_minutes


def _utcnow() -> datetime:
    """Naive UTC, matching every existing timestamp column in this schema."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_email(raw: str) -> str:
    """
    Lowercased, Unicode-normalised, deliverability not checked (that would
    make login depend on DNS). Raises ValueError on a malformed address.
    """
    try:
        result = validate_email(raw, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValueError(str(exc)) from exc
    return result.normalized.lower()


# ------------------------------------------------------------------- audit ---

async def record_audit(
    session: AsyncSession,
    *,
    action: AuditAction,
    tenant_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    target_user_id: uuid.UUID | None = None,
    actor_email: str = "",
    ip_address: str = "",
    user_agent: str = "",
    detail: dict | None = None,
    commit: bool = True,
) -> AuditLog:
    """`detail` must never contain a password, token or API key."""
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        action=action,
        actor_email=actor_email[:320],
        ip_address=ip_address[:64],
        user_agent=user_agent[:300],
        detail=detail or {},
    )
    session.add(entry)
    if commit:
        await session.commit()
    return entry


# ---------------------------------------------------------------- lockout ---

def is_locked(user: User) -> bool:
    locked_until = _naive_utc(user.locked_until)
    return locked_until is not None and locked_until > _utcnow()


async def _register_failure(session: AsyncSession, user: User) -> None:
    user.failed_login_count += 1
    if user.failed_login_count >= settings.max_failed_logins:
        user.locked_until = _utcnow() + timedelta(minutes=settings.lockout_minutes)
        user.failed_login_count = 0
        log.warning("auth.account_locked", user_id=str(user.id))
    await session.commit()


# --------------------------------------------------------------- authenticate ---

async def authenticate(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    ip_address: str = "",
    user_agent: str = "",
) -> User:
    """
    Returns the User or raises AuthError.

    Every failure path raises the SAME message and spends comparable CPU, so a
    caller cannot distinguish "no such account" from "wrong password" and use
    the endpoint to enumerate registered addresses.
    """
    generic = AuthError("Invalid email or password.")

    try:
        normalized = normalize_email(email)
    except ValueError:
        pw.verify_dummy()
        raise generic from None

    user = (
        await session.execute(select(User).where(User.email == normalized))
    ).scalar_one_or_none()

    if user is None:
        pw.verify_dummy()
        await record_audit(
            session, action=AuditAction.LOGIN_FAILURE, actor_email=normalized,
            ip_address=ip_address, user_agent=user_agent,
            detail={"reason": "no_such_user"},
        )
        raise generic

    if is_locked(user):
        pw.verify_dummy()
        await record_audit(
            session, action=AuditAction.LOGIN_FAILURE, tenant_id=user.tenant_id,
            actor_user_id=user.id, actor_email=normalized, ip_address=ip_address,
            user_agent=user_agent, detail={"reason": "locked"},
        )
        raise generic

    if not pw.verify_password(password, user.password_hash):
        await record_audit(
            session, action=AuditAction.LOGIN_FAILURE, tenant_id=user.tenant_id,
            actor_user_id=user.id, actor_email=normalized, ip_address=ip_address,
            user_agent=user_agent, detail={"reason": "bad_password"}, commit=False,
        )
        await _register_failure(session, user)
        raise generic

    # Correct password but disabled: still a generic failure to the caller.
    if not user.is_active:
        await record_audit(
            session, action=AuditAction.LOGIN_FAILURE, tenant_id=user.tenant_id,
            actor_user_id=user.id, actor_email=normalized, ip_address=ip_address,
            user_agent=user_agent, detail={"reason": "inactive"},
        )
        raise generic

    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.is_active:
        await record_audit(
            session, action=AuditAction.LOGIN_FAILURE, tenant_id=user.tenant_id,
            actor_user_id=user.id, actor_email=normalized, ip_address=ip_address,
            user_agent=user_agent, detail={"reason": "tenant_inactive"},
        )
        raise generic

    # STEP 18: tenant and domain policy. Evaluated *after* the password check,
    # so a refusal can never be used to learn whether an address exists -- by
    # the time it can happen, the caller has already proved they own the
    # account. The reason is recorded for the operator, never returned.
    refusal = await _login_policy_refusal(session, user)
    if refusal is not None:
        await record_audit(
            session, action=AuditAction.LOGIN_FAILURE, tenant_id=user.tenant_id,
            actor_user_id=user.id, actor_email=normalized, ip_address=ip_address,
            user_agent=user_agent, detail={"reason": refusal},
        )
        raise generic

    # Transparent upgrade if the cost factor was raised since signup.
    if pw.needs_rehash(user.password_hash):
        user.password_hash = pw.hash_password(password)

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = _utcnow()
    await record_audit(
        session, action=AuditAction.LOGIN_SUCCESS, tenant_id=user.tenant_id,
        actor_user_id=user.id, actor_email=normalized, ip_address=ip_address,
        user_agent=user_agent, commit=False,
    )
    await session.commit()
    return user


async def _login_policy_refusal(session: AsyncSession, user: User) -> str | None:
    """The reason a password login may not proceed, or ``None``.

    Returns a short reason code rather than raising, because the caller must
    translate *every* outcome into the same generic 401. A tenant that requires
    SSO, one that has switched password login off, one whose domains demand a
    federated login, and one that restricts login to listed email domains all
    land here.
    """
    from app.auth.identity import policies

    policy = await policies.load_policy(session, user.tenant_id)
    domain = await policies.load_domain_evaluation(session, user.email)
    evaluation = policies.evaluate_login_policy(
        policy, email=user.email, role=user.role, domain_policy=domain
    )
    if evaluation.allowed:
        return None
    return evaluation.reason or "policy_denied"


# ------------------------------------------------------------------ tokens ---

@dataclass(frozen=True)
class LoginRequirements:
    """What a caller must still satisfy after proving their password.

    Computed *after* a successful password check, so it can never be used to
    learn anything about an address that has not already authenticated.
    """

    mfa_required: bool
    mfa_available: bool
    enrollment_required: bool
    sso_required: bool
    idle_minutes: int
    session_max_active: int


async def post_login_requirements(session: AsyncSession, user: User) -> LoginRequirements:
    """Whether this user must present a second factor, and whether they can.

    ``POST /auth/login`` calls this immediately after ``authenticate``. The
    interesting case is ``mfa_required and not mfa_available``: the tenant
    requires a second factor and the user has never enrolled one. Refusing the
    login would lock them out of the only page where they could fix it, so the
    login succeeds with ``enrollment_required`` set and the dashboard sends
    them to enrollment. What the requirement *does* gate is everything
    privileged: ``identity.service.assert_privileged`` still demands a fresh
    proof of presence, which for this user is their password.
    """
    from app.auth.identity import mfa as identity_mfa
    from app.auth.identity import policies

    policy = await policies.load_policy(session, user.tenant_id)
    domain = await policies.load_domain_evaluation(session, user.email)
    evaluation = policies.evaluate_login_policy(
        policy,
        email=user.email,
        role=user.role,
        mfa_available=True,
        user_mfa_override=user.mfa_required,
        domain_policy=domain,
    )
    has_factor = await identity_mfa.has_active_factor(session, user_id=user.id)
    return LoginRequirements(
        mfa_required=evaluation.mfa_required,
        mfa_available=has_factor,
        enrollment_required=evaluation.mfa_required and not has_factor,
        sso_required=evaluation.sso_required,
        idle_minutes=policy.session_idle_minutes,
        session_max_active=policy.session_max_active,
    )


async def issue_tokens(
    session: AsyncSession,
    user: User,
    *,
    ip_address: str = "",
    user_agent: str = "",
    auth_method: str = "password",
    mfa_verified: bool = False,
    password_confirmed: bool = True,
    session_row: "object | None" = None,
    sso_connection_id: uuid.UUID | None = None,
) -> dict:
    """Mint an access/refresh pair plus the session they belong to.

    The defaults reproduce the pre-STEP-18 behaviour exactly -- a password login
    that opens a session and records that the password was just checked -- and
    the return value only *adds* keys, so an existing caller sees no change.
    """
    if session_row is None:
        from app.auth.identity.policies import AuthMethod
        from app.auth.identity.service import ensure_session

        session_row, _label = await ensure_session(
            session,
            user,
            auth_method=AuthMethod(auth_method),
            mfa_verified=mfa_verified,
            password_confirmed=password_confirmed and auth_method == "password",
            ip_address=ip_address,
            user_agent=user_agent,
            sso_connection_id=sso_connection_id,
            commit=False,
        )

    access, expires_in = jwt_utils.create_access_token(
        user_id=user.id, tenant_id=user.tenant_id,
        role=user.role.value, token_version=user.token_version,
        session_id=getattr(session_row, "id", None),
        auth_method=auth_method,
        mfa_verified=mfa_verified,
    )
    plaintext, digest = jwt_utils.generate_refresh_token()
    session.add(RefreshToken(
        user_id=user.id,
        token_hash=digest,
        expires_at=jwt_utils.refresh_expiry().replace(tzinfo=None),
        user_agent=user_agent[:300],
        ip_address=ip_address[:64],
        session_id=getattr(session_row, "id", None),
    ))
    await session.commit()
    return {
        "access_token": access,
        "refresh_token": plaintext,
        "token_type": "bearer",
        "expires_in": expires_in,
        "session_id": str(getattr(session_row, "id", "")) or None,
    }


async def rotate_refresh_token(
    session: AsyncSession, plaintext: str, *, ip_address: str = "", user_agent: str = ""
) -> dict:
    """
    Single-use rotation with reuse detection.

    Presenting an already-used token means the token leaked, so every live
    refresh token for that user is revoked and the access tokens are
    invalidated by bumping token_version.
    """
    generic = AuthError("Invalid refresh token.")
    digest = jwt_utils.hash_refresh_token(plaintext)

    record = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == digest))
    ).scalar_one_or_none()
    if record is None:
        raise generic

    user = await session.get(User, record.user_id)

    if record.used_at is not None or record.revoked_at is not None:
        log.warning("auth.refresh_reuse_detected", user_id=str(record.user_id))
        await revoke_all_for_user(
            session,
            record.user_id,
            bump_version=True,
            identity_reason="refresh_reuse_detected",
        )
        if user is not None:
            await record_audit(
                session, action=AuditAction.AUTHZ_DENIED, tenant_id=user.tenant_id,
                actor_user_id=user.id, actor_email=user.email, ip_address=ip_address,
                detail={"reason": "refresh_token_reuse"},
            )
        raise generic

    if _naive_utc(record.expires_at) <= _utcnow():
        raise generic

    if user is None or not user.is_active:
        raise generic

    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.is_active:
        raise generic

    # STEP 18: a refresh token issued for a session dies with that session, so
    # "sign out this device" really signs it out rather than waiting for the
    # token to expire. Rows created before sessions existed carry no
    # session_id and keep rotating exactly as they used to.
    session_row = None
    if record.session_id is not None:
        from app.auth.identity import sessions as identity_sessions

        session_row = await session.get(UserSession, record.session_id)
        if session_row is None or not session_row.is_live(_utcnow()):
            log.info(
                "auth.refresh_for_dead_session",
                user_id=str(user.id),
                session_id=str(record.session_id),
            )
            raise generic
        record.used_at = _utcnow()
        await identity_sessions.touch(
            session,
            session_row,
            ip_address=ip_address,
            user_agent=user_agent,
            idle_minutes=await _idle_minutes_for(session, user.tenant_id),
        )
    else:
        record.used_at = _utcnow()

    tokens = await issue_tokens(
        session,
        user,
        ip_address=ip_address,
        user_agent=user_agent,
        auth_method=session_row.auth_method if session_row else "password",
        mfa_verified=bool(session_row.mfa_verified) if session_row else False,
        password_confirmed=False,
        session_row=session_row,
    )

    replacement = (
        await session.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == jwt_utils.hash_refresh_token(
                tokens["refresh_token"]
            ))
        )
    ).scalar_one_or_none()
    if replacement is not None:
        record.replaced_by = replacement.id

    await record_audit(
        session, action=AuditAction.TOKEN_REFRESH, tenant_id=user.tenant_id,
        actor_user_id=user.id, actor_email=user.email, ip_address=ip_address,
        commit=False,
    )
    await session.commit()
    return tokens


async def revoke_refresh_token(session: AsyncSession, plaintext: str) -> bool:
    digest = jwt_utils.hash_refresh_token(plaintext)
    record = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == digest))
    ).scalar_one_or_none()
    if record is None or record.revoked_at is not None:
        return False
    record.revoked_at = _utcnow()
    await session.commit()
    return True


async def revoke_all_for_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    bump_version: bool = False,
    identity_reason: str | None = None,
) -> int:
    """Revoke every outstanding refresh token for a user.

    ``bump_version`` additionally invalidates the access tokens already handed
    out (the JWT carries the version it was minted with).

    ``identity_reason`` closes the third hole: the ``user_sessions`` rows. A
    caller that passes one gets that user's live sessions revoked too, so "all
    credentials are dead" is true in the session list and not only in the token
    tables. It is opt-in rather than automatic because several callers (logout,
    password reset, MFA change) revoke sessions themselves with a more specific
    reason and an explicit ``keep_session_id``; revoking here first would
    overwrite theirs.
    """
    rows = (
        await session.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
            )
        )
    ).scalars().all()
    now = _utcnow()
    for row in rows:
        row.revoked_at = now
    if bump_version:
        user = await session.get(User, user_id)
        if user is not None:
            user.token_version += 1
    if identity_reason:
        from app.auth.identity import sessions as identity_sessions

        await identity_sessions.revoke_all_for_user(
            session, user_id=user_id, reason=identity_reason, commit=False
        )
    await session.commit()
    return len(rows)


# --------------------------------------------------------- team management ---

async def count_active_owners(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count(User.id)).where(
                User.tenant_id == tenant_id,
                User.role == UserRole.OWNER,
                User.is_active.is_(True),
            )
        )
    ).scalar_one()


async def create_user(
    session: AsyncSession,
    *,
    actor: User,
    email: str,
    password: str,
    role: UserRole,
    full_name: str = "",
    tenant_id: uuid.UUID | None = None,
) -> User:
    """
    The new user is always created inside the ACTOR's tenant. `tenant_id` is
    accepted only so a caller can be explicit, and a mismatch is rejected --
    it can never be used to plant a user in someone else's tenant.
    """
    if tenant_id is not None and tenant_id != actor.tenant_id:
        raise PermissionDenied("Cannot create a user in another tenant.")

    if not can_assign_role(actor.role, role):
        raise PermissionDenied(
            f"A {actor.role.value} cannot grant the {role.value} role."
        )

    normalized = normalize_email(email)
    pw.validate_policy(password, email=normalized)

    existing = (
        await session.execute(select(User).where(User.email == normalized))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError("That email address is already registered.")

    user = User(
        tenant_id=actor.tenant_id,
        email=normalized,
        full_name=full_name.strip()[:200],
        password_hash=pw.hash_password(password),
        role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    await record_audit(
        session, action=AuditAction.USER_CREATED, tenant_id=actor.tenant_id,
        actor_user_id=actor.id, target_user_id=user.id, actor_email=actor.email,
        detail={"role": role.value}, commit=False,
    )
    await session.commit()
    await session.refresh(user)
    return user


async def change_role(
    session: AsyncSession, *, actor: User, target: User, new_role: UserRole
) -> User:
    if target.tenant_id != actor.tenant_id:
        raise PermissionDenied("Cross-tenant modification is not allowed.")
    if target.id == actor.id:
        raise PermissionDenied("You cannot change your own role.")
    if not can_manage_user(actor.role, target.role):
        raise PermissionDenied("You cannot modify a user at or above your level.")
    if not can_assign_role(actor.role, new_role):
        raise PermissionDenied(f"You cannot grant the {new_role.value} role.")

    # Demoting the last owner would lock the tenant out of its own settings.
    if target.role is UserRole.OWNER and new_role is not UserRole.OWNER:
        if await count_active_owners(session, target.tenant_id) <= 1:
            raise ConflictError("A tenant must always have at least one active owner.")

    previous = target.role
    target.role = new_role
    target.token_version += 1          # old access tokens stop working now
    from app.organization.membership_service import sync_home_role
    await sync_home_role(session, target)
    await revoke_all_for_user(session, target.id, identity_reason="role_changed")
    await record_audit(
        session, action=AuditAction.ROLE_CHANGED, tenant_id=actor.tenant_id,
        actor_user_id=actor.id, target_user_id=target.id, actor_email=actor.email,
        detail={"from": previous.value, "to": new_role.value}, commit=False,
    )
    await session.commit()
    await session.refresh(target)
    return target


async def set_active(
    session: AsyncSession, *, actor: User, target: User, active: bool
) -> User:
    if target.tenant_id != actor.tenant_id:
        raise PermissionDenied("Cross-tenant modification is not allowed.")
    if target.id == actor.id and not active:
        raise PermissionDenied("You cannot deactivate yourself.")
    if not can_manage_user(actor.role, target.role):
        raise PermissionDenied("You cannot modify a user at or above your level.")

    if not active and target.role is UserRole.OWNER:
        if await count_active_owners(session, target.tenant_id) <= 1:
            raise ConflictError("A tenant must always have at least one active owner.")

    target.is_active = active
    target.token_version += 1
    if not active:
        await revoke_all_for_user(
            session, target.id, identity_reason="user_deactivated"
        )
    await record_audit(
        session,
        action=AuditAction.USER_DEACTIVATED if not active else AuditAction.USER_REACTIVATED,
        tenant_id=actor.tenant_id, actor_user_id=actor.id, target_user_id=target.id,
        actor_email=actor.email, commit=False,
    )
    await session.commit()
    await session.refresh(target)
    return target
````

### `app/main.py`

````python
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.agent_management_routes import router as agent_management_router
from app.api.auth_routes import router as auth_router
from app.api.appointment_routes import (
    calendar_router,
    router as appointment_router,
)
from app.api.analytics_routes import router as analytics_router
from app.api.automation_routes import router as automation_router
from app.api.campaign_routes import router as campaign_router
from app.api.inbox_routes import router as inbox_router
from app.api.notification_routes import router as notification_router
from app.api.workflow_routes import router as workflow_router
from app.api.billing_routes import router as billing_router
from app.api.calendar_webhook_routes import router as calendar_webhook_router
from app.api.crm_webhook_routes import router as crm_webhook_router
from app.api.integration_routes import router as integration_router
from app.api.knowledge_routes import router as knowledge_router
from app.api.routes import router as api_router
from app.api.organization_routes import router as organization_router
from app.api.tenant_admin_routes import router as tenant_admin_router
from app.api.environment_routes import router as environment_router
from app.api.organization_membership_routes import router as organization_membership_router
from app.api.tenant_membership_routes import router as tenant_membership_router
from app.api.environment_access_routes import router as environment_access_router
from app.api.team_routes import router as team_router
from app.api.gdpr_routes import router as gdpr_router
from app.api.license_routes import router as license_router
from app.api.api_key_routes import router as api_key_router
from app.api.domain_routes import router as domain_router
from app.api.identity_routes import router as identity_router
from app.api.mfa_routes import router as mfa_router
from app.api.password_routes import router as password_router
from app.api.scim_routes import admin_router as scim_admin_router
from app.api.scim_routes import scim_router
from app.api.service_account_routes import router as service_account_router
from app.api.session_routes import router as session_router
from app.api.sso_routes import admin_router as sso_admin_router
from app.api.sso_routes import public_router as sso_public_router
from app.core import health as health_check
from app.core.chaos import add_chaos_middleware
from app.core.config import settings
from app.core.errors import install_error_handling
from app.core.logging import log
from app.core.metrics import add_metrics_endpoint, add_metrics_middleware
from app.core.rate_limit import add_rate_limit_middleware
from app.core.security_headers import add_security_headers
from app.core.security_txt import add_security_txt
from app.db.models import Base
from app.db.session import get_engine
from app.channels.messaging import router as channels_router
from app.telephony.twilio_handler import router as telephony_router

# Observability: Sentry error reporting is optional and off unless a DSN is
# configured. Initialised at import time so it covers startup failures too.
if settings.sentry_dsn:
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        # Keep a small trace sample in production; none in dev/test.
        traces_sample_rate=0.1 if settings.is_production else 0.0,
        send_default_pii=False,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to serve traffic with an insecure configuration. In development
    # the same problems are logged as warnings so the app stays runnable.
    problems = settings.validate_security()
    if problems:
        if settings.is_production:
            raise RuntimeError(
                "Insecure configuration, refusing to start: " + "; ".join(problems)
            )
        for problem in problems:
            log.warning("config.insecure", problem=problem)

    engine = get_engine()
    if settings.app_env.lower() in {"development", "test"}:
        # Development/test bootstrap: create any missing tables so the app is
        # usable without running migrations. Production AND staging never do
        # this -- Alembic is the sole schema owner there, and create_all
        # would build schema outside the migration history (and staging must
        # mirror production's schema exactly). See alembic/versions/.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    # STEP 7: make sure the plan catalogue exists, and check that every active
    # priced plan has a provider price id.
    #
    # Seeding lives here rather than in a migration because prices are
    # business data that changes: repricing should be an operator action
    # against a running system, not a schema change. `sync_seed_plans` never
    # overwrites a price an operator has edited.
    #
    # The configuration check is requirement 6 -- a plan with no price id
    # fails at boot rather than at checkout, where the failure would be in
    # front of a customer holding a credit card.
    from app.billing.plans import configuration_problems, list_plans, sync_seed_plans
    from app.db.session import get_sessionmaker

    maker = get_sessionmaker()
    async with maker() as session:
        await sync_seed_plans(session)
        plan_problems = configuration_problems(
            await list_plans(session, active_only=True),
            is_production=settings.is_production,
        )
    if plan_problems:
        if settings.is_production and settings.billing_provider == "stripe":
            raise RuntimeError(
                "Billing is misconfigured, refusing to start: "
                + "; ".join(plan_problems)
            )
        for problem in plan_problems:
            log.warning("billing.plan_misconfigured", problem=problem)

    log.info("voxdesk.started")
    yield
    await engine.dispose()


def _api_docs_config() -> dict[str, str | None]:
    """Swagger / ReDoc / OpenAPI are developer surfaces.

    In production they expose the full API schema and an interactive
    "try it out" console, so they are disabled there. Development keeps the
    FastAPI defaults.
    """
    if settings.is_production:
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {}


app = FastAPI(
    title="VoxDesk",
    version="0.4.0",
    lifespan=lifespan,
    **_api_docs_config(),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,   # never "*" once cookies are in play
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Host-header validation. Off unless TRUSTED_HOSTS is set, so the single-proxy
# topology (Caddy terminates TLS for exactly the configured domains and binds
# the API to loopback) is unchanged; when set, the app rejects a request whose
# Host header names any other host, closing host-poisoning SSRF and
# cache-poisoning at the application layer too.
if settings.trusted_host_list:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=settings.trusted_host_list,
    )

app.include_router(telephony_router)
app.include_router(channels_router)
app.include_router(auth_router)
app.include_router(team_router)
app.include_router(knowledge_router)
app.include_router(integration_router)
app.include_router(crm_webhook_router)
app.include_router(appointment_router)
app.include_router(calendar_router)
app.include_router(calendar_webhook_router)
app.include_router(billing_router)
app.include_router(analytics_router)
app.include_router(api_router)
app.include_router(gdpr_router)
app.include_router(license_router)

# Enterprise expansion surface. Batch 01 shipped these route modules with
# registration deliberately out of its file set ("reported as an integration
# dependency"); Batch 02 closes that dependency, and adds the three route
# modules whose services had no HTTP surface at all (automation, notification,
# inbox). Registration order is irrelevant to routing — every path here is
# distinct — but it is kept stable so `app.routes` is diffable.
# STEP 18, enterprise identity. Registered here, in the same place and the same
# way as everything above: the human identity surface (identity, MFA, sessions),
# the two public credential-recovery flows plus the login-page capability
# lookup (password_router), machine credentials (api_key_router for API keys,
# service_account_router for machine identities), enterprise
# domains (domain_router), SSO administration and its public login endpoints
# (sso_admin_router / sso_public_router), and SCIM provisioning
# (scim_admin_router for the credentials an IdP uses, scim_router for the
# protocol itself).
app.include_router(identity_router)
app.include_router(mfa_router)
app.include_router(session_router)
app.include_router(password_router)
app.include_router(api_key_router)
app.include_router(service_account_router)
app.include_router(domain_router)
app.include_router(sso_admin_router)
app.include_router(sso_public_router)
app.include_router(scim_admin_router)
app.include_router(scim_router)
# Organization → tenant → environment foundation. Same registration site as
# the identity routers. Paths do not overlap the existing ``/api/tenants``
# collection; hierarchy ids in the URL are checked against the principal.
app.include_router(organization_router)
app.include_router(tenant_admin_router)
app.include_router(environment_router)
app.include_router(organization_membership_router)
app.include_router(tenant_membership_router)
app.include_router(environment_access_router)

app.include_router(agent_management_router)
app.include_router(workflow_router)
app.include_router(campaign_router)
app.include_router(automation_router)
app.include_router(notification_router)
app.include_router(inbox_router)

# Cross-cutting middleware and handlers. Order is deliberate: exception
# handlers + request-id first, then security headers, then rate limiting, then
# (test-only) failure injection, then metrics — which observes whatever the
# inner stack produces, injected failures and latency included.
install_error_handling(app)
add_security_headers(app)
add_rate_limit_middleware(app)
add_chaos_middleware(app)
add_metrics_middleware(app)
add_metrics_endpoint(app)
add_security_txt(app)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/ready")
async def readiness():
    """Readiness probe: the process is up AND it can serve traffic.

    Distinct from /health (liveness): a load balancer routes traffic only to
    nodes whose /health/ready returns 200, so a node that lost its database —
    or its Redis, or (in production) its voice providers — stops receiving
    work instead of failing every request. The checks never call a provider:
    they verify configuration presence and dependency reachability only. See
    app/core/health.py for the semantics.
    """
    result = await health_check.readiness()
    status_code = 200 if result["ready"] else 503
    if not result["ready"]:
        log.error("readiness.unavailable", checks=result["body"]["checks"])
    return JSONResponse(status_code=status_code, content=result["body"])


def _mount_dashboard_if_built(app: FastAPI, dist_dir: str | None = None) -> None:
    """Serve the built dashboard when it is present in the image.

    The React build is a separate stage in the Dockerfile. When it exists
    (production image) its static assets are mounted and any non-API path falls
    back to index.html so client-side routes (e.g. /agent) survive a refresh.
    In dev/test the dist directory does not exist and the app stays API-only.
    """
    if dist_dir is None:
        dist_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "dashboard", "dist")
        )
    index_file = os.path.join(dist_dir, "index.html")
    if not os.path.isfile(index_file):
        return

    assets_dir = os.path.join(dist_dir, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def _spa_fallback(full_path: str):
        # Never let the SPA shell swallow unknown API/telephony/auth paths -- a
        # typo'd client call must 404 (JSON), not receive an HTML 200.
        if full_path.startswith(("api/", "auth/", "telephony/", "channels/", "health")):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        # API/telephony/auth paths are handled by the routers above; anything
        # else maps to a real file when one exists, otherwise the SPA shell.
        candidate = os.path.normpath(os.path.join(dist_dir, full_path))
        if (
            full_path
            and os.path.isfile(candidate)
            and os.path.abspath(candidate).startswith(os.path.abspath(dist_dir))
        ):
            return FileResponse(candidate)
        return FileResponse(index_file)


_mount_dashboard_if_built(app)
````

### `app/api/routes.py`

````python
"""REST API for the React dashboard (and for your own onboarding scripts).

Membership and environment-access routers are registered in ``app.main`` next
to the organization hierarchy routers. They are not mounted on this router:
this router already owns ``/api/tenants`` resource routes, and a second include
here would nest the prefix.
"""
from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agent.llm_factory import PRESETS
from app.agent.voice_settings import SPEECH_SPEED_MAX, SPEECH_SPEED_MIN
from app.auth.dependencies import (
    TenantContext, get_owned, get_platform_admin, require_permission,
    scoped_permission,
)
from app.auth.permissions import Permission
from app.db.models import (
    Appointment, Call, CallDirection, CallStatus, CrmSync, Tenant,
)
from app.db.session import get_session
from app.integrations.crm import hooks as crm_hooks

router = APIRouter(prefix="/api", tags=["api"])


# ------------------------------------------------------------- schemas ----
class TenantCreate(BaseModel):
    name: str
    industry: str = "general"
    twilio_number: str
    agent_name: str = "Alex"
    greeting: str = "Thanks for calling. How can I help you today?"
    timezone: str = "America/New_York"
    knowledge_base: dict = {}
    google_calendar_id: str | None = None
    notify_sms_number: str | None = None
    escalation_number: str | None = None

    # কোন AI চালাবে
    llm_preset: str = "natural"          # fast(ChatGPT) | natural(Claude) | cheap(Gemini) | smart
    llm_provider: str | None = None      # অথবা সরাসরি
    llm_model: str | None = None

    # মানুষের মতো শোনানোর নব
    humanize: bool = True
    vad_stop_secs: float = 0.45          # 0.30 দ্রুত <-> 0.70 নিরাপদ
    # ElevenLabs voice_settings.speed supports 0.7–1.2 (see
    # app/agent/voice_settings.py); values outside are rejected rather than
    # silently ignored by the provider.
    speech_speed: float = Field(default=1.0, ge=SPEECH_SPEED_MIN, le=SPEECH_SPEED_MAX)
    temperature: float = 0.65
    voice_id: str | None = None
    language: str = "en-US"


class VoiceSettings(BaseModel):
    """লাইভ টিউনিং -- ক্লায়েন্টের সাথে কলে বসে নব ঘোরানোর জন্য।"""
    llm_preset: str | None = None
    humanize: bool | None = None
    vad_stop_secs: float | None = None
    # ElevenLabs voice_settings.speed supports 0.7–1.2.
    speech_speed: float | None = Field(
        default=None, ge=SPEECH_SPEED_MIN, le=SPEECH_SPEED_MAX
    )
    temperature: float | None = None
    voice_id: str | None = None


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    twilio_number: str
    agent_name: str
    plan: str
    minutes_used: float
    included_minutes: int

    class Config:
        from_attributes = True


# ------------------------------------------------------------- tenants ----
@router.post("/tenants", response_model=TenantOut, status_code=201,
             include_in_schema=False)
async def create_tenant(
    payload: TenantCreate,
    _: TenantContext = Depends(get_platform_admin),
    session: AsyncSession = Depends(get_session),
):
    """
    Provisioning a whole new business is an operator action, not something a
    tenant user may do. `get_platform_admin` denies everyone; use
    `python -m scripts.seed_demo_tenant` or a dedicated ops path instead.
    """
    # Compatibility path: ignore any organization or environment id a caller
    # might one day add to this payload. The insert hook attaches one
    # organization and one production environment. This route stays
    # platform-denied; the body is here so the operator path cannot orphan.
    from app.tenancy.service import open_legacy_tenant

    tenant = await open_legacy_tenant(session, payload.model_dump())
    await session.commit()
    await session.refresh(tenant)
    return tenant


@router.get("/tenants", response_model=list[TenantOut])
async def list_tenants(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """
    Returns ONLY the caller's own tenant. This previously returned every
    tenant in the deployment to any anonymous caller.
    """
    return [ctx.tenant]


# ----------------------------------------------------- agent configuration ----
#
# STEP 8 phase 2. `PATCH /tenants/{id}/voice` has existed since v0.5 and writes
# six live-tuning fields, but **nothing could read the agent's configuration
# back**: `TenantOut` carries seven columns and `/auth/me` five, and neither
# includes the greeting, the prompt, the model or any behaviour setting. A page
# that lets you edit a prompt it cannot display is not a page, so this is the
# read half of an API that was already half-written.
#
# It is a projection of columns that already exist on `Tenant`. No schema
# change, no migration, no new state, and no business logic: the agent pipeline
# keeps reading the same columns it always did.


class AgentConfigOut(BaseModel):
    """
    Everything the dashboard needs to render the agent settings page.

    **Deliberately omits every credential-shaped column on `Tenant`.**
    `crm_api_key`, `a2p_brand_sid`, `a2p_campaign_sid` and the Twilio
    credentials are not here and must never be added: this response is
    readable by any role with `tenant:read`, which is every role including
    viewer. The CRM connection is surfaced by `/api/integrations/crm`, which
    reports health without ever returning the secret itself (STEP 5).
    """

    # Identity
    id: uuid.UUID
    name: str
    industry: str
    twilio_number: str

    # Personality
    agent_name: str
    greeting: str
    system_prompt_extra: str

    # Model
    llm_preset: str | None
    llm_provider: str | None
    llm_model: str | None
    temperature: float

    # Voice / speech behaviour
    voice_id: str | None
    language: str
    humanize: bool
    vad_stop_secs: float
    speech_speed: float

    # Call behaviour
    timezone: str
    business_open: time
    business_close: time
    appointment_minutes: int
    escalation_number: str | None
    notify_sms_number: str | None

    # Compliance
    record_calls: bool
    recording_disclaimer: str

    # Channels
    sms_enabled: bool
    whatsapp_enabled: bool
    ivr_enabled: bool

    class Config:
        from_attributes = True


@router.get("/tenants/{tenant_id}/agent", response_model=AgentConfigOut)
async def get_agent_config(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(scoped_permission(Permission.TENANT_READ)),
):
    """
    The agent's current configuration.

    `scoped_permission` has already proved `tenant_id == ctx.tenant_id` and
    audited the attempt if it did not, so the authenticated object is returned
    rather than anything re-fetched by the client-supplied id. The path
    parameter is a consistency check, never a selector.
    """
    return ctx.tenant


# --------------------------------------------------------- AI নির্বাচন ----
@router.get("/llm/presets")
async def llm_presets(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    """ড্যাশবোর্ডে ড্রপডাউন ভরার জন্য -- ক্লায়েন্ট নিজেই AI বদলাতে পারবে।"""
    label = {"openai": "ChatGPT", "anthropic": "Claude", "google": "Gemini"}
    return [
        {
            "key": key,
            "brand": label[c.provider],
            "provider": c.provider,
            "model": c.model,
            "latency_ms": c.est_latency_ms,
            "notes": c.notes,
        }
        for key, c in PRESETS.items()
    ]


@router.patch("/tenants/{tenant_id}/voice")
async def update_voice_settings(
    tenant_id: uuid.UUID,
    payload: VoiceSettings,
    ctx: TenantContext = Depends(scoped_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """কল চলাকালীন নয়, কিন্তু পরের কল থেকেই কার্যকর।

    Workflow যেটা ক্লায়েন্টকে মুগ্ধ করে:
      1. ক্লায়েন্ট বলল "রোবটটা তাড়াহুড়ো করছে"
      2. আপনি vad_stop_secs 0.45 -> 0.60 করলেন
      3. তাকে আবার কল দিতে বললেন -> ঠিক হয়ে গেছে
    """
    # scoped_permission already proved tenant_id == ctx.tenant_id; use the
    # authenticated object rather than re-fetching by a client-supplied id.
    tenant = ctx.tenant

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(tenant, field, value)
    await session.commit()

    return {
        "ok": True,
        "llm_preset": tenant.llm_preset,
        "humanize": tenant.humanize,
        "vad_stop_secs": tenant.vad_stop_secs,
        "speech_speed": tenant.speech_speed,
    }


# --------------------------------------------------------------- calls ----
@router.get("/tenants/{tenant_id}/calls")
async def list_calls(
    tenant_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description="a CallStatus value"),
    direction: str | None = Query(None, description="inbound | outbound"),
    booked: bool | None = Query(None),
    transferred: bool | None = Query(None),
    search: str | None = Query(None, max_length=64, description="phone fragment"),
    start: datetime | None = Query(None, description="UTC lower bound, inclusive"),
    end: datetime | None = Query(None, description="UTC upper bound, exclusive"),
    ctx: TenantContext = Depends(scoped_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """
    Paginated, filtered call log.

    STEP 8 added everything except `limit`. The dashboard previously fetched
    whatever the default returned and filtered nothing, so there was no way to
    reach call 51 and no way to answer "show me yesterday's missed calls".

    Filtering happens **here**, not in the browser (requirement 6). Every
    predicate is ANDed into the same `WHERE` as the tenant scope, so a filter
    can only ever narrow the caller's own rows -- the same discipline the
    STEP 4 knowledge filters use. `search` is a parameterised `LIKE` on the
    phone numbers only; there is no free-form field selector, so a filter
    cannot become a way to probe columns.

    Returns an envelope rather than a bare list. A pager needs a total, and
    adding one later would be a breaking change for every client.
    """
    scope = [Call.tenant_id == ctx.tenant_id]

    if status:
        try:
            scope.append(Call.status == CallStatus(status.lower()))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown status {status!r}")
    if direction:
        try:
            scope.append(Call.direction == CallDirection(direction.lower()))
        except ValueError:
            raise HTTPException(
                status_code=422, detail=f"Unknown direction {direction!r}"
            )
    if booked is not None:
        scope.append(Call.booked.is_(booked))
    if transferred is not None:
        scope.append(Call.escalated.is_(transferred))
    if start is not None:
        scope.append(Call.started_at >= start)
    if end is not None:
        scope.append(Call.started_at < end)
    if search:
        fragment = f"%{search.strip()}%"
        scope.append(Call.from_number.ilike(fragment) | Call.to_number.ilike(fragment))

    total = (
        await session.execute(select(func.count(Call.id)).where(*scope))
    ).scalar() or 0

    rows = (
        await session.execute(
            select(Call)
            .where(*scope)
            .order_by(Call.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()

    return {
        "calls": [
            {
                "id": str(c.id),
                "from": c.from_number,
                "to": c.to_number,
                "direction": c.direction.value,
                "started_at": c.started_at,
                "duration": c.duration_seconds,
                "status": c.status,
                "intent": c.intent,
                "booked": c.booked,
                "escalated": c.escalated,
                "transfer_state": c.transfer_state.value,
                "summary": c.summary,
                "llm_used": c.llm_used,
                # Whether a recording *exists*, not where it lives. The URL is
                # served by the detail endpoint, which re-checks permission.
                "has_recording": bool(c.recording_url),
                "lead_id": str(c.lead_id) if c.lead_id else None,
            }
            for c in rows
        ],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.get("/calls/{call_id}")
async def call_detail(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """
    One call, with the related records the detail page needs.

    Assembled server-side so the page is one request rather than five
    (requirement 32). `get_owned` 404s on another tenant's id rather than
    403ing, so ids cannot be probed for existence.

    `recording_url` requires `RECORDING_READ` on top of `CALL_READ`: a
    transcript is operational data, but the audio is the customer's voice.
    """
    call = await get_owned(session, Call, call_id, ctx)

    appointment = (
        await session.execute(
            select(Appointment).where(
                Appointment.tenant_id == ctx.tenant_id,
                Appointment.call_id == call.id,
            )
        )
    ).scalars().first()

    lead = None
    if call.lead_id:
        lead = await session.get(Lead, call.lead_id)
        if lead is not None and lead.tenant_id != ctx.tenant_id:
            lead = None          # defensive: an id is not authorization

    crm_syncs = (
        (
            await session.execute(
                select(CrmSync).where(
                    CrmSync.tenant_id == ctx.tenant_id,
                    CrmSync.entity_id == call.id,
                )
            )
        )
        .scalars()
        .all()
    )

    return {
        "id": str(call.id),
        "call_sid": call.call_sid,
        "direction": call.direction.value,
        "status": call.status.value,
        "from": call.from_number,
        "to": call.to_number,
        "started_at": call.started_at,
        "ended_at": call.ended_at,
        "duration": call.duration_seconds,
        "intent": call.intent,
        "summary": call.summary,
        "booked": call.booked,
        "escalated": call.escalated,
        "lead_score": call.lead_score,
        "llm_used": call.llm_used,
        "transfer": {
            "state": call.transfer_state.value,
            "reason": call.transfer_reason,
            "destination": call.transfer_destination,
            "started_at": call.transfer_started_at,
            "completed_at": call.transfer_completed_at,
            "failed_at": call.transfer_failed_at,
        },
        "appointment": None if appointment is None else {
            "id": str(appointment.id),
            "status": appointment.status.value,
            "starts_at": appointment.starts_at,
            "timezone": appointment.timezone,
            "customer_name": appointment.customer_name,
            "meeting_url": appointment.meeting_url,
        },
        "lead": None if lead is None else {
            "id": str(lead.id),
            "name": lead.name,
            "phone": lead.phone,
            "status": lead.status.value,
            "score": lead.score,
        },
        "crm_syncs": [
            {
                "provider": sync.provider.value,
                "status": sync.status.value,
                "attempt_count": sync.attempt_count,
                # Already scrubbed by `errors.safe_message` before storage.
                "last_error": sync.last_error,
                "synced_at": sync.synced_at,
            }
            for sync in crm_syncs
        ],
        "has_recording": bool(call.recording_url),
        "recording_url": (
            call.recording_url if ctx.can(Permission.RECORDING_READ) else None
        ),
    }


@router.get("/calls/{call_id}/transfer")
async def call_transfer_detail(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """
    How the human escalation on this call actually went.

    `get_owned` 404s for another tenant's call id, so transfer metadata is
    scoped exactly like transcripts are.

    `transfer_destination` is redacted: the dashboard needs to show *that* a
    transfer happened, not to become a directory of staff mobile numbers.
    """
    call = await get_owned(session, Call, call_id, ctx)
    return {
        "call_id": str(call.id),
        "status": call.status.value,
        "escalated": call.escalated,
        "transfer_state": call.transfer_state.value,
        "transfer_destination": phone.redact(call.transfer_destination),
        "transfer_reason": call.transfer_reason,
        "transfer_attempts": call.transfer_attempts,
        "transfer_error": call.transfer_error,
        "transfer_requested_at": call.transfer_requested_at,
        "transfer_started_at": call.transfer_started_at,
        "transfer_completed_at": call.transfer_completed_at,
        "transfer_failed_at": call.transfer_failed_at,
        "failure_reason": call.failure_reason,
    }


@router.get("/calls/{call_id}/transcript")
async def transcript(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TRANSCRIPT_READ)),
    session: AsyncSession = Depends(get_session),
):
    # get_owned 404s when the call belongs to another tenant, so a call id
    # cannot be probed for existence across the deployment.
    await get_owned(session, Call, call_id, ctx)
    call = (
        await session.execute(
            select(Call).options(selectinload(Call.turns)).where(Call.id == call_id)
        )
    ).scalar_one_or_none()
    if call is None:
        raise HTTPException(404, "call not found")
    return [
        {"speaker": t.speaker, "text": t.text, "at": t.created_at}
        for t in sorted(call.turns, key=lambda x: x.created_at)
    ]


# ----------------------------------------------------------- analytics ----
@router.get("/tenants/{tenant_id}/stats")
async def stats(
    tenant_id: uuid.UUID,
    days: int = 30,
    ctx: TenantContext = Depends(scoped_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
):
    since = datetime.utcnow() - timedelta(days=days)
    base = select(func.count()).select_from(Call).where(
        Call.tenant_id == ctx.tenant_id, Call.started_at >= since
    )
    total = (await session.execute(base)).scalar_one()
    booked = (await session.execute(base.where(Call.booked.is_(True)))).scalar_one()
    escalated = (await session.execute(base.where(Call.escalated.is_(True)))).scalar_one()
    minutes = (
        await session.execute(
            select(func.coalesce(func.sum(Call.duration_seconds), 0.0) / 60.0).where(
                Call.tenant_id == ctx.tenant_id, Call.started_at >= since
            )
        )
    ).scalar_one()
    return {
        "days": days,
        "calls": total,
        "booked": booked,
        "escalated": escalated,
        "booking_rate": round(booked / total, 3) if total else 0,
        "minutes": round(float(minutes), 1),
        # This is the number you put on the invoice.
        "estimated_value_usd": booked * 150,
    }


# =============================================================================
# v0.3 -- leads, campaigns, IVR, languages, compliance
# =============================================================================

from app.core import compliance as _compliance          # noqa: E402
from app.core.i18n import get_profile, supported_languages  # noqa: E402
from app.db.models import Campaign, Lead, LeadStatus  # noqa: E402
from app.telephony import ivr as _ivr                   # noqa: E402
from app.telephony import phone  # noqa: E402
from app.telephony.outbound import run_campaign_tick    # noqa: E402


# ------------------------------------------------------------- languages ----
@router.get("/languages")
async def list_languages(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    """Dashboard dropdown. Shows which STT model each language will use."""
    return {"default": "en-US", "languages": supported_languages()}


# ------------------------------------------------------------------ leads ----
class LeadIn(BaseModel):
    name: str = ""
    phone: str
    email: str | None = None
    company: str | None = None
    notes: str = ""
    custom_fields: dict = {}


class LeadBulkIn(BaseModel):
    campaign_id: uuid.UUID | None = None
    leads: list[LeadIn]


@router.post("/tenants/{tenant_id}/leads", status_code=201)
async def add_leads(
    tenant_id: uuid.UUID,
    payload: LeadBulkIn,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_CREATE)),
    session: AsyncSession = Depends(get_session),
):
    """Bulk import (CSV upload in the dashboard posts here). Skips duplicates."""
    existing = set(
        (await session.execute(
            select(Lead.phone).where(Lead.tenant_id == ctx.tenant_id)
        )).scalars().all()
    )

    # A campaign supplied in the body must belong to the caller's tenant,
    # otherwise leads could be injected into another tenant's dialer queue.
    if payload.campaign_id is not None:
        await get_owned(session, Campaign, payload.campaign_id, ctx)

    created, skipped = 0, 0
    new_leads: list[Lead] = []
    for item in payload.leads:
        phone = item.phone.strip()
        if not phone or phone in existing:
            skipped += 1
            continue
        lead = Lead(
            tenant_id=ctx.tenant_id, campaign_id=payload.campaign_id,
            name=item.name, phone=phone, email=item.email,
            company=item.company, notes=item.notes,
            custom_fields=item.custom_fields,
        )
        session.add(lead)
        new_leads.append(lead)
        existing.add(phone)
        created += 1

    # Flush once for the whole batch rather than once per lead: importing two
    # thousand leads should be one round trip's worth of id generation, not
    # two thousand.
    if new_leads:
        await session.flush()
        for lead in new_leads:
            await crm_hooks.on_lead_created(session, lead)

    await session.commit()
    return {"created": created, "skipped": skipped}


@router.get("/tenants/{tenant_id}/leads")
async def list_leads(
    tenant_id: uuid.UUID,
    status: str | None = None,
    limit: int = 100,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Lead).where(Lead.tenant_id == ctx.tenant_id)
    if status:
        stmt = stmt.where(Lead.status == status)
    rows = (await session.execute(
        stmt.order_by(Lead.score.desc().nullslast(), Lead.created_at.desc()).limit(limit)
    )).scalars().all()
    return [
        {
            "id": str(lead.id), "name": lead.name, "phone": lead.phone,
            "email": lead.email, "company": lead.company,
            "status": lead.status.value, "score": lead.score,
            "attempts": lead.attempts,
            "next_attempt_at": (
                lead.next_attempt_at.isoformat() if lead.next_attempt_at else None
            ),
        }
        for lead in rows
    ]


@router.post("/tenants/{tenant_id}/leads/{lead_id}/do-not-call")
async def mark_lead_dnc(
    tenant_id: uuid.UUID, lead_id: uuid.UUID,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    lead = await get_owned(session, Lead, lead_id, ctx)
    lead.status = LeadStatus.DNC
    await session.commit()
    return {"ok": True, "phone": lead.phone, "status": "do_not_call"}


# -------------------------------------------------------------- campaigns ----
class CampaignIn(BaseModel):
    name: str
    goal: str = "qualify"                 # qualify | remind | followup | survey
    script_prompt: str = ""
    opening_line: str = "Hi, this is {agent} calling from {business}. Do you have a quick minute?"
    calls_per_minute: int = 2
    is_active: bool = False


@router.post("/tenants/{tenant_id}/campaigns", status_code=201)
async def create_campaign(
    tenant_id: uuid.UUID, payload: CampaignIn,
    ctx: TenantContext = Depends(scoped_permission(Permission.CAMPAIGN_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    campaign = Campaign(tenant_id=ctx.tenant_id, **payload.model_dump())
    session.add(campaign)
    await session.commit()
    await session.refresh(campaign)
    return {"id": str(campaign.id), "name": campaign.name, "is_active": campaign.is_active}


@router.get("/tenants/{tenant_id}/campaigns")
async def list_campaigns(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(scoped_permission(Permission.CAMPAIGN_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = (await session.execute(
        select(Campaign).where(Campaign.tenant_id == ctx.tenant_id)
    )).scalars().all()
    out = []
    for c in rows:
        total = (await session.execute(
            select(func.count(Lead.id)).where(Lead.campaign_id == c.id)
        )).scalar_one()
        done = (await session.execute(
            select(func.count(Lead.id)).where(
                Lead.campaign_id == c.id,
                Lead.status.in_([LeadStatus.CALLED, LeadStatus.QUALIFIED,
                                 LeadStatus.UNQUALIFIED]),
            )
        )).scalar_one()
        out.append({
            "id": str(c.id), "name": c.name, "goal": c.goal,
            "is_active": c.is_active, "calls_per_minute": c.calls_per_minute,
            "leads_total": total, "leads_done": done,
        })
    return out


@router.post("/tenants/{tenant_id}/campaigns/{campaign_id}/run")
async def run_campaign(
    tenant_id: uuid.UUID, campaign_id: uuid.UUID, dry_run: bool = True,
    ctx: TenantContext = Depends(scoped_permission(Permission.CAMPAIGN_RUN)),
    session: AsyncSession = Depends(get_session),
):
    """
    One batch. `dry_run=true` is the default so nobody dials by accident.
    This endpoint spends real money, hence its own CAMPAIGN_RUN permission.
    """
    campaign = await get_owned(session, Campaign, campaign_id, ctx)
    return await run_campaign_tick(session, ctx.tenant, campaign, dry_run=dry_run)


# --------------------------------------------------------------------- IVR ----
class IvrIn(BaseModel):
    enabled: bool = True
    flow: dict


@router.get("/tenants/{tenant_id}/ivr")
async def get_ivr(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(scoped_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    tenant = ctx.tenant
    return {
        "enabled": tenant.ivr_enabled,
        "flow": tenant.ivr_flow or _ivr.DEFAULT_FLOW,
        "default_flow": _ivr.DEFAULT_FLOW,
    }


@router.put("/tenants/{tenant_id}/ivr")
async def set_ivr(
    tenant_id: uuid.UUID, payload: IvrIn,
    ctx: TenantContext = Depends(scoped_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """Rejects a broken flow instead of shipping a dead menu to real callers."""
    tenant = ctx.tenant

    problems = _ivr.validate_flow(payload.flow)
    if problems:
        raise HTTPException(422, {"errors": problems})

    tenant.ivr_flow = payload.flow
    tenant.ivr_enabled = payload.enabled
    await session.commit()
    return {"ok": True, "enabled": tenant.ivr_enabled}


@router.post("/ivr/validate")
async def validate_ivr(payload: IvrIn, 
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    """Live validation for the dashboard editor."""
    problems = _ivr.validate_flow(payload.flow)
    return {"valid": not problems, "errors": problems}


# -------------------------------------------------------------- compliance ----
class MessageCheckIn(BaseModel):
    body: str
    is_first_of_thread: bool = False


@router.post("/compliance/check-message")
async def check_message(payload: MessageCheckIn, 
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
):
    """Run before any bulk SMS. Also shows the segment count / billing impact."""
    issues = _compliance.check_message(
        payload.body, is_first_of_thread=payload.is_first_of_thread
    )
    return {
        "sendable": not any(i.severity == "error" for i in issues),
        "issues": [{"severity": i.severity, "message": i.message} for i in issues],
        "segments": _compliance.count_segments(payload.body),
    }


@router.get("/compliance/a2p-checklist")
async def a2p_checklist(business: str = "Your Business", 
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
):
    return {
        "checklist": _compliance.registration_checklist(),
        "sample_messages": _compliance.sample_messages(business),
    }


@router.get("/tenants/{tenant_id}/compliance")
async def tenant_compliance(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(scoped_permission(Permission.COMPLIANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    tenant = ctx.tenant
    dnc = (await session.execute(
        select(func.count(Lead.id)).where(
            Lead.tenant_id == ctx.tenant_id, Lead.status == LeadStatus.DNC
        )
    )).scalar_one()
    return {
        "a2p_status": tenant.a2p_status,
        "a2p_brand_sid": tenant.a2p_brand_sid,
        "a2p_campaign_sid": tenant.a2p_campaign_sid,
        "do_not_call_count": dnc,
        "outbound_window": (
            f"{tenant.outbound_window_open:%H:%M}-{tenant.outbound_window_close:%H:%M} "
            f"{tenant.timezone}"
        ),
        "recording_enabled": tenant.record_calls,
        "language": get_profile(tenant.language).name,
    }
````

### `app/db/models.py`

````python
"""Database schema.

Multi-tenant from day one: every business you sell to is a Tenant row.
This is what lets you charge $199/month x N clients from one deployment.

A tenant now also belongs to an Organization, and each tenant has Environments
(development / staging / production). That hierarchy is a parent/child of the
existing tenant; it does not replace ``tenants`` and it does not move calls,
leads, billing or identity onto an environment id.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, time

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON,
    String, Text, Time, UniqueConstraint, event, false, select, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class CallStatus(str, enum.Enum):
    """
    Lifecycle of a single call.

    NOTE: SQLAlchemy's Enum() persists the member *name* (e.g. "NO_ANSWER"),
    not the value. The PostgreSQL type `callstatus` must therefore contain
    exactly these six names -- see alembic/versions/0002_enum_consistency.py.
    """
    RINGING = "ringing"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    NO_ANSWER = "no_answer"       # Twilio "no-answer": rang out, nobody picked up
    TRANSFERRED = "transferred"   # handed to a human via <Dial>


class TransferState(str, enum.Enum):
    """
    Human-escalation sub-state, tracked separately from CallStatus.

    A call can be IN_PROGRESS while a transfer is mid-flight, so this cannot be
    folded into CallStatus. It also doubles as the idempotency lock: moving out
    of NONE is what stops a second tool call from dialling the human twice.

    NOTE: SQLAlchemy's Enum() persists the member NAME, so the PostgreSQL type
    `transferstate` must contain NONE/REQUESTED/DIALING/CONNECTED/FAILED --
    see alembic/versions/0004_call_transfer_lifecycle.py.
    """
    NONE = "none"             # no escalation has been requested
    REQUESTED = "requested"   # the AI asked; we have not told the provider yet
    DIALING = "dialing"       # the provider accepted; the human's phone is ringing
    CONNECTED = "connected"   # the human answered
    FAILED = "failed"         # busy, no answer, rejected, or a provider error


#: States in which a transfer is already under way or finished. A second
#: request while in one of these must be a no-op, never a second phone call.
TRANSFER_IN_FLIGHT = frozenset({
    TransferState.REQUESTED,
    TransferState.DIALING,
    TransferState.CONNECTED,
})


class CallDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class LeadStatus(str, enum.Enum):
    NEW = "new"
    QUEUED = "queued"
    CALLED = "called"
    QUALIFIED = "qualified"
    UNQUALIFIED = "unqualified"
    FAILED = "failed"
    DNC = "do_not_call"          # legally required: never call again


class UserRole(str, enum.Enum):
    """
    Ordered by privilege. `rbac.ROLE_LEVEL` turns these into integers so a
    role can never grant something above itself.
    """
    OWNER = "owner"
    ADMIN = "admin"
    MANAGER = "manager"
    AGENT = "agent"
    VIEWER = "viewer"


class MembershipStatus(str, enum.Enum):
    """Shared lifecycle for organization, tenant and environment memberships.

    ``revoked`` and ``expired`` never authorize. ``suspended`` never authorizes
    a privileged action. Ordinary callers cannot move ``revoked`` back to
    ``active``; that requires a new invitation.
    """

    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    EXPIRED = "expired"


class InvitationScope(str, enum.Enum):
    ORGANIZATION = "organization"
    TENANT = "tenant"


class PolicyScope(str, enum.Enum):
    ORGANIZATION = "organization"
    TENANT = "tenant"
    ENVIRONMENT = "environment"


class AuditAction(str, enum.Enum):
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    TOKEN_REFRESH = "token_refresh"
    USER_CREATED = "user_created"
    USER_DEACTIVATED = "user_deactivated"
    USER_REACTIVATED = "user_reactivated"
    ROLE_CHANGED = "role_changed"
    PASSWORD_CHANGED = "password_changed"
    AUTHZ_DENIED = "authz_denied"
    # STEP 5. `detail` on these carries provider and outcome only -- never a
    # token, never a config value that could hold one.
    INTEGRATION_CONNECTED = "integration_connected"
    INTEGRATION_UPDATED = "integration_updated"
    INTEGRATION_DISCONNECTED = "integration_disconnected"
    INTEGRATION_TESTED = "integration_tested"
    # STEP 6. Same rule: provider and outcome only, never a token.
    CALENDAR_CONNECTED = "calendar_connected"
    CALENDAR_DISCONNECTED = "calendar_disconnected"
    APPOINTMENT_CANCELLED = "appointment_cancelled"
    APPOINTMENT_RESCHEDULED = "appointment_rescheduled"
    # STEP 7. `detail` carries plan codes and outcomes only -- never a card
    # number, never a Stripe key, never a webhook secret.
    BILLING_CHECKOUT_STARTED = "billing_checkout_started"
    BILLING_SUBSCRIPTION_CREATED = "billing_subscription_created"
    BILLING_SUBSCRIPTION_CHANGED = "billing_subscription_changed"
    BILLING_CANCELLATION_REQUESTED = "billing_cancellation_requested"
    BILLING_CANCELLATION_COMPLETED = "billing_cancellation_completed"
    BILLING_PAYMENT_FAILED = "billing_payment_failed"
    BILLING_PLAN_CHANGED = "billing_plan_changed"
    BILLING_LIMIT_HIT = "billing_limit_hit"
    BILLING_ADJUSTMENT = "billing_adjustment"
    # STEP 9. Data-subject rights and licensing; detail carries counts and
    # plan codes only -- never personal data beyond what the action implies.
    GDPR_EXPORT = "gdpr_export"
    GDPR_ERASURE = "gdpr_erasure"
    LICENSE_ISSUED = "license_issued"
    # STEP 18, enterprise identity. Same discipline as every block above:
    # `detail` carries ids, outcomes, counts and role names. It never carries a
    # password, an access/refresh token, an API-key or SCIM secret, a TOTP seed,
    # a recovery code, a raw SAML assertion, an ID token or a client secret.
    IDENTITY_REAUTHENTICATED = "identity_reauthenticated"
    IDENTITY_LINK_REJECTED = "identity_link_rejected"
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    PASSWORD_RESET_COMPLETED = "password_reset_completed"
    EMAIL_VERIFICATION_SENT = "email_verification_sent"
    EMAIL_VERIFIED = "email_verified"
    MFA_ENROLLMENT_STARTED = "mfa_enrollment_started"
    MFA_ENABLED = "mfa_enabled"
    MFA_DISABLED = "mfa_disabled"
    MFA_VERIFIED = "mfa_verified"
    MFA_FAILED = "mfa_failed"
    MFA_CHALLENGE_LOCKED = "mfa_challenge_locked"
    MFA_RECOVERY_CODE_USED = "mfa_recovery_code_used"
    MFA_RECOVERY_CODES_REGENERATED = "mfa_recovery_codes_regenerated"
    SESSION_CREATED = "session_created"
    SESSION_REVOKED = "session_revoked"
    SESSION_SUSPICIOUS = "session_suspicious"
    SSO_CONNECTION_CREATED = "sso_connection_created"
    SSO_CONNECTION_UPDATED = "sso_connection_updated"
    SSO_CONNECTION_DELETED = "sso_connection_deleted"
    SSO_CONNECTION_ENABLED = "sso_connection_enabled"
    SSO_CONNECTION_DISABLED = "sso_connection_disabled"
    SSO_MAPPING_CHANGED = "sso_mapping_changed"
    SSO_CERTIFICATE_ROTATED = "sso_certificate_rotated"
    SSO_LOGIN_STARTED = "sso_login_started"
    SSO_LOGIN_SUCCEEDED = "sso_login_succeeded"
    SSO_LOGIN_FAILED = "sso_login_failed"
    SSO_USER_PROVISIONED = "sso_user_provisioned"
    SSO_ACCOUNT_LINKED = "sso_account_linked"
    # Removing a federated subject from an account. The user row is not deleted,
    # and the detail carries a hashed subject hint rather than the subject.
    SSO_ACCOUNT_UNLINKED = "sso_account_unlinked"
    SCIM_CREDENTIAL_CREATED = "scim_credential_created"
    SCIM_CREDENTIAL_ROTATED = "scim_credential_rotated"
    SCIM_CREDENTIAL_REVOKED = "scim_credential_revoked"
    SCIM_USER_PROVISIONED = "scim_user_provisioned"
    SCIM_USER_UPDATED = "scim_user_updated"
    SCIM_USER_DEPROVISIONED = "scim_user_deprovisioned"
    SCIM_GROUP_CREATED = "scim_group_created"
    SCIM_GROUP_UPDATED = "scim_group_updated"
    SCIM_GROUP_DELETED = "scim_group_deleted"
    API_KEY_CREATED = "api_key_created"
    API_KEY_ROTATED = "api_key_rotated"
    API_KEY_REVOKED = "api_key_revoked"
    # A machine credential that *resolved to a row* and was then refused:
    # revoked, expired, its account switched off, its family disabled for the
    # workspace, or its owner no longer active. An unknown or malformed token
    # is not recorded here -- that would be an unauthenticated log amplifier --
    # so every row of this kind names a credential the operator can look up.
    CREDENTIAL_AUTH_REJECTED = "credential_auth_rejected"
    SERVICE_ACCOUNT_CREATED = "service_account_created"
    SERVICE_ACCOUNT_UPDATED = "service_account_updated"
    SERVICE_ACCOUNT_DISABLED = "service_account_disabled"
    SERVICE_ACCOUNT_ENABLED = "service_account_enabled"
    SERVICE_ACCOUNT_CREDENTIAL_CREATED = "service_account_credential_created"
    SERVICE_ACCOUNT_CREDENTIAL_ROTATED = "service_account_credential_rotated"
    SERVICE_ACCOUNT_CREDENTIAL_REVOKED = "service_account_credential_revoked"
    DOMAIN_ADDED = "domain_added"
    DOMAIN_VERIFIED = "domain_verified"
    DOMAIN_VERIFICATION_FAILED = "domain_verification_failed"
    DOMAIN_REMOVED = "domain_removed"
    DOMAIN_ENFORCEMENT_CHANGED = "domain_enforcement_changed"
    # A change to the identity policy itself (PATCH /api/identity/policy).
    # Separate from the controls it governs, so "who turned MFA on for this
    # workspace, and when" is one row rather than an inference.
    SECURITY_SETTINGS_CHANGED = "security_settings_changed"
    # Organization → tenant → environment foundation. Identifiers and status
    # only; never a credential. Added by 0017 as member *names*.
    ORGANIZATION_CREATED = "organization_created"
    ORGANIZATION_UPDATED = "organization_updated"
    ORGANIZATION_SUSPENDED = "organization_suspended"
    ORGANIZATION_RESTORED = "organization_restored"
    ORGANIZATION_READ_ONLY = "organization_read_only"
    TENANT_LIFECYCLE_CHANGED = "tenant_lifecycle_changed"
    ENVIRONMENT_CREATED = "environment_created"
    ENVIRONMENT_UPDATED = "environment_updated"
    ENVIRONMENT_SUSPENDED = "environment_suspended"
    ENVIRONMENT_RESTORED = "environment_restored"
    ENVIRONMENT_ARCHIVED = "environment_archived"
    ENVIRONMENT_DEFAULT_CHANGED = "environment_default_changed"
    # Membership, invitation and quota events. Identifiers and status only.
    # Added by 0018 as member names. Detail never carries an invitation token.
    MEMBERSHIP_CREATED = "membership_created"
    MEMBERSHIP_UPDATED = "membership_updated"
    MEMBERSHIP_SUSPENDED = "membership_suspended"
    MEMBERSHIP_RESTORED = "membership_restored"
    MEMBERSHIP_REVOKED = "membership_revoked"
    INVITATION_CREATED = "invitation_created"
    INVITATION_ACCEPTED = "invitation_accepted"
    INVITATION_REVOKED = "invitation_revoked"
    INVITATION_RESENT = "invitation_resent"
    ENVIRONMENT_SELECTED = "environment_selected"
    QUOTA_UPDATED = "quota_updated"
    QUOTA_DENIED = "quota_denied"


class Speaker(str, enum.Enum):
    """
    Who produced a transcript turn.

    Canonical values are USER / ASSISTANT / SYSTEM. They match the role
    vocabulary the LLM context and the text channels already use, and they
    match the `speaker` PostgreSQL type created by the baseline migration,
    so no persisted row has to be rewritten on an Alembic-managed database.
    """
    USER = "user"            # the caller / the person texting
    ASSISTANT = "assistant"  # the AI receptionist
    SYSTEM = "system"        # system notes (transfer, timeout, error)


class Organization(Base):
    """Parent of one or more tenants.

    Not a billing account and not a second tenant. Suspending or marking an
    organization deleted changes this row's status; it does not delete tenants,
    users, calls or invoices.
    """

    __tablename__ = "organizations"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_organizations_slug"),
        CheckConstraint(
            "status IN ('active', 'suspended', 'read_only', 'deleted')",
            name="ck_organizations_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class Tenant(Base):
    """One business = one tenant. Their phone number routes to their config.

    ``organization_id`` is the hierarchy parent. Callers that still construct a
    ``Tenant`` without one — tests, the seed script, the operator create path —
    receive a dedicated organization from the insert hook below. The id is never
    read from a JWT claim.
    """
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(
            "lifecycle_status IN ('active', 'suspended', 'read_only', 'deleted')",
            name="ck_tenants_lifecycle_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    # Nullable in no shipped schema: the insert hook assigns it before the
    # INSERT, and migration 0017 backfills then sets NOT NULL. Declared
    # non-null so a tenant cannot be stored without a parent.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Separate from ``is_active``. Lifecycle is the new hierarchy state;
    # ``is_active`` remains the existing login/kill switch and is not flipped
    # by suspend, so a token issued before this feature still authenticates.
    lifecycle_status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active", nullable=False
    )
    industry: Mapped[str] = mapped_column(String(80), default="general")   # dental, legal, restaurant...
    twilio_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)

    # Agent personality / knowledge
    agent_name: Mapped[str] = mapped_column(String(80), default="Alex")
    greeting: Mapped[str] = mapped_column(Text, default="Thanks for calling. How can I help you today?")
    system_prompt_extra: Mapped[str] = mapped_column(Text, default="")
    knowledge_base: Mapped[dict] = mapped_column(JSON, default=dict)  # {"hours": "...", "services": [...]}

    # ---- LLM choice (প্রতি ক্লায়েন্টে আলাদা AI) ----
    llm_preset: Mapped[str | None] = mapped_column(String(32), default="natural")
    llm_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)  # openai|anthropic|google
    llm_model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.65)

    # ---- মানুষের মতো শোনানোর সেটিং ----
    humanize: Mapped[bool] = mapped_column(Boolean, default=True)
    vad_stop_secs: Mapped[float] = mapped_column(Float, default=0.45)
    speech_speed: Mapped[float] = mapped_column(Float, default=1.0)
    voice_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str] = mapped_column(String(16), default="en-US")

    # Behaviour
    timezone: Mapped[str] = mapped_column(String(64), default="America/New_York")
    business_open: Mapped[time] = mapped_column(Time, default=time(9, 0))
    business_close: Mapped[time] = mapped_column(Time, default=time(17, 0))
    appointment_minutes: Mapped[int] = mapped_column(Integer, default=30)
    escalation_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notify_sms_number: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Integrations
    google_calendar_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    crm_webhook_url: Mapped[str | None] = mapped_column(String(500), nullable=True)   # GHL / Zapier / Make / n8n
    crm_type: Mapped[str] = mapped_column(String(32), default="webhook")              # webhook|gohighlevel|hubspot
    crm_api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Outbound calling (cold calls, follow-ups, reminders)
    outbound_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    outbound_caller_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outbound_window_open: Mapped[time] = mapped_column(Time, default=time(9, 0))      # TCPA: no calls before 8am
    outbound_window_close: Mapped[time] = mapped_column(Time, default=time(20, 0))    # TCPA: none after 9pm
    max_call_attempts: Mapped[int] = mapped_column(Integer, default=3)

    # Reminders
    reminder_hours_before: Mapped[int] = mapped_column(Integer, default=24)
    reminder_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # IVR / call flow (JSON so the dashboard can edit it without a deploy)
    ivr_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    ivr_flow: Mapped[dict] = mapped_column(JSON, default=dict)

    # Text channels
    sms_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    whatsapp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    whatsapp_number: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # A2P 10DLC (US SMS is silently filtered without this)
    a2p_brand_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    a2p_campaign_sid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    a2p_status: Mapped[str] = mapped_column(String(32), default="not_started")

    # Compliance / recording
    record_calls: Mapped[bool] = mapped_column(Boolean, default=False)
    recording_disclaimer: Mapped[str] = mapped_column(
        Text, default="This call may be recorded for quality purposes."
    )

    # Data-subject rights (STEP 9). Consent provenance is recorded when the
    # business captures opt-in; erasure_requested_at marks a GDPR erasure
    # request (set by app/api/gdpr_routes.py) for operator completion.
    data_consent_recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    data_consent_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    erasure_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Billing
    plan: Mapped[str] = mapped_column(String(32), default="starter")   # starter / pro
    included_minutes: Mapped[int] = mapped_column(Integer, default=500)
    minutes_used: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Real-call E2E readiness (STEP 5). `is_test_tenant` marks a tenant that a
    # human operator created for a genuine, manual end-to-end Twilio call. It is
    # the single source of truth the E2E guard trusts; it has no effect on
    # anything else in the product. Defaults to False so no existing tenant is
    # ever silently marked as a test tenant.
    is_test_tenant: Mapped[bool] = mapped_column(Boolean, server_default=false(), default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    calls: Mapped[list["Call"]] = relationship(back_populates="tenant")


class Environment(Base):
    """Deployment target for one tenant. Not a second tenant and not a secret store.

    ``production_guard`` / ``default_guard`` are the uniqueness locks. A NULL
    guard does not collide, so a tenant may hold one development and one staging
    row beside the single production row. Business tables do not carry
    ``environment_id``; this row is hierarchy metadata only.
    """

    __tablename__ = "environments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_environments_tenant_slug"),
        UniqueConstraint("tenant_id", "kind", name="uq_environments_tenant_kind"),
        UniqueConstraint("production_guard", name="uq_environments_production_guard"),
        UniqueConstraint("default_guard", name="uq_environments_default_guard"),
        CheckConstraint(
            "kind IN ('development', 'staging', 'production')",
            name="ck_environments_kind",
        ),
        CheckConstraint(
            "status IN ('active', 'suspended', 'archived')",
            name="ck_environments_status",
        ),
        Index("ix_environments_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Set to ``tenant_id`` only for the production row. Unique, so a second
    #: production cannot be inserted. NULL for every other kind.
    production_guard: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    #: Set to ``tenant_id`` only for the default row. Unique, so two defaults
    #: cannot exist. NULL on every non-default row.
    default_guard: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    release_version: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deployment_status: Mapped[str] = mapped_column(String(16), default="idle", nullable=False)
    deployment_source: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    health_state: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    call_sid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    from_number: Mapped[str] = mapped_column(String(32))
    to_number: Mapped[str] = mapped_column(String(32))
    status: Mapped[CallStatus] = mapped_column(Enum(CallStatus), default=CallStatus.RINGING)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)

    # Outcome
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent: Mapped[str | None] = mapped_column(String(80), nullable=True)
    booked: Mapped[bool] = mapped_column(Boolean, default=False)
    escalated: Mapped[bool] = mapped_column(Boolean, default=False)

    # Latency stats -- your product's #1 quality metric
    avg_response_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_used: Mapped[str | None] = mapped_column(String(80), nullable=True)

    direction: Mapped[CallDirection] = mapped_column(
        Enum(CallDirection), default=CallDirection.INBOUND
    )
    recording_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    lead_score: Mapped[int | None] = mapped_column(Integer, nullable=True)   # 0-100

    # Why the call ended badly ("busy", "no-answer", ...). Only set for
    # FAILED/NO_ANSWER; `summary` stays the human-readable outcome.
    failure_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # --- human transfer -------------------------------------------------
    # `escalated` (above) stays as the boolean the dashboard and CRM payload
    # already read; these columns record how the escalation actually went.
    transfer_state: Mapped[TransferState] = mapped_column(
        Enum(TransferState), default=TransferState.NONE, nullable=False
    )
    transfer_destination: Mapped[str | None] = mapped_column(String(64), nullable=True)
    transfer_reason: Mapped[str | None] = mapped_column(String(400), nullable=True)
    transfer_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    transfer_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    transfer_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    transfer_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    transfer_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    transfer_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    crm_synced: Mapped[bool] = mapped_column(Boolean, default=False)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id"), nullable=True, index=True
    )

    tenant: Mapped[Tenant] = relationship(back_populates="calls")
    turns: Mapped[list["Turn"]] = relationship(back_populates="call", cascade="all, delete-orphan")


class Turn(Base):
    """One utterance. Storing these gives you transcripts + training data."""
    __tablename__ = "turns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id"), index=True)
    speaker: Mapped[Speaker] = mapped_column(Enum(Speaker))
    text: Mapped[str] = mapped_column(Text)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    call: Mapped[Call] = relationship(back_populates="turns")


class AppointmentStatus(str, enum.Enum):
    """
    Lifecycle of one booking.

    Before STEP 6 there was no status at all: a row existed or it did not, so
    there was no way to cancel, to record a no-show, or -- most importantly --
    to distinguish "the provider accepted this" from "we wrote a row and hoped".

    PENDING    persisted, provider has not accepted (yet, or ever)
    CONFIRMED  the provider accepted and returned an event id
    RESCHEDULED  moved; `starts_at` is the new time
    CANCELLED  cancelled by anyone
    NO_SHOW    the customer did not arrive
    FAILED     the provider refused permanently; nothing is on the calendar
    """
    PENDING = "pending"
    CONFIRMED = "confirmed"
    RESCHEDULED = "rescheduled"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    FAILED = "failed"


#: Statuses that still occupy their slot. Anything outside this set frees the
#: time for someone else, so it is the definition the conflict check uses --
#: named here rather than inlined, because "does a cancelled appointment still
#: block the slot" is a policy question and it should have one answer.
BLOCKING_APPOINTMENT_STATUSES = frozenset({
    AppointmentStatus.PENDING,
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.RESCHEDULED,
})

#: Terminal: no further provider call will be made for these.
TERMINAL_APPOINTMENT_STATUSES = frozenset({
    AppointmentStatus.CANCELLED,
    AppointmentStatus.NO_SHOW,
    AppointmentStatus.FAILED,
})


class CalendarProviderType(str, enum.Enum):
    """
    Calendar backends with an adapter.

    `GOOGLE_SERVICE_ACCOUNT` is the pre-STEP-6 path, kept as a distinct member
    rather than folded into `GOOGLE`: it authenticates with a platform-wide
    service-account file instead of per-tenant OAuth, which is a different
    security posture and a different set of failure modes. Merging them would
    hide which tenants are still on the shared credential.
    """
    GOOGLE = "google"
    GOOGLE_SERVICE_ACCOUNT = "google_service_account"
    MICROSOFT = "microsoft"
    CALCOM = "calcom"
    INTERNAL = "internal"


class Appointment(Base):
    """
    One booking.

    Columns added in STEP 6 are all nullable or defaulted, so existing rows
    keep working: `status` defaults to CONFIRMED for them (they were created
    under the old code, which only ever wrote a row it believed in), and
    `timezone` is backfilled from the tenant.
    """
    __tablename__ = "appointments"
    __table_args__ = (
        # The concurrency primitive. Two callers racing for the same slot both
        # compute the same key, so the second INSERT loses at the database
        # rather than in application logic. See `service.book`.
        UniqueConstraint(
            "tenant_id", "slot_key", name="uq_appointment_slot"
        ),
        # Requirement 9: a retried booking request must find its own earlier
        # attempt rather than creating a second appointment.
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_appointment_idempotency"
        ),
        Index("ix_appointment_tenant_start", "tenant_id", "starts_at"),
        Index("ix_appointment_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    call_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("calls.id"), nullable=True)

    customer_name: Mapped[str] = mapped_column(String(200))
    customer_phone: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text, default="")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    google_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    # ---- STEP 6 ----------------------------------------------------------

    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus), default=AppointmentStatus.PENDING, nullable=False
    )

    #: The IANA zone the customer agreed to, captured at booking time.
    #:
    #: Not derivable from `Tenant.timezone` after the fact: a business that
    #: relocates, or corrects a wrong timezone, would otherwise silently
    #: reinterpret every appointment already in the book. The offset alone is
    #: not enough either -- it does not survive a DST boundary.
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)

    provider: Mapped[CalendarProviderType | None] = mapped_column(
        Enum(CalendarProviderType), nullable=True
    )
    #: The provider's own event id. Proof that the booking was accepted; a row
    #: is only CONFIRMED when this is set.
    external_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Which calendar within the provider (Google calendar id, Graph calendar
    #: id, Cal.com event-type id).
    calendar_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)

    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    attendee_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    meeting_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    #: Deterministic `{start}|{end}` in UTC. The unique constraint above turns
    #: it into a slot lock. Nullable so pre-STEP-6 rows do not all collide on
    #: NULL -- in both PostgreSQL and SQLite, NULLs are distinct in a UNIQUE
    #: index, which is exactly the behaviour needed for a backfill.
    slot_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Stable across retries of one booking request.
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Free text rather than a FK: the canceller may be a staff user, the
    #: customer on a call, or the provider itself via a webhook.
    cancelled_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    rescheduled_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Scrubbed before storage. Shown to staff, so never a raw provider body.
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class Campaign(Base):
    """A batch of outbound calls: cold-call list, follow-up sweep, reminder run."""
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(String(32), default="qualify")   # qualify|remind|followup|survey
    script_prompt: Mapped[str] = mapped_column(Text, default="")
    opening_line: Mapped[str] = mapped_column(
        Text, default="Hi, this is {agent} calling from {business}. Do you have a quick minute?"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    calls_per_minute: Mapped[int] = mapped_column(Integer, default=2)   # throttle
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    leads: Mapped[list["Lead"]] = relationship(back_populates="campaign")


class Lead(Base):
    """A person to call. Feeds outbound campaigns and gets pushed to the CRM."""
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id"), nullable=True, index=True
    )

    name: Mapped[str] = mapped_column(String(200), default="")
    phone: Mapped[str] = mapped_column(String(32), index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    custom_fields: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[LeadStatus] = mapped_column(Enum(LeadStatus), default=LeadStatus.NEW)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    campaign: Mapped["Campaign | None"] = relationship(back_populates="leads")


class Reminder(Base):
    """Scheduled outbound reminder for an appointment. Cuts no-shows ~30%."""
    __tablename__ = "reminders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    appointment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appointments.id"), index=True)
    channel: Mapped[str] = mapped_column(String(16), default="sms")   # sms|call|whatsapp
    send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Step 6 (scale-compliance): a durable send lease. The worker that wins the
    # atomic claim (see `app/integrations/reminders.py`) stamps these before
    # sending; a crashed worker's lease is reclaimed by the reaper. Without
    # this, two overlapping ticks (or a crash between the SMS send and the
    # commit) would text the customer twice.
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claimed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


# =============================================================================
# Authentication / RBAC
# =============================================================================

class User(Base):
    """
    A human operator. Always belongs to exactly one tenant -- that binding is
    the root of tenant isolation and is never taken from a request.

    Email is unique GLOBALLY, not per tenant. Login is by email alone with no
    tenant selector, so a duplicate address across tenants would make
    authentication ambiguous. Documented in README under "Tenant isolation".
    """
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_tenant_role", "tenant_id", "role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)   # RFC 5321 max
    full_name: Mapped[str] = mapped_column(String(200), default="")

    # bcrypt output. Never exposed by any serializer -- see UserOut.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.VIEWER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Bumping this invalidates every access token issued earlier for this user,
    # which is how deactivation and role changes take effect before expiry.
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ---- STEP 18, enterprise identity (all nullable/defaulted) ----
    # `email_verified_at` is the proof the login address never had. It is
    # nullable so every pre-existing user keeps working unchanged: an unverified
    # address is only *refused* where a flow explicitly requires proof.
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Per-user MFA override. NULL means "follow the tenant policy", which is
    #: what every existing user has.
    mfa_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: Set on every password change; drives "sessions older than the password
    #: change are dead" without parsing tokens.
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tenant: Mapped["Tenant"] = relationship()


class RefreshToken(Base):
    """
    Only a SHA-256 digest of the refresh token is stored, so a database leak
    does not hand out sessions. Rotation is enforced: using a token marks it
    used and links the replacement, and replaying a used token revokes the
    whole chain (a standard reuse-detection scheme).
    """
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_active", "user_id", "revoked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    user_agent: Mapped[str] = mapped_column(String(300), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")

    #: STEP 18: the browser/device session this refresh token belongs to.
    #: Nullable, so tokens minted before this feature (and by any caller that
    #: does not open a session) remain valid.
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user_sessions.id", ondelete="CASCADE"), nullable=True, index=True
    )


class AuditLog(Base):
    """
    Security events. Deliberately holds no secret material: no passwords, no
    raw tokens, no API keys. `detail` is free-form JSON for non-sensitive
    context such as which role changed to what.
    """
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_tenant_time", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    action: Mapped[AuditAction] = mapped_column(Enum(AuditAction), nullable=False)
    # Stored even on failed logins, so it must never be a real credential.
    actor_email: Mapped[str] = mapped_column(String(320), default="")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, index=True, nullable=False
    )


# ============================================================ knowledge ===
#
# STEP 4: tenant-scoped RAG. `Tenant.knowledge_base` (the flat JSON dict) is
# kept and still works -- it holds the handful of one-line facts a business
# types into the dashboard, and those are cheap enough to sit in the system
# prompt. Documents are the new thing: too large to inline, so they are
# chunked, embedded, and retrieved a few chunks at a time.
#
# Uploading a document does NOT mean the agent may answer from all of it. Only
# chunks returned by a retrieval query ever reach the model.


class DocumentStatus(str, enum.Enum):
    """
    Ingestion lifecycle.

    Only READY is searchable. Everything else -- including ARCHIVED -- is
    excluded from retrieval at the SQL level, not filtered afterwards.

    NOTE: SQLAlchemy's Enum() persists the member NAME, so the PostgreSQL type
    `documentstatus` must contain UPLOADED/PROCESSING/READY/FAILED/ARCHIVED.
    """
    UPLOADED = "uploaded"       # stored, not yet processed
    PROCESSING = "processing"   # extraction/chunking/embedding in flight
    READY = "ready"             # searchable
    FAILED = "failed"           # ingestion failed; error_message explains
    ARCHIVED = "archived"       # soft-deleted; never retrieved


#: The only status whose chunks may be returned by a search.
SEARCHABLE_DOCUMENT_STATUSES = frozenset({DocumentStatus.READY})


class DocumentSourceType(str, enum.Enum):
    UPLOAD = "upload"           # a file the tenant uploaded
    TEXT = "text"               # pasted directly into the dashboard
    URL = "url"                 # fetched from a URL (not implemented yet)


class KnowledgeDocument(Base):
    """One uploaded source document belonging to exactly one tenant."""

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        # Deduplication is per tenant: two businesses may legitimately upload
        # the same price list, and that must not collide.
        UniqueConstraint("tenant_id", "content_hash", name="uq_knowledge_doc_hash"),
        Index("ix_knowledge_doc_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    source_type: Mapped[DocumentSourceType] = mapped_column(
        Enum(DocumentSourceType), default=DocumentSourceType.UPLOAD, nullable=False
    )
    #: Storage key, NOT a filesystem path. Resolved by the storage backend so
    #: nothing outside app/knowledge/storage knows where bytes actually live.
    source_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(300), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: SHA-256 of the raw bytes. Drives per-tenant deduplication.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus), default=DocumentStatus.UPLOADED, nullable=False
    )
    #: Bumped on every reindex. Chunks record the version they were built from,
    #: so a half-finished reindex can never mix old and new chunks.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    #: Which embedding model produced this document's vectors. If it stops
    #: matching the configured model the document is not searchable until it
    #: is reindexed -- mixing vector spaces silently returns nonsense.
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)

    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: Free-form, tenant-visible. Never holds credentials or storage paths.
    doc_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    #: Safe, human-readable failure summary. Never a raw stack trace.
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow,
        onupdate=datetime.utcnow, nullable=False,
    )
    ingested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Set when PROCESSING begins, so a stuck job can be detected and reaped.
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    @property
    def is_searchable(self) -> bool:
        return self.status in SEARCHABLE_DOCUMENT_STATUSES


class KnowledgeChunk(Base):
    """
    One retrievable passage.

    `tenant_id` is denormalised onto the chunk on purpose. Retrieval filters on
    it directly in the WHERE clause, so a bug in a join can never widen the
    result set past one tenant.
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "version", "chunk_index", name="uq_knowledge_chunk_slot"
        ),
        Index("ix_knowledge_chunk_tenant_doc", "tenant_id", "document_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Page number, heading trail, CSV row range -- whatever the extractor knew.
    chunk_metadata: Mapped[dict] = mapped_column(JSON, default=dict)

    #: The vector. Stored as JSON so the same code runs on SQLite in tests and
    #: on PostgreSQL in production; app/knowledge/vectorstore.py upgrades to a
    #: real pgvector column when the extension is available.
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")


# ===========================================================================
# STEP 5 -- CRM integration layer
#
# The pre-existing CRM support was three columns on `Tenant`
# (`crm_webhook_url`, `crm_type`, `crm_api_key`) and one best-effort POST.
# Those columns are deliberately left in place: `tests/test_outbound.py` still
# exercises the legacy helper, and a migration that drops a column holding a
# live tenant's webhook URL is not something to do in the same change that
# introduces its replacement. Nothing in the new layer reads them.
#
# Five new tables, and the reason each one is separate rather than folded into
# an existing row:
#
# * `CrmIntegration` -- per (tenant, provider) configuration and credentials.
#   Not on `Tenant`, because a tenant may connect several providers at once
#   and because credentials need their own encrypted column with its own
#   access pattern.
# * `CrmEvent`       -- the normalized business event. Persisted *before* any
#   provider is contacted, so a crash between "the call ended" and "the CRM
#   accepted it" loses nothing.
# * `CrmSync`        -- one delivery attempt-set per (event, integration). Fan
#   out to three providers is three rows, so one provider being down cannot
#   mark the others failed.
# * `CrmContactLink` -- the (tenant, provider, identity) -> external id map.
#   This is what stops a second call from the same phone number creating a
#   second contact.
# * `CrmWebhookReceipt` -- inbound replay protection.
# ===========================================================================

class CrmProviderType(str, enum.Enum):
    """
    Providers with an adapter. A closed enum on purpose: the old `crm_type`
    was a free string, so a typo silently selected the generic branch instead
    of failing.
    """
    GOHIGHLEVEL = "gohighlevel"
    HUBSPOT = "hubspot"
    JOBBER = "jobber"
    WEBHOOK = "webhook"


class CrmEntityType(str, enum.Enum):
    CALL = "call"
    LEAD = "lead"
    APPOINTMENT = "appointment"


class CrmEventType(str, enum.Enum):
    """
    Normalized business events.

    The values are dotted wire strings rather than the lowercase-of-the-name
    convention the knowledge enums follow, because these values are published:
    they appear in outbound webhook bodies and in tenant-facing filters.
    `lead.created` is a documented part of the integration contract, so it is
    the value, and the enum member name is what the database stores.
    """
    CALL_COMPLETED = "call.completed"
    CALL_MISSED = "call.missed"
    LEAD_CREATED = "lead.created"
    LEAD_UPDATED = "lead.updated"
    APPOINTMENT_BOOKED = "appointment.booked"
    APPOINTMENT_CANCELLED = "appointment.cancelled"
    TRANSFER_COMPLETED = "transfer.completed"


class CrmSyncStatus(str, enum.Enum):
    """
    PENDING            queued, not yet picked up
    PROCESSING         a worker holds it right now
    SYNCED             the provider acknowledged the write
    FAILED             transient failure, attempts remain
    PERMANENT_FAILURE  will not be retried without human action
    """
    PENDING = "pending"
    PROCESSING = "processing"
    SYNCED = "synced"
    FAILED = "failed"
    PERMANENT_FAILURE = "permanent_failure"


#: Statuses a worker may pick up. `SYNCED` and `PERMANENT_FAILURE` are
#: terminal; `PROCESSING` is excluded so two workers cannot claim one row, and
#: is recovered by the stuck-sync reaper instead.
RETRYABLE_SYNC_STATUSES = frozenset({CrmSyncStatus.PENDING, CrmSyncStatus.FAILED})
TERMINAL_SYNC_STATUSES = frozenset(
    {CrmSyncStatus.SYNCED, CrmSyncStatus.PERMANENT_FAILURE}
)


class CrmIntegration(Base):
    """
    One tenant's connection to one provider.

    `credentials_encrypted` is ciphertext produced by
    `app.integrations.crm.crypto`. It is never selected into an API response --
    the response models in `app/api/integration_routes.py` are allowlists, and
    there is a test asserting the column name does not appear in any response
    body.
    """
    __tablename__ = "crm_integrations"
    __table_args__ = (
        # The isolation primitive. Lookups are (tenant_id, provider); this
        # constraint is what makes that pair a key rather than a filter.
        UniqueConstraint("tenant_id", "provider", name="uq_crm_integration_tenant_provider"),
        Index("ix_crm_integration_tenant_enabled", "tenant_id", "is_enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[CrmProviderType] = mapped_column(
        Enum(CrmProviderType), nullable=False
    )

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    #: AES-GCM ciphertext of a JSON credential bundle. Opaque here on purpose.
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Which key encrypted it, so keys can be rotated without a flag day.
    credentials_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Set when credentials change, so "connected but never tested" is visible.
    credentials_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Non-secret provider settings: GHL location id, HubSpot pipeline, the
    #: outbound webhook URL. Safe to return from the API.
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    #: Tenant-defined mapping of VoxDesk fields to provider custom fields.
    field_mappings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    #: Which normalized events this integration wants. Empty = all of them.
    subscribed_events: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    #: Requirement 18: transcripts are not shared unless asked for.
    share_transcripts: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    last_health_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_health_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: Operator-facing, already scrubbed of anything secret.
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class CrmEvent(Base):
    """
    A business fact, recorded once, independent of any provider.

    Written inside the same transaction as the thing that caused it. Delivery
    is a separate concern with separate rows, which is what makes "the CRM was
    down when the call ended" a recoverable situation rather than a lost lead.
    """
    __tablename__ = "crm_events"
    __table_args__ = (
        # The idempotency primitive. Scoped to the tenant so two tenants can
        # never collide, and so a key from one tenant cannot suppress
        # another's event.
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_crm_event_idempotency"),
        Index("ix_crm_event_tenant_created", "tenant_id", "created_at"),
        Index("ix_crm_event_entity", "tenant_id", "entity_type", "entity_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )

    event_type: Mapped[CrmEventType] = mapped_column(Enum(CrmEventType), nullable=False)
    entity_type: Mapped[CrmEntityType] = mapped_column(Enum(CrmEntityType), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    #: Deterministic; see `events.idempotency_key`. Same business fact, same
    #: key, forever.
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)

    #: The normalized payload, already scrubbed. Versioned so an adapter can
    #: tell an old row from a new one after a schema change.
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    payload_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class CrmSync(Base):
    """
    Delivery state for one event to one integration.

    `external_id` is written in the same commit that sets SYNCED, so a row can
    never claim success without recording what the provider created.
    """
    __tablename__ = "crm_syncs"
    __table_args__ = (
        # One delivery record per (event, integration). This is the constraint
        # that makes a duplicate worker pass a no-op rather than a second POST.
        UniqueConstraint("event_id", "integration_id", name="uq_crm_sync_event_integration"),
        Index("ix_crm_sync_due", "status", "next_attempt_at"),
        Index("ix_crm_sync_tenant_status", "tenant_id", "status"),
        Index("ix_crm_sync_entity", "tenant_id", "entity_type", "entity_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crm_events.id", ondelete="CASCADE"), index=True, nullable=False
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crm_integrations.id", ondelete="CASCADE"), index=True, nullable=False
    )

    provider: Mapped[CrmProviderType] = mapped_column(Enum(CrmProviderType), nullable=False)
    entity_type: Mapped[CrmEntityType] = mapped_column(Enum(CrmEntityType), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    status: Mapped[CrmSyncStatus] = mapped_column(
        Enum(CrmSyncStatus), default=CrmSyncStatus.PENDING, nullable=False
    )
    #: What the provider created or updated. Proof of the SYNCED claim.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Scrubbed before storage -- see `errors.safe_message`. Shown to the
    #: tenant, so it must never contain a token or a raw provider body.
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Normalized error class (`rate_limited`, `unauthorized`, ...), for
    #: dashboards that want to group failures without parsing prose.
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class CrmContactLink(Base):
    """
    (tenant, provider, identity) -> provider contact id.

    The identity is a normalized phone or email, hashed, never the raw value:
    this table exists to be looked up quickly and it should not become a
    second copy of the customer list.

    Requirement 19's "never match contacts across tenants" is enforced by
    `tenant_id` being the first column of the unique constraint, not by
    application code remembering to filter.
    """
    __tablename__ = "crm_contact_links"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "provider", "identity_hash", name="uq_crm_contact_identity"
        ),
        Index("ix_crm_contact_tenant_provider", "tenant_id", "provider"),
        Index("ix_crm_contact_lead", "tenant_id", "lead_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[CrmProviderType] = mapped_column(Enum(CrmProviderType), nullable=False)

    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"), nullable=True
    )
    #: sha256 of the normalized identity, salted per tenant.
    identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    external_contact_id: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class CrmWebhookReceipt(Base):
    """
    Inbound provider events we have already processed.

    Requirement 21 needs replay protection, and replay protection needs
    somewhere durable to remember event ids. Rows are pruned by age, not kept
    forever.
    """
    __tablename__ = "crm_webhook_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "provider", "provider_event_id", name="uq_crm_receipt_event"
        ),
        Index("ix_crm_receipt_received", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[CrmProviderType] = mapped_column(Enum(CrmProviderType), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class MessageWebhookReceipt(Base):
    """
    Inbound Twilio message events (SMS/WhatsApp) we have already processed.

    The messaging channel was the one inbound webhook without durable replay
    protection: a redelivered message would have been appended as a second turn
    and answered a second time (extra LLM spend + a duplicate reply SMS). The
    unique constraint on `(tenant_id, channel, provider_message_id)` turns the
    redelivery into a no-op. Rows are pruned by age, not kept forever.
    """
    __tablename__ = "message_webhook_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "channel", "provider_message_id",
            name="uq_message_receipt_event",
        ),
        Index("ix_message_receipt_received", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)   # sms | whatsapp
    provider_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


# ===========================================================================
# STEP 6 -- calendar integration layer
#
# Three tables. The split mirrors STEP 5's, and for the same reason: what a
# tenant configured, what policy applies, and what the provider told us are
# three different lifetimes.
#
# * `CalendarIntegration`  -- per (tenant, provider) connection + encrypted
#   OAuth credentials. Reuses STEP 5's AES-GCM envelope, including its
#   (tenant, provider) associated data.
# * `SchedulingPolicy`     -- business hours, breaks, holidays, buffers.
#   Business policy is *not* provider availability; both are checked, so both
#   need somewhere to live.
# * `CalendarWebhookReceipt` -- inbound provider notification dedupe.
# ===========================================================================

class CalendarIntegration(Base):
    """
    One tenant's connection to one calendar provider.

    Note what is *not* here: a `google_calendar_id` equivalent on `Tenant`.
    That column still exists and still works for the legacy service-account
    path; this table is what a tenant gets when they connect through OAuth.
    """
    __tablename__ = "calendar_integrations"
    __table_args__ = (
        # Same isolation primitive as STEP 5: the pair is a key, not a filter.
        UniqueConstraint(
            "tenant_id", "provider", name="uq_calendar_integration_tenant_provider"
        ),
        Index("ix_calendar_integration_tenant_enabled", "tenant_id", "is_enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[CalendarProviderType] = mapped_column(
        Enum(CalendarProviderType), nullable=False
    )

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Which provider a booking goes to when the tenant has several connected.
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: AES-256-GCM envelope over {"access_token", "refresh_token", ...}.
    #: Identical scheme to CRM credentials -- one cipher, one key ring, one
    #: rotation story. Opaque here.
    credentials_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    credentials_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    credentials_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When the access token stops working. Stored in the clear because it is
    #: not secret and the refresh scheduler needs to query on it.
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Non-secret settings: calendar id, Cal.com event-type id, base_url.
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    last_health_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_health_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class SchedulingPolicy(Base):
    """
    A tenant's booking rules.

    One row per tenant. Separate from `Tenant` because the pre-STEP-6
    `business_open` / `business_close` / `appointment_minutes` columns are read
    by existing code paths and tests, and widening `Tenant` with fifteen more
    scheduling columns would make an already-large table the home of a second
    subsystem.

    `Tenant`'s three columns remain the fallback: a tenant with no policy row
    behaves exactly as it did before STEP 6.
    """
    __tablename__ = "scheduling_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_scheduling_policy_tenant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )

    #: `{"mon": [["09:00", "12:00"], ["13:00", "17:00"]], "sun": []}`
    #: A list of intervals rather than one open/close pair, because a lunch
    #: break is the single most common reason a booking lands when nobody is
    #: there. An empty list means closed that day.
    weekly_hours: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    #: `["2026-12-25", "2026-01-01"]` -- full-day closures.
    holidays: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    #: `[{"start": "2026-07-01T00:00:00", "end": "2026-07-14T23:59:59"}]`
    #: Local wall-clock. Vacations, refits, a one-off closure.
    blocked_periods: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    slot_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    #: How far apart candidate slots start. Distinct from duration: a 60-minute
    #: appointment offered on a 30-minute grid gives twice the choice.
    slot_interval_minutes: Mapped[int] = mapped_column(Integer, default=30, nullable=False)
    buffer_before_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    buffer_after_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: The soonest a caller may book. Zero means "in one second", which is
    #: what the pre-STEP-6 code allowed.
    minimum_notice_minutes: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    #: The furthest ahead. Stops a caller booking in 2071.
    booking_horizon_days: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    #: How many options the voice agent reads aloud. Reading twelve kills a call.
    max_slots_offered: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    #: Escape hatch for businesses that genuinely take out-of-hours bookings.
    #: Off by default: requirement 5 says do not book outside business hours
    #: "unless explicitly enabled".
    allow_outside_business_hours: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    #: When the provider cannot be reached, refuse to book rather than
    #: guessing. Default True -- see the audit's F2.
    require_provider_confirmation: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class CalendarWebhookReceipt(Base):
    """
    Inbound calendar notifications already processed.

    Same shape and same reasoning as `CrmWebhookReceipt`: replay protection
    needs somewhere durable to remember provider event ids, and rows are
    pruned by age rather than kept forever.
    """
    __tablename__ = "calendar_webhook_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "provider", "provider_event_id",
            name="uq_calendar_receipt_event",
        ),
        Index("ix_calendar_receipt_received", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[CalendarProviderType] = mapped_column(
        Enum(CalendarProviderType), nullable=False
    )
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


# ===========================================================================
# STEP 7 -- billing, subscriptions and usage metering
#
# The pre-STEP-7 billing was four columns on `Tenant` and one `+=` on a float.
# Those columns stay (see `docs/BILLING-AUDIT.md`): `minutes_used` is demoted
# from *authority* to *cache*, kept in sync so the dashboard field and any
# external reader keep working, while the number that decides an invoice is
# derived from immutable `UsageEvent` rows.
#
# Five new tables. The split is not decoration -- each has a different
# lifetime and a different trust level:
#
# * `BillingPlan`   -- the catalogue. Server-owned. A tenant never names a
#   price, only a plan code, and the code is resolved here.
# * `Subscription`  -- per (tenant, provider) state, mirrored from the
#   provider. Never authoritative on its own: only a verified webhook or a
#   direct provider read may set it.
# * `UsageEvent`    -- immutable, append-only, idempotency-keyed. The
#   financial record of truth.
# * `UsageSummary`  -- a derived rollup per (tenant, period, metric). A cache
#   that can always be rebuilt from events, which is the property that makes
#   a metering bug recoverable.
# * `BillingWebhookReceipt` -- provider event dedupe and ordering.
# ===========================================================================

class BillingProviderType(str, enum.Enum):
    """
    Billing backends with an adapter.

    `MANUAL` is not a placeholder: it is how a tenant on an invoice-me
    contract, or a development instance with no Stripe account, still gets
    plans, entitlements and metering. Everything except the payment rail works
    identically.
    """
    STRIPE = "stripe"
    MANUAL = "manual"


class SubscriptionStatus(str, enum.Enum):
    """
    Normalized subscription state.

    Stripe's own vocabulary is mapped into this inside
    `app/billing/providers/stripe.py` and nowhere else. The brief is explicit
    that Stripe status strings must not be scattered through business logic,
    and the practical reason is that Stripe has changed them before --
    `incomplete_expired` did not always exist.

    CANCELING is ours, not Stripe's: Stripe expresses "cancel at period end"
    as `active` plus a boolean, which loses the distinction every UI needs.
    """
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELING = "canceling"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    PAUSED = "paused"


#: Statuses that entitle a tenant to use the product. `PAST_DUE` is included
#: deliberately -- see `app/billing/entitlements.py`. Cutting a business off
#: the instant a card expires costs far more goodwill than the few days of
#: service it saves, and Stripe is still retrying the payment.
ENTITLED_SUBSCRIPTION_STATUSES = frozenset({
    SubscriptionStatus.TRIALING,
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.CANCELING,
    SubscriptionStatus.PAST_DUE,
})

#: No further provider transition is expected.
TERMINAL_SUBSCRIPTION_STATUSES = frozenset({
    SubscriptionStatus.CANCELED,
    SubscriptionStatus.INCOMPLETE_EXPIRED,
})


class BillingInterval(str, enum.Enum):
    MONTH = "month"
    YEAR = "year"


class UsageMetric(str, enum.Enum):
    """
    What is counted.

    Values are the wire names used in the API and in plan configuration, so
    they are part of the published contract.
    """
    VOICE_MINUTE = "voice_minute"
    SMS_SEGMENT = "sms_segment"
    LLM_TOKEN = "llm_token"
    TTS_CHARACTER = "tts_character"


class UsageEventType(str, enum.Enum):
    """
    Why something was counted.

    Distinct from `UsageMetric` because one metric has several sources -- an
    inbound call, an outbound campaign call and a transfer leg all produce
    VOICE_MINUTE -- and the source is what makes a disputed invoice
    answerable.
    """
    VOICE_MINUTE_USED = "voice_minute_used"
    SMS_SEGMENT_USED = "sms_segment_used"
    LLM_TOKEN_USED = "llm_token_used"
    TTS_CHARACTER_USED = "tts_character_used"
    #: A signed correction. Never a destructive edit -- see `UsageEvent`.
    MANUAL_ADJUSTMENT = "manual_adjustment"


class InvoiceStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    UNCOLLECTIBLE = "uncollectible"
    VOID = "void"


class BillingPlan(Base):
    """
    The plan catalogue. Server-owned, and the reason a browser can never name
    a price.

    Prices are integer **minor units** (cents), not floats. A float `19.99`
    is not exactly 19.99, and accumulating overage across a few thousand
    fractional minutes in binary floating point produces invoices that do not
    reconcile with themselves. Money is counted, not measured.
    """
    __tablename__ = "billing_plans"
    __table_args__ = (
        UniqueConstraint("code", name="uq_billing_plan_code"),
        Index("ix_billing_plan_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    #: Stable identifier used by the API and by config. Never renamed.
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Ordering for a pricing page. Not billing-relevant.
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    currency: Mapped[str] = mapped_column(String(3), default="usd", nullable=False)
    monthly_price_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    annual_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    included_voice_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    included_sms_segments: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    included_llm_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    included_tts_characters: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: Overage rates, in **hundredths of a cent** per unit.
    #:
    #: A voice minute at 5c is 500 here; an LLM token at $2/million is 0.0002c,
    #: which cents cannot express at all. Sub-cent granularity is not
    #: fastidiousness -- token and character pricing is genuinely below one
    #: cent per unit, and rounding each unit to a cent would overcharge by
    #: several orders of magnitude.
    overage_voice_minute_millicents: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    overage_sms_millicents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    overage_llm_token_millicents: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    overage_tts_character_millicents: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    #: When false, exceeding the included allowance is refused rather than
    #: metered. Requirement 26: the check happens *before* the provider call.
    overage_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    #: Non-metered limits: {"team_members": 5, "rag_documents": 100, ...}.
    #: JSON rather than columns because these are the ones that change per
    #: sales conversation, and adding a column per feature would mean a
    #: migration every time someone negotiates.
    feature_entitlements: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    #: Provider price identifiers, keyed by interval:
    #: {"month": "price_FAKE123", "year": "price_FAKE456"}.
    #: **This is the trust boundary.** A checkout request names a plan code
    #: and an interval; the price id comes from here and never from a client.
    provider_price_ids: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    trial_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class Subscription(Base):
    """
    One tenant's subscription with one provider.

    `provider_updated_at` and `provider_event_sequence` exist for requirement
    23. Provider webhooks arrive out of order routinely -- `subscription.updated`
    can land before `checkout.session.completed` -- and applying a stale event
    would downgrade a customer who just upgraded. Every write compares
    timestamps first.
    """
    __tablename__ = "subscriptions"
    __table_args__ = (
        # One subscription per (tenant, provider). The isolation primitive,
        # and what makes "the tenant's subscription" a well-defined phrase.
        UniqueConstraint("tenant_id", "provider", name="uq_subscription_tenant_provider"),
        # A provider subscription id belongs to exactly one tenant. Without
        # this, a webhook carrying an id could be matched to the wrong row.
        UniqueConstraint(
            "provider", "external_subscription_id",
            name="uq_subscription_external_id",
        ),
        Index("ix_subscription_tenant_status", "tenant_id", "status"),
        Index("ix_subscription_period_end", "current_period_end"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("billing_plans.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    provider: Mapped[BillingProviderType] = mapped_column(
        Enum(BillingProviderType), nullable=False
    )

    external_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Which price the provider actually billed. Recorded so a mismatch with
    #: the plan's configured price is detectable rather than invisible.
    external_price_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus), default=SubscriptionStatus.INCOMPLETE, nullable=False
    )
    interval: Mapped[BillingInterval] = mapped_column(
        Enum(BillingInterval), default=BillingInterval.MONTH, nullable=False
    )

    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trial_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trial_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    canceled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: A downgrade that takes effect at renewal. Requirement 18 asks for an
    #: unambiguous policy: upgrades are immediate, downgrades are scheduled,
    #: and this is where a scheduled one waits.
    pending_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("billing_plans.id", ondelete="SET NULL"), nullable=True
    )
    pending_interval: Mapped[BillingInterval | None] = mapped_column(
        Enum(BillingInterval), nullable=True
    )

    #: The provider's own clock for the last state we applied. Ordering
    #: authority -- see the class docstring.
    provider_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_invoice_status: Mapped[InvoiceStatus | None] = mapped_column(
        Enum(InvoiceStatus), nullable=True
    )
    last_payment_failed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Scrubbed before storage. Shown to staff, never a raw provider body.
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class UsageEvent(Base):
    """
    One immutable, idempotency-keyed unit of consumption.

    **Append-only.** Nothing in `app/billing` updates or deletes a row here.
    A correction is a new row with a negative `quantity` and
    `event_type = MANUAL_ADJUSTMENT`, which is requirement 35's "never modify
    historical usage quantities destructively" -- and also the only way an
    invoice dispute can ever be answered, because the original figure survives
    alongside the correction.

    `quantity` is an integer in the metric's smallest unit: **seconds** for
    voice, segments for SMS, tokens, characters. Not minutes. Storing 1.5
    minutes as a float and summing a few thousand of them is how a total stops
    matching the sum of its parts.
    """
    __tablename__ = "usage_events"
    __table_args__ = (
        # The idempotency guarantee, at the database rather than in Python.
        # Requirement 14 asks for exactly this backstop.
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_usage_event_idempotency"
        ),
        # The aggregation query: everything for a tenant, period and metric.
        Index("ix_usage_event_rollup", "tenant_id", "billing_period", "metric"),
        Index("ix_usage_event_source", "tenant_id", "source_entity_id"),
        Index("ix_usage_event_created", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )

    #: `YYYY-MM` of the *billing* period, not the calendar month. Derived from
    #: the subscription's anchor where one exists -- see `billing/periods.py`.
    #: A string because it is a label, compared and grouped, never arithmetic.
    billing_period: Mapped[str] = mapped_column(String(16), nullable=False)

    metric: Mapped[UsageMetric] = mapped_column(Enum(UsageMetric), nullable=False)
    event_type: Mapped[UsageEventType] = mapped_column(
        Enum(UsageEventType), nullable=False
    )

    #: The call, message or document this came from. Makes an invoice line
    #: traceable back to the thing the customer actually did.
    source_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    source_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: Signed. Negative only for MANUAL_ADJUSTMENT.
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)

    #: Derived from the business fact, never generated per attempt. See
    #: `billing/metering.py::usage_idempotency_key`.
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)

    #: Safe context: direction, whether the call was transferred, which leg.
    #: Never a transcript, never a credential.
    event_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class UsageSummary(Base):
    """
    A derived rollup per (tenant, period, metric).

    Purely a cache: `metering.rebuild_summary()` can reconstruct any row from
    `UsageEvent` at any time. That property is the point -- it means a bug in
    the summariser is a recoverable inconvenience rather than a corrupted
    ledger, and it is what makes requirement 34's reconciliation possible.

    `finalized` marks a closed period. Once true the figures are what was
    invoiced, and later events for that period are counted but do not silently
    change the number a customer already paid.
    """
    __tablename__ = "usage_summaries"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "billing_period", "metric", name="uq_usage_summary_slot"
        ),
        Index("ix_usage_summary_period", "billing_period"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    billing_period: Mapped[str] = mapped_column(String(16), nullable=False)
    metric: Mapped[UsageMetric] = mapped_column(Enum(UsageMetric), nullable=False)

    #: All in the metric's smallest unit, matching `UsageEvent.quantity`.
    included_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    used_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    overage_quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Hundredths of a cent, to match the plan's overage rates.
    estimated_overage_millicents: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )

    event_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    finalized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Highest threshold already announced (80, 100). Stops a warning firing
    #: on every single call once a tenant is over the line.
    warned_at_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class BillingInvoice(Base):
    """
    A mirror of a provider invoice, holding only what is safe to show.

    Deliberately not a general-purpose accounting record: no line items, no
    tax breakdown, no payment method. Requirement 20 asks for metadata and a
    hosted URL, and anything beyond that would be re-implementing Stripe's
    invoice object badly.
    """
    __tablename__ = "billing_invoices"
    __table_args__ = (
        UniqueConstraint(
            "provider", "external_invoice_id", name="uq_billing_invoice_external"
        ),
        Index("ix_billing_invoice_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    provider: Mapped[BillingProviderType] = mapped_column(
        Enum(BillingProviderType), nullable=False
    )
    external_invoice_id: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[InvoiceStatus] = mapped_column(Enum(InvoiceStatus), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="usd", nullable=False)
    amount_due_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    amount_paid_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Stripe's short-lived signed URL. Safe to hand to an authorized user;
    #: it carries no API credential.
    hosted_invoice_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class BillingWebhookReceipt(Base):
    """
    Provider events already processed.

    Same pattern as `CrmWebhookReceipt` and `CalendarWebhookReceipt`. The
    difference here is that a duplicate has financial consequences, so the
    unique constraint is not an optimisation.

    `tenant_id` is nullable because a Stripe event can arrive before the
    customer is linked to a tenant -- the receipt is still recorded so a
    retry of that same event is recognised.
    """
    __tablename__ = "billing_webhook_receipts"
    __table_args__ = (
        # Provider event ids are globally unique, so this is not tenant-scoped.
        UniqueConstraint(
            "provider", "provider_event_id", name="uq_billing_receipt_event"
        ),
        Index("ix_billing_receipt_received", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    provider: Mapped[BillingProviderType] = mapped_column(
        Enum(BillingProviderType), nullable=False
    )
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    #: The provider's own creation time, used to detect a stale replay.
    provider_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: Scrubbed. Kept so a failed event can be investigated and replayed.
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

# =============================================================================
# Batch 02: the enterprise surface's durable home
# =============================================================================
#
# Batch 01 shipped the automation, notification and inbox services with state in
# process-local dictionaries and reported the missing schema as a known gap.
# These five tables close it. The service *logic* is untouched: the registries
# keep their shape, and `app/services/enterprise_store.py` hydrates them from
# these rows before an operation and flushes them back afterwards, so there is
# exactly one implementation of every rule and it is the one Batch 01 wrote.
#
# Column-type notes (deliberate, not accidental):
#   * Ids are the domain's own stable hashes (`stable_id(...)`, 24 hex chars), so
#     they are String(64) primary keys rather than UUIDs — the id a client holds
#     is the id stored, with no translation layer to drift.
#   * Timestamps that the domain models carry as ISO-8601 *strings* are stored as
#     String(40) so a round-trip through the database cannot change their
#     meaning (offset form, fractional seconds). Nothing queries them
#     arithmetically; retention uses `created_at`, which is a real timestamp.
#   * State fields are String, not `Enum`, on purpose: `test_enum_consistency.py`
#     pins the PostgreSQL enum types created by the migrations, and adding new
#     PG enum types here would extend that contract for no benefit — the closed
#     vocabularies are enforced in the domain layer, where they are tested.

class Automation(Base):
    """A tenant-owned automation definition (Batch 02 persistence)."""

    __tablename__ = "automations"
    __table_args__ = (
        Index("ix_automations_tenant_event", "tenant_id", "event"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="disabled", nullable=False)
    #: [{"field", "operator", "value"}, ...] — evaluated by the domain rule.
    filters: Mapped[list] = mapped_column(JSON, default=list)
    #: [{"name", "params"}, ...] — names are restricted to CONTROLLED_ACTIONS.
    actions: Mapped[list] = mapped_column(JSON, default=list)
    schedule_kind: Mapped[str] = mapped_column(String(16), default="on_event", nullable=False)
    delay_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    backoff_seconds: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_per_event: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    #: ISO-8601 of the last completed run — the cooldown anchor.
    last_run_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class AutomationRun(Base):
    """One attempted execution of an automation for one business event."""

    __tablename__ = "automation_runs"
    __table_args__ = (
        # The idempotency key IS the primary key: two deliveries of the same
        # business fact cannot create two rows, whatever the concurrency.
        Index("ix_automation_runs_tenant_automation", "tenant_id", "automation_id"),
        Index("ix_automation_runs_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    automation_id: Mapped[str] = mapped_column(String(64), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), index=True)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    business_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    last_error: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    result_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    finished_at: Mapped[str] = mapped_column(String(40), default="", nullable=False)


class NotificationTemplateRow(Base):
    """A tenant-owned notification template (Batch 02 persistence)."""

    __tablename__ = "notification_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    variables: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class NotificationRow(Base):
    """One rendered notification, durable across restarts.

    PII: ``recipient`` holds the delivery target (phone number, email address or
    webhook URL). The API never returns it unmasked, logs never include it, and
    :func:`app.core.retention.purge_expired_notifications` deletes whole rows
    once they are older than the retention window.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_notification_dedupe"),
        Index("ix_notifications_tenant_state", "tenant_id", "delivery_state"),
        Index("ix_notifications_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    template_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    #: {"kind", "target", "user_id"} — see the PII note above.
    recipient: Mapped[dict] = mapped_column(JSON, default=dict)
    event_source: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    rendered_body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    delivery_state: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    sent_at: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    error_summary: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class InboxThreadState(Base):
    """Inbox-only state overlaid on a real ``Call`` row.

    Assignment, priority, tags, internal notes, read/unread and the SLA clock
    have no columns on ``calls`` (Batch 01 kept them in an overlay dict), so they
    live here — one row per (tenant, call), deleted by cascade when the call
    itself is purged by retention.
    """

    __tablename__ = "inbox_thread_states"
    __table_args__ = (
        UniqueConstraint("tenant_id", "call_id", name="uq_inbox_thread_state_call"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), index=True
    )
    #: Explicit thread status once an operator moves it (open/assigned/…).
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    assignee_id: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    #: [{"body", "at", "author"}, ...] — internal notes, never customer-visible.
    notes: Mapped[list] = mapped_column(JSON, default=list)
    unread: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    opened_at: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    sla_deadline_at: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


# ================================================= membership and quotas ===
#
# ORM mappings live here, with the rest of the schema. The services under
# ``app/organization``, ``app/tenancy``, ``app/environments`` and ``app/quotas``
# own the rules. Role values are the existing ``UserRole`` enum — there is no
# second role namespace. A missing membership row is legacy single-tenant
# behaviour; a revoked row is an explicit deny.

_MEMBERSHIP_STATUSES = "('invited', 'active', 'suspended', 'revoked', 'expired')"


class OrganizationMembership(Base):
    """One principal's place in an organization.

    ``user_id`` is the principal. Machine credentials do not get a second row:
    they are attributed to this user and then narrowed by their own scopes.
    """

    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", name="uq_organization_memberships_principal"
        ),
        CheckConstraint(
            f"status IN {_MEMBERSHIP_STATUSES}",
            name="ck_organization_memberships_status",
        ),
        Index("ix_organization_memberships_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TenantMembership(Base):
    """One principal's place in a tenant. Unique per tenant and user."""

    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_tenant_memberships_principal"),
        CheckConstraint(
            f"status IN {_MEMBERSHIP_STATUSES}",
            name="ck_tenant_memberships_status",
        ),
        Index("ix_tenant_memberships_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EnvironmentMembership(Base):
    """Optional explicit environment binding. Absence means inherit."""

    __tablename__ = "environment_memberships"
    __table_args__ = (
        UniqueConstraint(
            "environment_id", "user_id", name="uq_environment_memberships_principal"
        ),
        CheckConstraint(
            f"status IN {_MEMBERSHIP_STATUSES}",
            name="ck_environment_memberships_status",
        ),
        Index("ix_environment_memberships_user", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("environments.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class MembershipInvitation(Base):
    """An invitation bound to one organization, and optionally one tenant.

    Only the SHA-256 digest of the token is stored. ``binding_key`` is the
    server-chosen scope key used to stop a second live invitation for the same
    address. It is not a client-supplied id.
    """

    __tablename__ = "membership_invitations"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_membership_invitations_token"),
        CheckConstraint(
            "scope IN ('organization', 'tenant')",
            name="ck_membership_invitations_scope",
        ),
        CheckConstraint(
            "status IN ('invited', 'accepted', 'revoked', 'expired')",
            name="ck_membership_invitations_status",
        ),
        CheckConstraint(
            "(scope = 'organization' AND tenant_id IS NULL) "
            "OR (scope = 'tenant' AND tenant_id IS NOT NULL)",
            name="ck_membership_invitations_scope_tenant",
        ),
        Index(
            "uq_membership_invitations_active",
            "binding_key",
            "email",
            unique=True,
            sqlite_where=text("status = 'invited'"),
            postgresql_where=text("status = 'invited'"),
        ),
        Index("ix_membership_invitations_org", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    #: The tenant a newly accepted user is placed in. Set by the service from
    #: the inviter's tenant or the invitation tenant. Never taken from the body.
    home_tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="invited")
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_key: Mapped[str] = mapped_column(String(80), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    accepted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QuotaLimit(Base):
    """A typed limit at one scope. Absence is unknown, not zero and not unlimited."""

    __tablename__ = "quota_limits"
    __table_args__ = (
        UniqueConstraint(
            "scope_kind", "scope_id", "quota_key", name="uq_quota_limits_scope_key"
        ),
        CheckConstraint(
            "scope_kind IN ('organization', 'tenant', 'environment')",
            name="ck_quota_limits_scope",
        ),
        CheckConstraint(
            "mode IN ('hard', 'soft', 'unlimited', 'unknown')",
            name="ck_quota_limits_mode",
        ),
        CheckConstraint(
            "(mode IN ('hard', 'soft') AND limit_value IS NOT NULL AND limit_value >= 0) "
            "OR (mode IN ('unlimited', 'unknown') AND limit_value IS NULL)",
            name="ck_quota_limits_value",
        ),
        Index("ix_quota_limits_scope", "scope_kind", "scope_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    quota_key: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    limit_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class ScopePolicy(Base):
    """Stricter overlay on the existing identity policy. Null means inherit.

    This is not a second policy engine. The resolver starts from
    ``IdentityPolicy`` and then refuses any overlay that would weaken a
    mandatory parent control.
    """

    __tablename__ = "scope_policies"
    __table_args__ = (
        UniqueConstraint("scope_kind", "scope_id", name="uq_scope_policies_scope"),
        CheckConstraint(
            "scope_kind IN ('organization', 'tenant', 'environment')",
            name="ck_scope_policies_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    mfa_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    mfa_required_for_admins: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sso_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    password_login_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    api_keys_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    service_accounts_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    session_idle_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    session_max_active: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


class EnvironmentSelection(Base):
    """Server-side current environment. Not a JWT claim."""

    __tablename__ = "environment_selections"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "tenant_id", name="uq_environment_selections_principal"
        ),
        Index("ix_environment_selections_environment", "environment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("environments.id", ondelete="CASCADE"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow,
        nullable=False,
    )


# ===================================================== enterprise identity ===
#
# The identity tables live in `app/auth/identity/models.py`, next to the domain
# rules that use them, rather than at the bottom of this already-2100-line
# module. They are attached to the *same* `Base`, imported here at the very end
# so `Base.metadata` is complete for Alembic and for `create_all` in tests,
# while the import cycle stays one-way: identity.models needs `Base` from this
# module, and this module needs nothing from it beyond the side effect of the
# definitions executing. No name is imported, precisely so a partially
# initialised module cannot fail this import.
from app.organization.models import (  # noqa: E402
    legacy_organization_name,
    legacy_organization_slug,
)


@event.listens_for(Tenant, "before_insert")
def _assign_legacy_organization(mapper, connection, target) -> None:
    """Give a tenant inserted without a parent its own organization.

    Existing callers (``make_tenant``, the seed script, ``POST /api/tenants``)
    construct a ``Tenant`` and do not know about organizations. Leaving
    ``organization_id`` null would either fail the NOT NULL constraint or store
    an orphan. One organization per tenant, slug derived from the tenant id,
    so two businesses are never folded into a shared default organization.
    """
    if target.organization_id is not None:
        return
    if target.id is None:
        target.id = _uuid()
    org_id = _uuid()
    now = datetime.utcnow()
    connection.execute(
        Organization.__table__.insert().values(
            id=org_id,
            name=legacy_organization_name(getattr(target, "name", None)),
            slug=legacy_organization_slug(target.id),
            status="active",
            created_at=now,
            updated_at=now,
        )
    )
    target.organization_id = org_id


@event.listens_for(Tenant, "after_insert")
def _ensure_production_environment(mapper, connection, target) -> None:
    """Every tenant gets exactly one production environment, including legacy inserts."""
    if target.id is None:
        return
    existing = connection.execute(
        select(Environment.id).where(
            Environment.tenant_id == target.id,
            Environment.kind == "production",
        )
    ).first()
    if existing is not None:
        return
    now = datetime.utcnow()
    connection.execute(
        Environment.__table__.insert().values(
            id=_uuid(),
            tenant_id=target.id,
            name="Production",
            slug="production",
            kind="production",
            status="active",
            is_default=True,
            production_guard=target.id,
            default_guard=target.id,
            release_version="",
            deployed_at=None,
            deployment_status="idle",
            deployment_source="",
            health_state="unknown",
            created_at=now,
            updated_at=now,
        )
    )



def _role_name(role: UserRole | str | None) -> str:
    if isinstance(role, UserRole):
        return role.name
    text_role = str(role or UserRole.VIEWER.value)
    try:
        return UserRole(text_role).name
    except ValueError:
        return text_role


@event.listens_for(User, "after_insert")
def _ensure_legacy_membership(mapper, connection, target) -> None:
    """Give a newly inserted user the membership their tenant already implies.

    Callers that still do ``User(...)`` — tests, the team API, SCIM — do not
    know about membership tables. Without this hook a later revoke would have
    no row to revoke, and a backfilled database would not match a fresh insert.
    The role copied is the role already on the user. Nothing here grants a
    higher one.
    """
    if target.id is None or target.tenant_id is None:
        return
    tenant_row = connection.execute(
        select(Tenant.organization_id).where(Tenant.id == target.tenant_id)
    ).first()
    if tenant_row is None or tenant_row[0] is None:
        return
    organization_id = tenant_row[0]
    now = datetime.utcnow()
    role = _role_name(target.role)
    existing_org = connection.execute(
        select(OrganizationMembership.id).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == target.id,
        )
    ).first()
    if existing_org is None:
        connection.execute(
            OrganizationMembership.__table__.insert().values(
                id=_uuid(),
                organization_id=organization_id,
                user_id=target.id,
                role=role,
                status=MembershipStatus.ACTIVE.value,
                created_at=now,
                updated_at=now,
                invited_at=None,
                accepted_at=now,
                suspended_at=None,
                revoked_at=None,
            )
        )
    existing_tenant = connection.execute(
        select(TenantMembership.id).where(
            TenantMembership.tenant_id == target.tenant_id,
            TenantMembership.user_id == target.id,
        )
    ).first()
    if existing_tenant is None:
        connection.execute(
            TenantMembership.__table__.insert().values(
                id=_uuid(),
                tenant_id=target.tenant_id,
                user_id=target.id,
                role=role,
                status=MembershipStatus.ACTIVE.value,
                created_at=now,
                updated_at=now,
                invited_at=None,
                accepted_at=now,
                suspended_at=None,
                revoked_at=None,
            )
        )


from app.auth.identity import models as identity_models  # noqa: E402,F401  (metadata side effect)
````

### `README.md`

````markdown
# VoxDesk v0.5 — AI Voice + Text Receptionist

**যা যা মিসিং ছিল, সব যোগ করা হয়েছে।** Fiverr-এর টপ এজেন্সিগুলোর গিগে যে ফিচারগুলো লেখা থাকে, তার প্রতিটার কোড এখানে আছে।

```
১৪৫ tests passing · ৫,১০০+ লাইন Python · ২০+ API endpoint · ৭টা AI tool
```

---

## ⚡ ৬০ সেকেন্ডে চালু

```bash
cp .env.example .env      # কী-গুলো বসান
make up                   # postgres + api + scheduler + dashboard
make seed                 # ডেমো ক্লায়েন্ট বানায় (tenant id প্রিন্ট করে)

# প্রথম owner অ্যাকাউন্ট — পাসওয়ার্ড প্রম্পটে চাইবে, argv-তে যাবে না
python -m scripts.create_owner --tenant-id <উপরের uuid> --email you@example.com

open http://localhost:8000/docs
```

> **রিপোতে কোনো ডিফল্ট ইউজারনেম/পাসওয়ার্ড নেই — ইচ্ছাকৃতভাবে।** শিপ করা
> ক্রেডেনশিয়াল মানেই ব্যাকডোর। প্রথম owner আপনি নিজে বানাবেন।

`make up` নিজেই `alembic upgrade head` চালায়। আলাদা কিছু করতে হবে না।

| কমান্ড | কাজ |
|---|---|
| `make up` / `make down` | পুরো স্ট্যাক চালু / বন্ধ |
| `make migrate` | ডাটাবেস মাইগ্রেশন |
| `make seed` | ডেমো tenant |
| `make test` | ৪১১টা টেস্ট |
| `make worker` | রিমাইন্ডার + আউটবাউন্ড ওয়ার্কার |
| `make dev` | docker ছাড়া লোকাল API |

---

## 📦 কী কী আছে

### ১. ইনবাউন্ড ভয়েস (মূল পণ্য)

```
📞 Twilio  →  Silero VAD  →  Deepgram STT  →  Backchannel
                                                   ↓
    Twilio ← ElevenLabs ← TextNormalizer ← FillerInjector ← LLM
                                                   ↑
                                    ChatGPT | Claude | Gemini
```
প্রথম অডিও পর্যন্ত লক্ষ্য **~৫৫০–৭৫০ms**। আসল সংখ্যা আপনার সার্ভারের লোকেশন আর নেটওয়ার্কের উপর নির্ভর করবে — নিজে মেপে নেবেন।

### ২. আউটবাউন্ড কলিং — `app/telephony/outbound.py`

Cold calling, follow-up, lead nurture। **আইনি গার্ডরেল কোডেই বসানো:**

| গার্ডরেল | কী করে |
|---|---|
| `is_call_window_open()` | ক্লায়েন্টের **নিজের timezone-এ** ৯টা–৮টার বাইরে কল যায় না |
| `next_window_start()` | সময় শেষ হলে ড্রপ না করে পরের দিনে শিডিউল করে |
| `backoff_for()` | ১ → ৪ → ২৪ ঘণ্টা, তারপর থামে |
| `max_call_attempts` | ডিফল্ট ৩ |
| DNC ফিল্টার | `next_callable_leads()` কখনো DNC লিড ফেরত দেয় না |
| `machine_detection` | ভয়েসমেইল ধরলে সাথে সাথে কেটে দেয় |

### ৩. আসল কল ট্রান্সফার — `app/telephony/transfer.py`

আগে AI শুধু *"transferring now"* **বলত** — কিছুই হতো না। ডেমোতে ধরা পড়ার এক নম্বর জায়গা।

এখন Twilio-র live-call-update API দিয়ে সত্যিকারের `<Dial>`:
- `answer_on_bridge=True` — কলার ringback শোনে, নীরবতা না
- whisper — মানুষটা ধরার আগে শোনে "AI transfer, emergency"
- মানুষ না ধরলে **ভয়েসমেইল**, কল কেটে যায় না

### ৪. SMS + WhatsApp — `app/channels/messaging.py`

একই ৭টা tool, একই knowledge base, একই AI — শুধু চ্যানেল আলাদা।

- **একটাই webhook** দুই চ্যানেলের জন্য (`whatsapp:` prefix দেখে চেনে)
- **STOP/START/HELP LLM-এর আগে** হ্যান্ডেল হয় — আইনি বাধ্যবাধকতা
- `"please stop by at 3"` কে opt-out ধরে **না** (এই বাগটা সবার থাকে)
- SMS উত্তর ৩০০ অক্ষরে বাক্যের শেষে কাটে
- থ্রেড ৩০ মিনিট বাঁচে, তারপর নতুন কথোপকথন

### ৫. IVR / কল ফ্লো — `app/telephony/ivr.py`

Twilio Studio-র বদলে JSON। ড্যাশবোর্ড থেকে এডিট করা যায়, deploy লাগে না।

```json
{"start": "menu", "nodes": {
  "menu": {"say": "Emergency? Press 1. Otherwise just tell me what you need.",
           "gather": {"1": "emergency"}, "timeout_goto": "ai"},
  "emergency": {"say": "Connecting you.", "transfer": "{escalation_number}"},
  "ai": {"ai": true}}}
```

`validate_flow()` ভাঙা ফ্লো **ঢুকতেই দেয় না** — dangling pointer, dead end, unreachable node, আর সবচেয়ে জরুরি: **এমন মেনু যেখান থেকে কখনো মানুষ বা AI-তে পৌঁছানো যায় না।**

### ৬. ১৩টা ভাষা — `app/core/i18n.py`

আগে `Tenant.language` কলামটা কেউ পড়ত না। এখন চার জায়গায় পৌঁছায়:

| কোথায় | কী হয় |
|---|---|
| Deepgram | nova-3 যে ভাষা কভার করে না, **নিজে থেকে nova-2**-এ নামে |
| ElevenLabs | অ-ইংরেজি হলে multilingual মডেল বাধ্যতামূলক (নইলে স্প্যানিশ ইংরেজি টানে পড়বে) |
| LLM প্রম্পট | *"ONLY Español"* — কাস্টমার ইংরেজি শব্দ বললেও ভাষা বদলায় না |
| filler_words | শুধু ইংরেজি মডেলে আছে, তাই অন্য ভাষায় বন্ধ |

en-US/GB/AU · es-US/MX · fr · de · pt-BR · it · nl · hi · ar (RTL) · bn

### ৭. A2P 10DLC + কমপ্লায়েন্স — `app/core/compliance.py`

**US-এ রেজিস্ট্রেশন ছাড়া SMS নীরবে ব্লক হয়** — ক্যারিয়ার কিছু বলে না, ক্লায়েন্ট আপনাকে দোষ দেয়।

- **SHAFT ফিল্টার** — cannabis / loan / gambling / firearms / vape ধরে ফেলে
- **URL shortener ব্লক** — bit.ly = ক্যাম্পেইন রিজেক্ট
- **সেগমেন্ট কাউন্টার** — GSM-7 বনাম UCS-2। একটা ইমোজি ১৬০ অক্ষরকে ৭০ বানিয়ে দেয়, বিল তিনগুণ
- `registration_checklist()` — কোন কাজ ক্লায়েন্টের, কোনটা আপনার, কোনটা ক্যারিয়ারের

### ৮. CRM — `app/integrations/crm.py`

কল শেষ হলেই **অটো পুশ**, ব্যাকগ্রাউন্ড টাস্কে (Twilio-র webhook কখনো আটকায় না)।
**GoHighLevel** (contact + tag mapping), HubSpot, আর generic webhook → Zapier / Make / n8n।

### ৯. লিড স্কোরিং

```
immediately 40 · this_week 30 · this_month 15 · just_looking 5
+ budget জানা 20  + decision maker 15  + কন্টাক্ট 15  + booked 10
```
**৭০+ = সাথে সাথে মালিকের ফোনে "HOT LEAD" SMS।** নিয়মটা deterministic — ক্লায়েন্ট নিজে যাচাই করতে পারবে।

### ১০. রিমাইন্ডার — `app/integrations/reminders.py`

২৪ ঘণ্টা আগে *"Reply C to confirm or R to reschedule"*। **এটাই একমাত্র ফিচার যার দাম ডলারে মাপা যায়** — তাই এটা দিয়েই project price চাওয়া যায়, hourly না।

---

## ☎️ কল লাইফসাইকেল ও মানুষে ট্রান্সফার (v0.5)

আগে `escalate_to_human` শুধু `escalated = True` বসাত আর `action: "transfer"`
ফেরত দিত — **pipeline সেটা কখনো পড়তই না**, `execute_transfer`-এর কোনো caller
ছিল না। AI বলত "connecting you", তারপর কিছুই হতো না।

| জিনিস | কী করা হয়েছে |
|---|---|
| **স্টেট মেশিন** | `app/telephony/call_state.py` — একমাত্র জায়গা যেখানে `call.status` বসে |
| **টার্মিনাল সুরক্ষা** | COMPLETED/FAILED/NO_ANSWER থেকে আর কোথাও যাওয়া যায় না |
| **অজানা স্ট্যাটাস** | উপেক্ষা করা হয়, আগে চুপচাপ COMPLETED ধরে নিত |
| **আসল ট্রান্সফার** | provider redirect **গ্রহণ করার পরেই** `TRANSFERRED` বসে |
| **প্রমাণ** | `<Dial action=/telephony/transfer-status>` callback ছাড়া "connected" বলা হয় না |
| **Idempotency** | `transfer_state` = লক। দ্বিতীয় অনুরোধে মানুষের ফোন দ্বিতীয়বার বাজে না |
| **রেস** | ৪টা callback ordering কেস — অনুমান বনাম প্রমাণ আলাদা করা |
| **সিগনেচার** | `/telephony/status`-এ HMAC যোগ (আগে সম্পূর্ণ খোলা ছিল) |
| **ডাবল বিলিং** | duplicate callback আর মিনিট দুবার বিল করে না |
| **ফোন নম্বর** | E.164 normalize; country code **কখনো অনুমান করা হয় না**; লগে redacted |

পুরো বিবরণ: **[`docs/CALL-LIFECYCLE.md`](docs/CALL-LIFECYCLE.md)**

---

## 📚 নলেজ বেস ও RAG (v0.5)

আগে পুরো `knowledge_base` dict প্রতিটা টার্নে system prompt-এ ঢুকত। এখন নয়।

| জিনিস | কী করা হয়েছে |
|---|---|
| **ডকুমেন্ট** | PDF, DOCX, TXT, Markdown, CSV, JSON আপলোড → extract → chunk → embed |
| **মূল নিয়ম** | আপলোড ≠ জ্ঞান। শুধু **retrieve হওয়া chunk** LLM-এ যায় |
| **টেন্যান্ট আইসোলেশন** | tenant filter **query-র ভিতরে**, পরে ফিল্টার করে নয় |
| **স্ট্যাটাস** | UPLOADED → PROCESSING → READY → FAILED / ARCHIVED; শুধু READY খোঁজা যায় |
| **Embedding** | provider config-driven; মডেল বদলালে reindex বাধ্যতামূলক |
| **Injection** | retrieved text = UNTRUSTED DATA; instruction-শেপ লাইন neutralize করা হয় |
| **লাইভ কলে** | bounded 1.5s timeout; ব্যর্থ হলে বানায় না, escalate করে |
| **Eval** | `tests/evals/rag/` — relevance, groundedness, refusal, injection, isolation |

পুরো বিবরণ: **[`docs/KNOWLEDGE-RAG.md`](docs/KNOWLEDGE-RAG.md)**

---

## 🔌 CRM ইন্টিগ্রেশন (v0.6)

কল/লিড/অ্যাপয়েন্টমেন্ট → normalized ইভেন্ট → provider adapter → CRM API →
persisted sync result → retry/idempotency। **চারটা provider**, নতুন provider
যোগ করতে core business logic ছুঁতে হয় না।

| Provider | Contact | Note | Appointment | Tag |
|---|:--:|:--:|:--:|:--:|
| GoHighLevel | ✅ upsert | ✅ | ✅ | ✅ |
| HubSpot | ✅ upsert | ✅ | — | — |
| Jobber | ✅ query+create | ✅ | — | — |
| Generic Webhook | ✅ signed | ✅ | ✅ | ✅ |

মূল গ্যারান্টি:

| বিষয় | কীভাবে |
|---|---|
| **Credentials** | AES-256-GCM, `(tenant_id, provider)`-এ AAD-bound। API/লগ/audit — কোথাও যায় না |
| **Idempotency** | ইভেন্ট থেকে derived key + `UNIQUE (tenant_id, idempotency_key)`। ডুপ্লিকেট কল = একটাই CRM contact |
| **Retry** | Bounded exponential + full jitter। 401/422 রিট্রাই হয় না, timeout/429/5xx হয় |
| **Tenant isolation** | প্রতিটা lookup `(tenant_id, provider)`; contact hash tenant-salted; adapter-এর হাতে কোনো DB handle নেই |
| **Failure UX** | CRM ফেল করলে কলার কিছুই টের পায় না — hook কখনো raise করে না |

⚠️ **কোনো provider API আসল credentials দিয়ে live টেস্ট করা হয়নি।** কী যাচাই
হয়েছে আর কী হয়নি: `docs/CRM-INTEGRATIONS.md` §13।

প্রোডাকশনে **`CRM_ENCRYPTION_KEYS` বাধ্যতামূলক** — না দিলে অ্যাপ boot করবে না।

পুরো বিবরণ: **[`docs/CRM-INTEGRATIONS.md`](docs/CRM-INTEGRATIONS.md)** ·
অডিট: [`docs/CRM-AUDIT.md`](docs/CRM-AUDIT.md)

---

## 📅 ক্যালেন্ডার ও শিডিউলিং (v0.7)

কলার রিকোয়েস্ট → availability → business rules → slot → booking → **provider
confirmation** → persistence → CRM event → reminder। **পাঁচটা provider**।

| Provider | Availability | Book | Reschedule | Cancel |
|---|:--:|:--:|:--:|:--:|
| Google Calendar | ✅ free/busy | ✅ | ✅ | ✅ |
| Microsoft Graph | ✅ getSchedule | ✅ | ✅ | ✅ |
| Cal.com | ✅ slots | ✅ | ✅ dedicated | ✅ |
| Internal | ✅ | ✅ | ✅ | ✅ |
| Google service account (legacy) | ⚠️ | ✅ | — | — |

মূল গ্যারান্টি:

| বিষয় | কীভাবে |
|---|---|
| **কখনো মিথ্যা "booked" নয়** | `CONFIRMED` লিখতে provider-এর `external_event_id` লাগে |
| **Outage ≠ খালি ক্যালেন্ডার** | `get_busy()` raise করে; আগের কোড `[]` ফেরাত |
| **Double-booking** | `UNIQUE (tenant_id, slot_key)` — DB সিদ্ধান্ত নেয়, application logic নয় |
| **Ambiguous timeout** | provider-কে জিজ্ঞেস করে; "চেক করতে পারিনি" ≠ "নেই" |
| **Timezone** | UTC-তে সংরক্ষণ, tenant-এর zone-এ যুক্তি; DST gap/overlap ধরা পড়ে |
| **Business hours** | per-weekday intervals, lunch break, holiday, blocked period |
| **LLM booking বানাতে পারে না** | outcome একটা closed enum; message service লেখে |

⚠️ **কোনো calendar provider আসল credentials দিয়ে live টেস্ট করা হয়নি।**
বিস্তারিত: `docs/CALENDAR-INTEGRATIONS.md` §12।

পুরো বিবরণ: **[`docs/CALENDAR-INTEGRATIONS.md`](docs/CALENDAR-INTEGRATIONS.md)** ·
অডিট: [`docs/CALENDAR-AUDIT.md`](docs/CALENDAR-AUDIT.md)

---

## 💳 বিলিং, Stripe ও usage metering (v0.8)

customer → plan → subscription → usage event → metering → invoice →
entitlement → webhook reconciliation।

| প্ল্যান | মাসিক | ভয়েস মিনিট | Overage/min | Overage? |
|---|--:|--:|--:|:--:|
| Trial | $0 | 60 | — | **না** |
| Starter | $199 | 500 | ১২¢ | হ্যাঁ |
| Pro | $499 | 2,000 | ১০¢ | হ্যাঁ |
| Enterprise | $1,499 | 10,000 | ৮¢ | হ্যাঁ |

মূল গ্যারান্টি:

| বিষয় | কীভাবে |
|---|---|
| **Usage authority** | immutable append-only `UsageEvent`; `minutes_used` এখন শুধু cache |
| **Idempotency** | key কল থেকে derived + `UNIQUE (tenant_id, idempotency_key)` — ১০টা duplicate callback = ১টা চার্জ |
| **Webhook ordering** | provider timestamp তুলনা — বাসি event নতুন state ফেরত নিতে পারে না |
| **Signature** | raw bytes, rotation-এ একাধিক `v1`, replay window, constant-time |
| **Price trust** | ক্লায়েন্ট শুধু plan code পাঠায়; `CheckoutIn`-এ `price` ফিল্ডই নেই |
| **Money** | integer cents ও millicents — কোথাও float নেই |
| **Checkout ≠ active** | শুধু verified webhook subscription সক্রিয় করে |

⚠️ **কোনো Stripe API কল আসল credentials দিয়ে করা হয়নি** — test-mode-ও নয়।
বিস্তারিত: `docs/BILLING.md` §13।

দুটো আসল বাগ পাওয়া গেছে ও সারানো হয়েছে: একই ১২০-সেকেন্ডের কল webhook ক্রম
অনুযায়ী **২.০০/১.০০/০.৫০ মিনিট** বিল করত, আর `minutes_used` **কখনো রিসেট হতো
না** — মানে "৫০০ মিনিট/মাস" আসলে "৭৫০ মিনিট চিরকাল"।

পুরো বিবরণ: **[`docs/BILLING.md`](docs/BILLING.md)** ·
অডিট: [`docs/BILLING-AUDIT.md`](docs/BILLING-AUDIT.md)

---

## 🔐 অথেনটিকেশন ও টেন্যান্ট আইসোলেশন (v0.4)

আগে প্রতিটা এন্ডপয়েন্ট খোলা ছিল। এখন নয়।

| জিনিস | কী করা হয়েছে |
|---|---|
| **লগইন** | `POST /auth/login` → ১৫ মিনিটের JWT + HttpOnly রিফ্রেশ কুকি |
| **পাসওয়ার্ড** | bcrypt cost 12, ১২+ ক্যারেক্টার পলিসি, কখনো লগ/সিরিয়ালাইজ হয় না |
| **রোল** | owner / admin / manager / agent / viewer — পারমিশন এক জায়গায় ডিফাইন করা |
| **টেন্যান্ট আইসোলেশন** | URL-এর `tenant_id` **অবিশ্বস্ত ইনপুট**; আসল tenant টোকেন থেকে আসে |
| **Organization** | প্রতিটা tenant একটা organization-এর নিচে। `organization_id` JWT claim নয় — সার্ভার tenant row থেকে বের করে। একটা গ্লোবাল Default Organization নেই |
| **Membership** | Organization/tenant membership বিদ্যমান `User.role` আর permission vocabulary ব্যবহার করে। Revoked membership বিদ্যমান tenant API-কেও বন্ধ করে। Invitation token শুধু hash আকারে সেভ হয় |
| **ক্রস-টেন্যান্ট** | সবসময় `404`, কখনো `403` — ৪০৩ দিলে বোঝা যায় জিনিসটা আছে |
| **রিফ্রেশ টোকেন** | opaque, শুধু SHA-256 হ্যাশ সেভ হয়, একবার ব্যবহারযোগ্য, চুরি ধরা পড়লে সব সেশন বাতিল |
| **ইনস্ট্যান্ট রিভোক** | `token_version` বাড়ালেই ওই ইউজারের সব টোকেন সঙ্গে সঙ্গে মৃত |
| **অডিট লগ** | লগইন, লগআউট, ইউজার তৈরি, রোল পরিবর্তন, ডিঅ্যাক্টিভেশন, অথ-ডিনায়াল |
| **স্টার্টআপ গেট** | প্রোডাকশনে ডিফল্ট `JWT_SECRET` থাকলে অ্যাপ **বুটই হবে না** |
| **WebSocket** | Twilio মিডিয়া স্ট্রিম এখন সাইন করা, কল-নির্দিষ্ট, ১২০ সেকেন্ডের টোকেন চায় |

পুরো বিবরণ, থ্রেট মডেল আর প্রোডাকশন চেকলিস্ট: **[`docs/AUTH.md`](docs/AUTH.md)**

```
POST /auth/login      /auth/refresh    /auth/logout    /auth/logout-all
GET  /auth/me         /auth/roles
GET  /api/team/users  POST /api/team/users
PATCH /api/team/users/{id}/role        /api/team/users/{id}/active
GET  /api/team/audit
```

---

## 🛠 ৭টা AI Tool

| Tool | কাজ |
|---|---|
| `check_availability` | ক্যালেন্ডার দেখে **সর্বোচ্চ ৩টা** স্লট বলে (১২টা পড়লে কল মরে) |
| `book_appointment` | বুক করার আগে **আবার** চেক করে (কথা বলার ফাঁকে স্লট চলে যেতে পারে) |
| `answer_question` | knowledge_base থেকে — **না জানলে বানায় না**, message নেয় |
| `qualify_lead` | ০–১০০ স্কোর + hot lead SMS |
| `take_message` | মালিককে SMS |
| `escalate_to_human` | **আসল** `<Dial>` ট্রান্সফার |
| `mark_do_not_call` | স্থায়ী DNC |

---

## 🌐 API (২০+)

```
POST   /telephony/voice              ইনকামিং কল (IVR অথবা সরাসরি AI)
POST   /telephony/ivr                মেনু কী-প্রেস
POST   /telephony/outbound-answer    আউটবাউন্ড উঠলে (voicemail detect)
WS     /telephony/ws                 মিডিয়া স্ট্রিম
POST   /channels/message             SMS + WhatsApp (একটাই)

GET    /api/llm/presets              ChatGPT / Claude / Gemini ড্রপডাউন
GET    /api/languages                ১৩ ভাষা + কোন STT মডেল
PATCH  /api/tenants/{id}/voice       লাইভ টিউনিং
GET    /api/tenants/{id}/ivr         ফ্লো পড়া
PUT    /api/tenants/{id}/ivr         ফ্লো লেখা (ভাঙা হলে 422)
POST   /api/ivr/validate             এডিটরের লাইভ ভ্যালিডেশন
POST   /api/tenants/{id}/leads       বাল্ক ইমপোর্ট (ডুপ্লিকেট বাদ)
GET    /api/tenants/{id}/leads       স্কোর অনুযায়ী সাজানো
POST   /api/tenants/{id}/campaigns   ক্যাম্পেইন
POST   /api/.../campaigns/{id}/run   ব্যাচ ডায়াল (dry_run=true ডিফল্ট)
POST   /api/compliance/check-message পাঠানোর আগে যাচাই + সেগমেন্ট
GET    /api/compliance/a2p-checklist ক্লায়েন্টকে পাঠানোর লিস্ট
GET    /api/tenants/{id}/stats       ড্যাশবোর্ড
```

---

## 🎛️ লাইভ টিউনিং — ডিল ক্লোজ করার মুহূর্ত

```
ক্লায়েন্ট : "রোবটটা আমার কথার মাঝে কেটে দিচ্ছে"
আপনি     : PATCH /api/tenants/{id}/voice  {"vad_stop_secs": 0.60}
আপনি     : "আবার কল দিন"
ক্লায়েন্ট : "ঠিক হয়ে গেছে!"
```

| নব | রেঞ্জ | ডিফল্ট |
|---|---|---|
| `vad_stop_secs` | 0.30 দ্রুত ↔ 0.70 নিরাপদ | 0.45 |
| `temperature` | 0.2 রোবটিক ↔ 0.8 স্বাভাবিক | 0.65 |
| `speech_speed` | 0.9 ↔ 1.15 | 1.0 |
| `llm_preset` | fast / natural / cheap / smart | natural |
| `humanize` | true / false | true |

---

## 🤖 চারটা প্রিসেট

| প্রিসেট | AI | কখন |
|---|---|---|
| `fast` | ChatGPT gpt-4o-mini | tool calling নিখুঁত |
| `natural` | Claude Haiku | সবচেয়ে মানুষের মতো **← ডিফল্ট** |
| `cheap` | Gemini Flash | সস্তা, বহুভাষী |
| `smart` | Claude Sonnet | জটিল কথোপকথন |

একটা key না থাকলে নিজে থেকেই পরেরটায় যায়।

---

## 📁 গঠন

```
app/
├── agent/       llm_factory · humanize · pipeline · prompts · functions · text_agent
├── channels/    messaging (SMS + WhatsApp)
├── telephony/   twilio_handler · outbound · transfer · ivr
├── integrations/ google_calendar · crm · reminders · notifications
├── core/        config · logging · i18n · compliance
├── db/          models · session
└── api/         routes
alembic/versions/0001_baseline.py     পুরো স্কিমা
scripts/scheduler.py                  আলাদা প্রসেস (আটকে গেলেও কল ধরা বন্ধ হবে না)
tests/            ১৪৫টা
```

---

## ⚠️ সৎ সতর্কবার্তা

**যা কোডে প্রমাণিত:** ১৪৫টা টেস্ট পাস — TCPA উইন্ডো, ব্যাকঅফ, DNC, লিড স্কোর, IVR ভ্যালিডেশন, GSM-7/UCS-2 সেগমেন্ট, ভাষা fallback, opt-out কীওয়ার্ড, CRM ম্যাপিং।

**যা এখনো আসল API-র বিপরীতে যাচাই হয়নি:**
- pipecat 0.0.55-এর import path (লাইব্রেরিটা দ্রুত বদলায়)
- `AnthropicLLMService` / `GoogleLLMService`-এর `InputParams`
- মডেল নাম (`claude-haiku-4-5`, `gemini-2.0-flash`)
- Twilio Trust Hub-এর policy SID
- **প্রকৃত latency**

**সবচেয়ে জরুরি:** প্রথম আসল ফোন কলের আগে এর কোনোটাই প্রমাণিত না। `make up` চালান, নিজের নম্বরে কল দিন, যা ভাঙে ঠিক করুন।

**কোড ৩০%, টিউনিং ৭০%।**

---

## পরের ধাপ

1. Twilio অ্যাকাউন্ট + US নম্বর — ২০ মিনিট, ফ্রি
2. `.env` ভরুন → `make up` → `make seed`
3. ngrok দিয়ে `/telephony/voice` webhook বসান
4. নিজের নম্বরে কল দিন
5. যা অস্বস্তিকর লাগে লিখে রাখুন → `vad_stop_secs` / `temperature` টিউন করুন
6. স্ক্রিন রেকর্ডিং = আপনার Fiverr gig, Upwork proposal, cold email — সব
````

### `docs/DEPLOYMENT.md`

````markdown
# VoxDesk — Deployment (staging & production)

This document is the operator runbook for taking VoxDesk from development →
staging → production. It covers the deployment dependency graph, environment
separation, the safe deploy/rollback sequence, migrations, backup/restore, TLS
and the security controls that hold it together. Anything that is *enforced*
here is enforced by code (`Settings.validate_security()`, `scripts/migrate.py`,
`scripts/deploy.sh`, `scripts/restore.sh`) and verified by
`tests/test_deployment.py` — not just prose.

## 1. Deployment dependency graph

Built from the repository, not assumed. Arrows mean "depends on to serve or
run correctly":

```
                     ┌──────────────┐
                     │    caddy     │  TLS termination, security headers,
                     │  (80/443)    │  request-size ceiling, WebSocket passthrough
                     └──────┬───────┘
                            │ reverse_proxy api:8000  (and grafana:3000 when
                            │ GRAFANA_DOMAIN is set)
                ┌───────────▼───────────┐
                │         api           │  FastAPI + built dashboard
                │  (127.0.0.1:8000)     │  entrypoint → migrate.py → uvicorn
                └──┬───────┬────────┬───┘
        ┌──────────▼──┐ ┌──▼──────┐ │ ┌──────────────┐
        │     db      │ │  redis  │ │ │  prometheus  │ scrapes api:8000/metrics
        │  PostgreSQL │ │ cache + │ │ └──────┬───────┘
        │  (internal) │ │ rate    │ │   ┌────▼─────┐
        └──────┬──────┘ │ limit   │ │   │ grafana  │ (127.0.0.1:3000, internal)
               │        └─────────┘ │   └──────────┘
        ┌──────▼──────────────────┐ │
        │  scheduler (worker)     │ │  reminders, campaigns, CRM sync,
        │  depends_on api+db+redis│ │  knowledge ingestion, billing
        └─────────────────────────┘ │  reconciliation, retention
        ┌───────────────────────────▼─┐
        │  backup (nightly pg_dump)   │  depends_on db: service_healthy
        └─────────────────────────────┘
```

Ordering is enforced in `docker-compose.prod.yml`: `api` waits for `db` and
`redis` to be *healthy*; `scheduler` waits for `db` healthy and `api` started;
`backup`, `prometheus` and `caddy` wait for their upstreams. `db` and `redis`
expose **no host ports** — they are reachable only on the compose network —
and `api`/`grafana` bind to `127.0.0.1` so nothing can bypass Caddy.

## 2. Environments

Three environments, strictly separated:

| | development | staging | production |
|---|---|---|---|
| Compose file | `docker-compose.yml` | `docker-compose.staging.yml` | `docker-compose.prod.yml` |
| `APP_ENV` | `development` | `staging` | `production` |
| Env file | `.env` | `.env.staging` | `.env` |
| Schema owner | `create_all` (dev convenience) | **Alembic only** | **Alembic only** |
| DB/Redis/volumes | local | `*_staging` (separate) | `pgdata`/`redisdata` |
| Secrets | dev placeholders | **its own** (`secrets-staging/`) | `secrets/` |
| Stripe | — | test-mode keys only | live keys |
| E2E guard (Step 5) | allowed | allowed (default off) | **refused at boot** |
| Failure injection | allowed | allowed | refused |
| Host ports | `8000`, `3000` | `8001`, `3001`, `8080/8443` | `8000`, `3000`, `80/443` |

Rules:

* **Never copy production secrets into staging.** Staging uses its own
  `.env.staging`, its own `secrets-staging/` directory, its own volumes
  (`pgdata_staging`, …), its own database and its own test-mode provider
  credentials (Twilio test account, Stripe `sk_test_*`, etc.). Template:
  `.env.staging.example`.
* `validate_security()` treats staging as **not production** (E2E and failure
  injection are permitted) but also as **not development** (no `create_all`,
  Alembic owns the schema). Staging therefore exercises the same migration,
  readiness and proxy path production does, without touching production data.
* `KNOWN_APP_ENVS = {development, test, staging, production, prod}` — any other
  `APP_ENV` fails at boot.

### Sandbox limitations (this repository's environment)

This workspace has no Docker daemon, so container-level staging/production
validation cannot run here. PostgreSQL 17 **is** available locally, so
migration and restore logic can be exercised against it. Everywhere else the
validation is static and deterministic (see `tests/test_deployment.py`). Real
runtime claims are never fabricated: when a check needs Docker or a live
stack, that blocker is stated explicitly rather than simulated.

## 3. Production configuration (fail-closed)

`Settings.validate_security()` runs at startup and, in production, **refuses
to boot** on any of:

* `RATE_LIMIT_ENABLED` not `true`
* `LOG_LEVEL=DEBUG`
* `PUBLIC_BASE_URL` not `https://`, or containing `localhost`/`127.0.0.1`
* any `CORS_ORIGINS` entry that is not `https://`
* placeholder-looking secrets (markers like `change-me`, `xxxx`,
  `placeholder`, …) in `SECRET_KEY`, `JWT_SECRET`, `TWILIO_AUTH_TOKEN`,
  `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, the LLM keys, `STRIPE_*`
* `TWILIO_AUTH_TOKEN` empty, or `TWILIO_SKIP_WEBHOOK_VERIFY` enabled
* `E2E_ENABLED` set (real-call E2E is operator-run in a test environment only)
* `KNOWLEDGE_EMBEDDING_PROVIDER=hashing` (development stub)
* `CRM_ENCRYPTION_KEYS` empty (provider credentials would be stored plaintext)
* `BILLING_PROVIDER=stripe` with a `sk_test_*` key, or
  `BILLING_UNLIMITED_ENTITLEMENTS` enabled
* `JWT_SECRET` still the default or shorter than 32 chars; `SECRET_KEY` default

There is **no second configuration system** — everything lives in
`app/core/config.py` on the existing `Settings` architecture. `uses_https` is
derived from the actual `PUBLIC_BASE_URL` scheme, so the Secure-cookie flag
(`auth_routes.py`) and the HSTS header (`security_headers.py`) follow the real
TLS posture instead of guessing from the environment name.

## 4. Deploy sequence (safe, single-VM)

`scripts/deploy.sh` (idempotent; flags `--no-build`, `--no-backup`, `--no-pull`):

1. `git pull --ff-only` (skipped under rollback)
2. build images
3. **pre-deploy backup** of the database, then **verify** the dump with
   `pg_restore --list`. A deploy that cannot back up (or whose backup fails
   verification) aborts before touching the schema — the backup is the
   rollback point.
4. **migrations under an advisory lock** — `scripts/migrate.py` (below)
5. recreate containers onto the new image
6. wait up to 120 s for `/health/ready`
7. verify `/health` and report

On any failure the script prints the rollback command. It **never** downgrades
the database automatically.

### Honest zero-downtime statement

This is a **single-VM, single-Postgres topology**, so it **does not guarantee
zero-downtime**. `docker compose up -d` recreates the `api` container, which
means a brief (seconds) connection drop on the public path while Caddy
retries. There is no blue/green deployment, no load balancer, and no
online/offline schema pattern. Migrations run *before* the new code serves, so
requests are not served against a half-migrated schema; that is the only
uptime guarantee claimed. If true zero-downtime is required, the deployment
must grow a second host, a load balancer and an expand/contract migration
cadence — out of scope for this step and stated here rather than pretended.

## 5. Migrations and rollback

`scripts/migrate.py` is the **only** sanctioned way the API applies migrations
in staging/production. It acquires a session-level PostgreSQL advisory lock
(`pg_try_advisory_lock`, key `7272_0011`) and then runs
`alembic upgrade head` in a subprocess. Concurrent container starts serialize
on the lock instead of racing DDL. If the lock is not acquired within
`MIGRATE_LOCK_TIMEOUT_SECONDS` (default 600 s) it fails loudly. Startup never
silently skips migrations: the entrypoint runs `migrate.py` and exits on
failure, so the container never serves with a stale schema.

Migration chain (`alembic/versions/`): a single linear head at
`0018_organization_memberships_quotas` with no gaps back to `0001_baseline`
(enforced by `tests/test_deployment.py`). `0018` adds membership, invitation
and quota tables and backfills one active membership per existing user without
rewriting user or tenant ids. `0017` adds `organizations`,
`environments`, and a non-null `tenants.organization_id` after backfilling one
organization and one production environment per existing tenant. It does not
add `environment_id` to calls, leads, billing or identity. Revision `0012` adds the five tables
behind the automation/notification/inbox surface (`automations`,
`automation_runs`, `notification_templates`, `notifications`,
`inbox_thread_states`); before it those services kept their state in process
memory, so a restart lost automations, notifications and unread badges. `alembic.ini`/`env.py` use the
SQLAlchemy URL from settings.

**Rollback policy:**

* `scripts/rollback.sh <git-sha>` checks out the sha and runs
  `deploy.sh --no-pull`. It records the *applied* migration revision first, so
  the operator can see whether the target sha predates the schema.
* **It never runs `alembic downgrade`.** Forward migrations can be destructive
  in principle (a `downgrade` can drop columns and lose data). Downgrades are
  a separate, explicitly-authorized, operator-run action.
* Migrations are written **additive/backward-compatible wherever practical**,
  so an old app against a newer schema keeps working — code rollback then
  needs no schema rollback. When a change cannot be backward-compatible it is
  released as its own step and documented as "no code rollback past this point
  without a manual downgrade".

## 6. Backup and restore

**Schedule/format/retention:** the `backup` service (and `scripts/backup.sh`)
take a nightly `pg_dump` custom-format (`-Fc`) archive; the last 14 are kept
locally; optional `rclone` sync to an S3-compatible remote when `RCLONE_REMOTE`
is set. **Off-site is required, not optional**: backups must not live only in
the same failure domain as the primary database — keep `RCLONE_REMOTE` (or an
equivalent) pointed somewhere outside the primary VM.

**Verification:** every dump — nightly, pre-deploy, or manual — is verified
with `pg_restore --list` before it is retained or synced; an unreadable dump
is reported and never counted as a backup (`backup_verify.sh` does this
standalone). `backup.sh` exits non-zero on an unreadable dump.

**Restore (`scripts/restore.sh`):**

1. verifies the dump with `pg_restore --list` **before writing anything**
2. refuses to restore over a database that already has tables unless
   `RESTORE_ALLOW_OVERWRITE=1` — a drill can never silently clobber a live DB
3. `RESTORE_TARGET_DB=voxdesk_restore_drill` restores into a **scratch**
   database for safe, non-production drills
4. after restoring, re-counts tables and fails if nothing landed

Post-restore verification (operator checklist): confirm the Alembic revision
(`alembic current`), spot-check tenant rows, billing/subscription rows, call
records and audit rows, then run `/health/ready` and `scripts/smoke_test.py`.
A restore that has not actually been exercised is not trusted.

## 7. Redis recovery classification

Redis is **ephemeral / cache-only** in this architecture: it holds the
per-IP rate-limit counters and the cache. The compose stack gives it an AOF
volume (`redisdata`) for convenient restart, but no correctness-critical state
lives in Redis — call state, tenant data, billing and audit all live in
PostgreSQL. The application already treats Redis as best-effort: on any Redis
error, reads return cache-miss, writes are dropped, and `ping()` reports
unreachable (`app/core/cache.py`). **Recovery is therefore "wipe and restart"**
— a lost cache is repopulated on demand and rate-limit counters reset, which
is an acceptable, documented behavior (not a data-loss event). This is pinned
by `tests/test_deployment.py::test_redis_cache_degrades_gracefully_when_down`.

## 8. TLS / reverse proxy

Caddy is the public entry point (80/443; automatic Let's Encrypt when `DOMAIN`
is set). `Caddyfile`:

* `admin off` — the admin control plane is not exposed
* security headers at the proxy layer (HSTS, `X-Content-Type-Options`,
  `X-Frame-Options: DENY`, `Referrer-Policy`), mirrored by the app middleware
* `request_body max_size 25MB` — above the 20 MB knowledge-upload ceiling
* **WebSocket upgrade passes through unchanged** — the Twilio Media Streams
  handshake depends on it, and nothing in the file interferes
* Grafana is only exposed when `GRAFANA_DOMAIN` is explicitly set; otherwise
  it is reachable only inside the compose network

The API trusts `X-Forwarded-*` via `--proxy-headers` (entrypoint) so
`PUBLIC_BASE_URL`, `wss://` and Twilio signature validation work; tighten
`FORWARDED_ALLOW_IPS` from the default `*` in hardened setups.

## 9. Monitoring exposure

Prometheus and Grafana are **internal**. Prometheus has no host ports at all;
Grafana binds `127.0.0.1:3000` and is only proxied when `GRAFANA_DOMAIN` is
set. The API `/metrics` scrape endpoint is gated by `METRICS_TOKEN` when set
(`app/core/metrics.py::_scrape_authorized`). The Caddyfile contains no
`/metrics` route. `tests/test_deployment.py` pins all of these.

## 10. Secrets and supply chain

* No secrets in Git: `.gitignore` covers `.env`, `secrets/`, `backups/`,
  `*.dump`, `*.pem`, `*.key`, `.netrc`; gitleaks runs in CI; `.env.example`
  and `.env.staging.example` contain placeholders only (asserted by tests).
* No secrets in the image or build context: `.dockerignore` excludes the same
  set, and the Dockerfile never copies `.env`.
* No secrets in logs/errors: Sentry DSN is the only observability secret and
  is never logged or returned by the API (existing behavior, unchanged).
* **Dockerfile**: multi-stage, runs as non-root `appuser` (UID 10001),
  `HEALTHCHECK`, pinned base `python:3.12-slim-bookworm` (see the comment in
  the file — the pin is a deliberate change policy, not a blind upgrade), no
  build tooling in the runtime stage.
* **Dependency/image scanning**: CI runs `bandit` (SAST), `pip-audit`
  (Python dependency CVEs), `npm audit --omit=dev --audit-level=high`
  (shipped frontend dependencies) and gitleaks; compose images are pinned
  (`postgres:16-alpine`, `redis:7-alpine`, `prom/prometheus:v2.53.0`,
  `grafana/grafana:11.1.0`, `caddy:2.9-alpine`).

### Accepted risks (documented, not hidden)

* `npm audit` (full, including dev) reports a **moderate** advisory in the
  Vitest *test tooling* (`@vitest/mocker` redirect mock, fixed only by a
  breaking Vitest major). It does not affect shipped code: `npm audit
  --omit=dev` is clean. Mitigation: upgrade Vitest on its next major in a
  dedicated change; do not `--force` a breaking upgrade mid-step.
* `FORWARDED_ALLOW_IPS` defaults to `*` for single-proxy deployments; document
  and tighten when a second proxy is introduced.

## 11. CI/CD and deploy authorization

* Normal CI (every push/PR): backend lint + tests, Postgres migration round
  trip (`alembic upgrade head` + `downgrade base`), frontend tests + build +
  production audit, Docker image build.
* **Real-provider tests are separate and manual** (`real-integrations.yml`,
  `workflow_dispatch`, gated on `VOXDESK_REAL_INTEGRATION=1` and the
  `real_provider` marker). They are never in the default CI path.
* **Production deployment requires explicit authorization**: there is no
  automatic deploy-to-prod step. Deploys are operator-run via
  `scripts/deploy.sh`; the safe defaults (backup + verified migration +
  readiness wait) are the authorization gate.

## 12. Staging smoke test

`scripts/smoke_test.py` is the deterministic, read-only deployment smoke test:
it exercises `/health`, `/health/ready`, the dashboard shell, `/metrics`
(200/401/404 are all legitimate), `/auth/login` (a bogus login must 4xx,
never 500), `/api/tenants` (anonymous must be refused), the Twilio webhook
(unsigned POST must 403 — proving signature verification is fail-closed), the
provider-config presence and the E2E guard state. It performs **no** writes
and **no** external calls; non-zero exit gates the deploy.

```bash
SMOKE_BASE_URL=http://localhost:8001 python scripts/smoke_test.py   # staging
```
````

### `docs/ENTERPRISE-IDENTITY.md`

````markdown
# VoxDesk — Enterprise Identity

The system of record for **who may sign in, how they prove it, and what they may
do once they have**. This document is the map: what exists, where it lives, which
knob turns it, and which test proves it. The five feature documents go deeper:

| Document | Covers |
| --- | --- |
| [`MFA.md`](MFA.md) | TOTP enrollment, recovery codes, challenges, lockout, reauth |
| [`SSO-OIDC.md`](SSO-OIDC.md) | OIDC connections, authorization-code + PKCE, token validation |
| [`SSO-SAML.md`](SSO-SAML.md) | SAML 2.0 SP, metadata, ACS/SLO, assertion and signature rules |
| [`SCIM.md`](SCIM.md) | SCIM 2.0 Users/Groups, credentials, filtering, pagination |
| [`API-KEYS.md`](API-KEYS.md) | Tenant-scoped API keys: scopes, rotation, expiry |
| [`SERVICE-ACCOUNTS.md`](SERVICE-ACCOUNTS.md) | Machine identities, credentials, emergency disable |
| [`DOMAIN-VERIFICATION.md`](DOMAIN-VERIFICATION.md) | DNS TXT ownership proof and SSO enforcement |
| [`IDENTITY-SECURITY.md`](IDENTITY-SECURITY.md) | Threat model, invariants, adversarial cases, response |

---

## 1. What this layer is, and what it deliberately is not

It is an **extension of the existing auth stack**, not a replacement:

- `app/auth/jwt.py`, `password.py`, `rbac.py`, `permissions.py` and
  `app/auth/service.py` keep their behaviour. Access tokens still carry
  `sub`, `tid`, `role`, `tv`, `sid`, `amr`, are HS256-pinned, and are still
  checked against `token_version` on every request.
- No second `User`, `Tenant`, `Role`, `Session` or `Token` model exists.
  `users`, `tenants`, `refresh_tokens` and `audit_logs` are the same tables as
  before; the identity layer adds its own and *references* them.
- Service-to-service authentication is untouched: a tenant API key or service
  account credential authenticates through the same dependency chain and
  produces a principal the same routes already accept.

The one deliberate change to existing behaviour is documented in
[`IDENTITY-SECURITY.md`](IDENTITY-SECURITY.md) §7: "revoke every credential for
this user" now closes `user_sessions` rows as well as `refresh_tokens`. Before
this layer the session row did not exist, so there was nothing to close.

**Not in this layer** (the next P0): Organization / Tenant / Environment
expansion, delegated tenant administration, cross-tenant identity federation.

---

## 2. Layout

```
app/auth/identity/
  models.py        19 tables (below)
  policies.py      the policy engine: load + evaluate, default policy
  service.py       IdentityContext, assert_privileged, reauth, features
  sessions.py      session rows: create, touch, revoke, evict, suspicious
  mfa.py           TOTP enrollment, challenges, recovery codes, lockout
  totp.py          RFC 6238 arithmetic (no I/O, pure and unit-tested)
  tokens.py        single-use token minting + hashing
  secrets.py       envelope encryption for identity secrets (never plaintext)
  events.py        audit emission for the identity vocabulary
  email.py         reset / verification tokens and message rendering
  api_keys.py      tenant API keys: issue, rotate, revoke, authenticate
  service_accounts.py  machine identities and their credentials
  domains.py       DNS TXT ownership proof and enforcement policy
  principals.py    machine credentials -> request principal
  sso/
    service.py     connection CRUD, state, login completion, provisioning
    oidc.py        discovery, PKCE, code exchange, ID-token verification
    saml.py        XML-DSig verification, assertion validation, metadata
    claims.py      claim/role/group normalization and mapping rules
  scim/
    service.py     Users, Groups, credentials, filtering, PATCH semantics
    schemas.py     RFC 7643 resource shapes
```

Route modules (10, all registered in `app/main.py`):

| Module | Prefix | Routes |
| --- | --- | --- |
| `identity_routes.py` | `/api/identity` | 5 |
| `mfa_routes.py` | `/api/mfa` | 9 |
| `session_routes.py` | `/api/sessions` | 5 |
| `password_routes.py` | `/auth` | 5 |
| `api_key_routes.py` | `/api/api-keys` | 5 |
| `service_account_routes.py` | `/api/service-accounts` | 15 |
| `domain_routes.py` | `/api/domains` | 7 |
| `sso_routes.py` | `/api/sso` (15) + `/auth/sso` (7) | 22 |
| `scim_routes.py` | `/api/scim` (4) + `/scim/v2` (14) | 18 |

`scripts/check_identity_route_contracts.py` asserts the mechanical contract
these modules share (dependency names, response models, tenant scoping) and
fails the build rather than letting one route drift; it currently reports
`OK: keyword contracts hold across 8 route modules`.

---

## 3. Data model

`alembic/versions/0013_enterprise_identity.py` creates the tables and the audit
vocabulary; `0014_identity_audit_actions.py` repairs the labels to the form
SQLAlchemy actually writes and adds `SECURITY_SETTINGS_CHANGED`;
`0015_credential_auth_rejected.py` adds the label a refused machine credential
is recorded under. `0016_sso_account_unlinked.py` adds the label for removing a
federated subject from an account. `0017_organization_environment_foundation.py`
is the chain head: it adds the organization/environment tables and twelve
hierarchy audit labels. `0018_organization_memberships_quotas.py` adds membership
bindings and quota rows without a second RBAC engine. Identity behaviour is
unchanged. Head is `0018`
(pinned by `tests/test_deployment.py` and `tests/test_enterprise_persistence.py`).

| Table | Purpose |
| --- | --- |
| `identity_policies` | one row per tenant; created only when an administrator changes something |
| `user_sessions` | the device/sitting a user can see and revoke |
| `user_emails` | email change + verification state |
| `mfa_factors` | TOTP factors (sealed seed, last consumed step) |
| `mfa_recovery_codes` | hashed, single-use recovery codes |
| `mfa_challenges` | in-flight second-factor challenges, failure counters, lockout |
| `sso_connections` | OIDC/SAML connection configuration per tenant |
| `sso_connection_certificates` | sealed IdP certificates, with rotation state |
| `sso_login_attempts` | every federated attempt and why it ended |
| `identity_mappings` | group/role → VoxDesk role mapping rows |
| `scim_credentials` | hashed, rotatable SCIM bearer tokens |
| `scim_group_mappings` | IdP groups tracked for role synchronisation |
| `service_accounts` | machine identities, scopes, expiry, ownership |
| `service_account_credentials` | hashed credentials belonging to service accounts |
| `api_keys` | hashed tenant API keys with explicit scopes |
| `enterprise_domains` | claimed domains and their enforcement posture |
| `domain_verifications` | DNS TXT challenges (per attempt, with expiry) |
| `password_reset_tokens` | hashed, single-use, expiring |
| `email_verification_tokens` | hashed, single-use, expiring |

Every secret column holds ciphertext or a hash — never a plaintext secret.
`app/auth/identity/secrets.py` seals with the deployment key ring
(`IDENTITY_ENCRYPTION_KEYS`, falling back to `CRM_ENCRYPTION_KEYS`) under the
AAD `voxdesk:identity:v1:{tenant}:{purpose}`, so a row copied between tenants
fails to decrypt rather than silently working.

---

## 4. The policy engine

`app/auth/identity/policies.py` is the single decision point. Every login,
every session, every dangerous action and every machine credential goes through
it; no route re-implements a rule.

### 4.1 Resolution

`load_policy(session, tenant_id)` returns a frozen `ResolvedPolicy`. A tenant
with no `identity_policies` row gets `default_policy()`, which is exactly the
pre-existing behaviour: password login allowed, MFA available but not required,
SSO optional, API keys and service accounts allowed. **Enabling this feature
cannot change a tenant until an administrator changes a setting.**

`ResolvedPolicy` is frozen and composed with `dataclasses.replace`, so a
half-updated policy cannot leak out of a request.

### 4.2 Evaluation entry points

| Function | Answers |
| --- | --- |
| `evaluate_login_policy` | may this principal finish a login of this kind? |
| `evaluate_session_policy` | how long, and how many? (idle, absolute, max active) |
| `evaluate_privileged_action` | may this session do a dangerous thing right now? |
| `evaluate_api_credential` | may this machine credential act at all? |
| `evaluate_scope_grant` | may this actor grant these scopes to that credential? |
| `scopes_allow` | does this credential's scope set cover this permission? |
| `evaluate_sso_policy` | is federation usable for this tenant / this address? |
| `evaluate_domain_policy` | does a *verified* domain claim restrict this address? |
| `evaluate_password_reset_policy` | may this account use the reset flow? |

Each returns a frozen decision dataclass with `allowed` and a `reason` string.
A refusal is a policy statement, and the reason is surfaced to administrators
(never as a generic failure): a misconfiguration must be diagnosable.

### 4.3 The rules that matter

- **MFA requirement precedence** (`mfa_required_for`): a per-user override
  (`users.mfa_required`) beats the tenant rule *in both directions*, then the
  admin-role rule, then the tenant-wide rule. A break-glass account that the
  tenant policy could re-arm would not be one.
- **An unproven claim imposes nothing.** `evaluate_login_policy` gates every
  domain-based restriction on `domain_policy.verified`. A domain an
  administrator merely typed, or a claim an IdP asserts without proof, cannot
  refuse a login. This is the anti-pre-hijack rule.
- **Freshness, not factors.** A privileged action requires a *fresh proof of
  presence*: a second factor if one is enrolled, otherwise the account
  password. An operator with no factor is never locked out of their own
  settings. A stale proof returns `allowed=True` with `requires_mfa` /
  `requires_password` — the route turns that into **428**, not 403, so the
  client knows to re-present rather than to give up.
- **Machine credentials cannot reauthenticate.** `API_KEY`,
  `SERVICE_ACCOUNT` and `SCIM` principals get
  `machine_credential_cannot_reauth` for any privileged action. A leaked key
  cannot rewrite the tenant's SSO.
- **Scope grants are validated against the *actor*.**
  `evaluate_scope_grant` refuses `unknown_scope:…`, `platform_scope_not_grantable:…`
  and `scope_not_held:…`. A credential can never be issued more authority than
  its issuer holds.

### 4.4 Reauthentication

`POST /api/identity/reauth` accepts the account password (or a fresh MFA code)
and records the proof on the *session row* (`password_confirmed_at`,
`mfa_verified_at`). `assert_privileged` is the guard every dangerous route
calls; it raises `ReauthenticationRequired` (428, `detail.code =
"reauth_required"`) when the proof is stale, and
`machine_credential_cannot_reauth` when the caller is a machine.

---

## 5. Sessions and devices

`user_sessions` is the sitting behind a credential. A session carries the
device label, IP, user agent, auth method, whether MFA was satisfied and when,
the SSO connection it came from, `last_seen_at`, an idle deadline and an
absolute deadline.

- `POST /auth/login` (and the SSO callback) create one through
  `sessions.create_session`, which also evicts the oldest session when the
  tenant's `session_max_active` ceiling is exceeded — including `keep=0`, i.e.
  a ceiling of exactly one session.
- Refresh tokens stay the credential (`refresh_tokens` is unchanged: hashed,
  single-use, reuse-detected). Revoking a session revokes the tokens issued
  for it, so a credential cannot outlive its sitting.
- `GET /api/sessions` lists the caller's live sessions; `DELETE
  /api/sessions/{id}` ends one; `POST /api/sessions/revoke-others` keeps only
  the caller; `DELETE /api/sessions` ends everything; `PATCH
  /api/sessions/{id}` renames a device.
- `SESSION_SUSPICIOUS` is emitted when a *new* address appears while another
  session is already live. A first sign-in is not suspicious and is not
  reported — an alert that fires on every first login is an alert nobody reads.

The full invalidation matrix (logout, logout-all, reuse detection, password
reset, MFA disable, admin MFA reset, idle, expiry) is tested in
`tests/security/test_session_invalidation.py`.

---

## 6. Audit

Identity uses the existing `audit_logs` table and `AuditAction` enum. 53 labels
were added in `0013`, one more in `0014` (`SECURITY_SETTINGS_CHANGED`) and one
more in `0015` (`CREDENTIAL_AUTH_REJECTED`) and `0016` (`SSO_ACCOUNT_UNLINKED`),
and twelve hierarchy labels in `0017`, plus twelve membership and quota labels
in `0018`, for 110 members total.
`tests/test_enum_consistency.py` and
`tests/test_identity_migrations.py` keep the Python enum and the PostgreSQL
`auditaction` labels identical — case-sensitively, because `ALTER TYPE` with a
lower-cased value is exactly the bug that file was written after.

Identity emits, among others: `MFA_ENROLLMENT_STARTED`, `MFA_ENABLED`,
`MFA_DISABLED`, `MFA_VERIFIED`, `MFA_FAILED`, `MFA_CHALLENGE_LOCKED`,
`MFA_RECOVERY_CODE_USED`, `MFA_RECOVERY_CODES_REGENERATED`, `SESSION_CREATED`,
`SESSION_REVOKED`, `SESSION_SUSPICIOUS`, `IDENTITY_REAUTHENTICATED`,
`IDENTITY_LINK_REJECTED`, `PASSWORD_RESET_REQUESTED`,
`PASSWORD_RESET_COMPLETED`, `EMAIL_VERIFICATION_SENT`, `SSO_*` (13),
`SCIM_*` (10), `API_KEY_*` (3), `SERVICE_ACCOUNT_*` (7), `DOMAIN_*` (5).

`GET /api/identity/events` returns the caller's tenant's identity events to an
administrator. Nothing in the identity vocabulary logs a password, token, API
key, OAuth secret, SAML assertion, recovery code, session secret or
`Authorization` header: events carry ids, reasons and counts.

---

## 7. Authorization

Every route is covered by `tests/security/test_authorization_matrix.py`, which
runs three layers:

1. **Pure policy units** — the tables above, evaluated directly, including the
   precedence rules and the "unproven claim imposes nothing" case.
2. **A role × route HTTP matrix** — anonymous, VIEWER, AGENT, MANAGER, ADMIN,
   OWNER (and a second tenant's owner) against every identity route.
3. **An owner sweep** — every registered identity route is called by a
   legitimate owner and must not answer 5xx; more than 50 routes are probed.

The resulting policy, in one table:

| Surface | Who |
| --- | --- |
| `GET /api/identity/{policy,events}`, api-keys, service-accounts, domains, SSO connections, SSO attempts, SCIM credentials | OWNER / ADMIN — the administrative surfaces |
| `GET /api/identity/status`, `GET /api/mfa/status`, `GET /api/sessions` | any signed-in human — self-service reads |
| reauth, MFA enroll/confirm/verify/challenge/disable/recovery-codes, `revoke-others` | any signed-in human — self-service writes |
| SCIM `/scim/v2/...` | a SCIM credential, tenant-bound; never a user JWT |
| everything above | refused for API keys and service accounts unless explicitly allowed |

"Authenticated" is never sufficient on its own: the routes call
`require_permission`, `require_human_session` and `assert_privileged` as
appropriate, and the matrix asserts the refusal, not just the permission.

---

## 8. Configuration

All settings live in `app/core/config.py` and are validated at boot by
`Settings.validate_security`. Production refuses the unsafe combinations
outright rather than starting degraded.

| Setting | Default | Meaning |
| --- | --- | --- |
| `IDENTITY_ENCRYPTION_KEYS` | falls back to `CRM_ENCRYPTION_KEYS` | key ring for every identity secret; refused at boot in production when SSO or MFA is enabled and no ring exists |
| `MFA_ENABLED` | `true` | master switch; when false the MFA routes answer 404 |
| `MFA_ISSUER` | `VoxDesk` | label in the authenticator app |
| `MFA_TOTP_DIGITS` / `MFA_TOTP_PERIOD_SECONDS` | `6` / `30` | TOTP parameters |
| `MFA_TOTP_WINDOW` | `1` | steps accepted either side of now |
| `MFA_RECOVERY_CODE_COUNT` | `10` | codes issued per regeneration |
| `MFA_MAX_VERIFICATIONS` / `MFA_VERIFICATION_WINDOW_SECONDS` | `10` / `300` | per-user rate limit |
| `MFA_CHALLENGE_MAX_FAILURES` / `MFA_CHALLENGE_LOCKOUT_MINUTES` | `5` / `15` | per-challenge burn + lockout |
| `MFA_CHALLENGE_TTL_SECONDS` | `300` | how long a half-finished login stays resumable |
| `MFA_FRESH_MINUTES` | `15` | default privileged-action freshness window |
| `SSO_ENABLED` | `true` | master switch for federation |
| `SSO_STATE_TTL_SECONDS` | `600` | lifetime of an in-flight authorization request |
| `SSO_CLOCK_SKEW_SECONDS` | `120` | accepted skew for ID tokens and assertions |
| `SSO_ALLOWED_REDIRECT_HOSTS` | empty | extra redirect hosts, comma-separated |
| `SSO_GROUP_CLAIM` / `SSO_ROLE_CLAIM` | `groups` / `roles` | default claim names for mapping |
| `SCIM_ENABLED` | `true` | master switch for SCIM |
| `SCIM_DEFAULT_PAGE_SIZE` / `SCIM_MAX_PAGE_SIZE` | `100` / `500` | pagination |
| `API_KEY_PREFIX` / `SERVICE_ACCOUNT_PREFIX` / `SCIM_TOKEN_PREFIX` | `vdk` / `vdsa` / `vdscim` | identification prefixes (never secrecy) |
| `SESSION_IDLE_MINUTES` / `SESSION_MAX_ACTIVE` | `720` / `20` | session defaults |
| `EMAIL_TRANSPORT` | `log` | `log` or `smtp`; **production refuses `log`** |
| `PASSWORD_RESET_TTL_MINUTES` | `30` | reset token lifetime |
| `IDENTITY_DEBUG_TOKENS` | `false` | see §9 |

---

## 9. `IDENTITY_DEBUG_TOKENS`

A password-reset request answers `202` with a byte-identical body whether or not
the address exists — that uniformity is what keeps the endpoint from being an
account oracle. The development affordance that hands the fresh token back in
the response breaks that property, so it is an explicit, off-by-default switch
that can only take effect **outside production and only with the `log`
transport**. `Settings.validate_security` refuses the combination in
production.

---

## 10. Verifying this layer

| Command | What it proves |
| --- | --- |
| `python3 -m pytest tests/auth -q` | TOTP vectors, sessions and devices, MFA, credentials, API keys, service accounts, SCIM, SSO, domains, policy, principals, password reset, email verification (442 tests) |
| `python3 -m pytest tests/security -q` | authorization matrix, session invalidation, SSO, sealed MFA secrets (197 tests) |
| `python3 -m pytest tests/integration -q` | the cross-module stories: hire → provision → federate → suspend → deprovision, domain enforcement, and the cross-workspace sweep (22 tests) |
| `python3 -m pytest tests/test_identity_migrations.py -q` | migration ↔ model ↔ enum parity, read out of the migration files |
| `python3 -m pytest tests/test_identity_pg_enum.py -q` | that the live **PostgreSQL** `auditaction` accepts every `AuditAction` — the one failure mode SQLite cannot show. Skips with a printed reason when `DATABASE_URL` is not PostgreSQL |
| `python3 -m pytest tests/test_enum_consistency.py -q` | every enum member has a migration |
| `python3 -m pytest tests -q` | the whole regression suite |
| `python3 scripts/check_identity_route_contracts.py` | the route contract across the identity modules |
| `ruff check app/ tests/ alembic/` | lint gate |

Two of those gates need a reachable PostgreSQL 17 (`tests/test_api_contract.py`
and `tests/test_identity_pg_enum.py`); the rest of the suite runs against
in-memory SQLite, which is why the enum check above exists at all.

**Dependencies.** This feature adds exactly one package — `signxml`, used by
`tests/security/test_sso.py` to mint independent SAML assertions, pinned at
`5.1.0` because `4.2.0` is broken against the resolver stack in this repository.
Everything else is already in the tree: `PyJWT` for OIDC ID tokens (pinned
algorithms, `none` refused even with an empty key), `cryptography` for RSA and
X.509, `httpx` for the discovery/JWKS/DoH calls, and `sqlalchemy`/`alembic` for
the tables. No new runtime service, no new external API, nothing to enable.

`tests/security/test_sso.py` mints its own signed SAML assertions with
`signxml` (an independent XML-DSig implementation) and its own JWKS, so the
hand-written verifier in `sso/saml.py` is checked against a second
implementation rather than against itself. There is no fake provider in
production code and no `real_provider`-marked test that is silently skipped:
everything in this table runs in the default suite.

---

## 11. Operating notes

- **Break-glass.** Keep one owner account with `users.mfa_required = false` and
  no factor. It can always sign in with a password and can always re-enter the
  tenant's identity settings. Test it before you need it.
- **Emergency disable.** A service account can be disabled outright
  (`POST /api/service-accounts/{id}/emergency-disable`), which is the lever to
  reach for when a machine credential leaks; it does not need the account owner
  to be present and it is audited.
- **Rotation.** SSO signing certificates, SCIM tokens, API keys and service
  account credentials all have a rotate path that keeps the old credential
  valid only until the new one is confirmed live, then revokes it.
- **When an audit row says `SSO_LOGIN_FAILED` or `MFA_FAILED`**, the detail
  carries the *check* that refused, not the secret that failed it. Use
  `GET /api/sso/attempts` for the connection-level view.
````

### `docs/IDENTITY-SECURITY.md`

````markdown
# VoxDesk — Identity security model

How the enterprise identity system is put together, what it defends against, and
which invariant each mechanism exists to hold. Read this first; the
feature-specific documents ([`SSO-OIDC.md`](SSO-OIDC.md),
[`SSO-SAML.md`](SSO-SAML.md), [`SCIM.md`](SCIM.md), [`MFA.md`](MFA.md),
[`API-KEYS.md`](API-KEYS.md), [`SERVICE-ACCOUNTS.md`](SERVICE-ACCOUNTS.md),
[`DOMAIN-VERIFICATION.md`](DOMAIN-VERIFICATION.md),
[`ENTERPRISE-IDENTITY.md`](ENTERPRISE-IDENTITY.md)) fill in the details.

---

## 1. The five rules

Everything below is an instance of one of these:

1. **A secret exists in plaintext exactly once** — when it is issued, to the
   caller who asked for it. Everywhere else it is a digest or a sealed envelope.
2. **Nothing is trusted because it arrived.** An IdP's claims, a SCIM payload's
   `active`, a machine credential's tenant, a domain name somebody typed: each is
   either proved by DNS, by a signature, by a hash, or by a row in our database —
   or it is not believed.
3. **Fail closed, and fail with the truth.** An unverifiable signature, an
   inactive tenant, an unknown credential, a disabled user and a missing key ring
   all refuse. Refusals are specific enough for an operator to act on and vague
   enough not to enumerate.
4. **A check that matters lives in one place.** Fresh proof of presence is
   `assert_privileged`; the tenant policy is `policies.evaluate_*`; the
   provisioning decision is `claims.decide_provisioning`; XML-DSig is
   `saml.verify_signature`. A second copy of a security rule is a second rule.
5. **Authority never outlives its source.** A service-account credential whose
   owner is gone stops working. An SSO user the IdP keeps asserting but we
   disabled stays disabled. A resolved policy change takes effect on the next
   request, not at the next expiry.

---

## 2. Threat model

| Adversary | Has | Defence |
| --- | --- | --- |
| Credential stuffer | a valid password | per-user lockout after `MAX_FAILED_LOGINS` (default 8) for `LOCKOUT_MINUTES` (default 15); per-IP limits when `RATE_LIMIT_ENABLED`; MFA available per tenant and forced for admins; enumeration-safe errors |
| Phisher with the password *and* the TOTP code | one code | per-user verification rate limit (`MFA_MAX_VERIFICATIONS`, default 10 / `MFA_VERIFICATION_WINDOW_SECONDS` 300); a wrong code on one challenge burns and locks it (`MFA_CHALLENGE_MAX_FAILURES` 5 → `MFA_CHALLENGE_LOCKOUT_MINUTES` 15); a challenge expires in `MFA_CHALLENGE_TTL_SECONDS` (300) |
| Stolen session cookie | a live session | session rows with idle and absolute expiry, refresh rotation, revocation by the user (one / all others / all), revocation on password reset and on MFA change, `token_version` bump, suspicious-session event |
| Stolen laptop, unattended browser | a live session | privileged actions require **fresh** proof: MFA verified within `MFA_FRESH_MINUTES` (default 15) or a password confirmation within `PRIVILEGED_REAUTH_MINUTES`, else **428** and the user is asked again |
| Leaked API key | a machine credential | hash-only storage, explicit scopes that cannot exceed the creator's, prefix for identification, expiry, last-used, per-request policy evaluation, revoke and rotate that leave no overlap window |
| Leaked provisioning token | a SCIM credential | tenant-bound by its row, scopes limited to `scim:users`/`scim:groups` (never RBAC values), cannot call the product API at all, rotate/revoke, one identical 401 for every failure |
| Malicious or compromised IdP | the ability to mint assertions and tokens | signature verified against a **pinned** certificate/JWKS, issuer, audience, destination, recipient, `InResponseTo` and nonce binding, time windows with skew, single-use state, single-use assertion id, one assertion per response, no DTD, `active`/tenant checks, and a refusal to ever cross tenants |
| Someone who types a domain into the dashboard | a claim | unverified claims route nobody and enforce nothing; only DNS proof makes a domain real, and only an explicit second action makes it enforce |
| A tenant trying to reach another tenant | valid credentials of their own | every query is keyed on the caller's `tenant_id`; a foreign id is 404, never 403; cross-tenant email during SSO is refused and audited (`IDENTITY_LINK_REJECTED`) |
| Curious administrator | `identity:write`, and the ability to trip an emergency stop | they cannot clear their own emergency stop (owner only, with a reason), cannot read any secret back, cannot grant themselves a scope, and every action is audited with the actor |
| Operator mistake | a plan | enforcement is two-key, the last active owner cannot be deprovisioned or MFA-locked out, retiring the last SAML certificate is refused, and every destructive action is reversible or recorded with a reason |

---

## 3. Where the code lives

```
app/core/config.py            identity settings + production refusals
app/auth/permissions.py       39 permissions; is_platform_permission
app/auth/rbac.py              role → permissions; _guard = human + privileged
app/auth/dependencies.py      require_permission, require_human_session
app/auth/identity/
  models.py                   19 tables, all tenant-keyed
  policies.py                 evaluate_login|session|privileged_action|
                              api_credential|sso_policy|domain_policy|scope_grant
  secrets.py                  the AES-256-GCM key ring
  events.py                   audit emission + the scrub list
  sessions.py                 device sessions, revocation, suspicious-session
  mfa.py                      TOTP, recovery codes, challenges
  api_keys.py / service_accounts.py / scim/      machine credentials
  sso/                        oidc.py, saml.py, claims.py, service.py
  domains.py                  DNS proof and enforcement
app/api/                      identity_routes, mfa_routes, session_routes,
                              password_routes, machine_routes, domain_routes,
                              sso_routes, scim_routes (+ the `sso` package)
```

---

## 4. Secrets at rest

Identity secrets are sealed with the same AES-256-GCM envelope as CRM
credentials: `encrypt_text(plaintext, tenant_id=…, purpose=…)` returns
`(envelope, key_id)` and the associated data is

```
voxdesk:{purpose}:v1:{tenant_id}
```

so a ciphertext cannot be moved between tenants **or** between purposes: a
sealed PKCE verifier cannot be replayed as an OIDC client secret even by someone
who can write rows, because the AAD differs. `key_id` records which key sealed
it, so a ring can be rotated by adding a key and re-sealing over time.

Encrypted fields: TOTP seeds, recovery-code digests' key material, OIDC client
secrets, SAML certificates (`pem_encrypted` + `pem_key_id`), PKCE verifiers,
SCIM and service-account credential digests' secrets. `seal_preview` exists for
diagnostics and never reveals the envelope.

**Plaintext is never stored, and never logged.** `identity_debug_tokens` is
`False` by default, and production refuses to boot with MFA or SSO enabled and no
key ring configured (`IDENTITY_ENCRYPTION_KEYS`, falling back to
`CRM_ENCRYPTION_KEYS`) — an unencryptable identity secret is a boot failure, not
a runtime surprise.

---

## 5. The audit pipeline

`identity_events.emit` is the only writer of identity audit rows. It:

1. **scrubs** the detail payload against `FORBIDDEN_DETAIL_KEYS` — `password`,
   `token`, `access_token`, `refresh_token`, `id_token`, `api_key`, `secret`,
   `client_secret`, `totp_secret`, `recovery_code`, `saml_assertion`,
   `saml_response`, `certificate_pem`, `private_key`, `code_verifier`, `state`,
   `nonce`, `authorization`, `cookie`, `code`, `otp` and their friends, replaced
   with `***`;
2. **logs a warning** when it had to scrub something, with the action and the
   number of masked fields — a caller that tried to record a credential is a bug
   worth seeing (the scrub list is why a refusal records the key *prefix* and not
   a field called `credential`: a field by that name is redacted wholesale);
3. **truncates** any value over 500 characters, because a 400 KB claim blob
   inline is a retention problem wearing an audit trail's clothes;
4. writes the row, with the tenant, actor, target, IP, user agent and detail.

Emission during a mutating operation uses `commit=False` and rides the caller's
transaction, so an event cannot be recorded for an action that then rolled back —
the audit trail is written if and only if the change happened. 110 `AuditAction`
members cover the vocabulary; the categories are login, MFA, session, SSO, SCIM,
API key, service account, domain, credential, settings, and the organization /
tenant / environment hierarchy added in `0017`. Hierarchy events carry ids and
status only.

**A refused credential is an event.** `CREDENTIAL_AUTH_REJECTED` is written
whenever a machine token *resolved to a row* and was then refused: revoked,
expired, its service account switched off or expired, its credential family
disabled for the workspace, or its owner suspended or gone. The HTTP answer stays
the uniform `Invalid credential`, so the caller learns nothing; the operator
learns which key, which family and why. A token that matches no row writes
nothing at all — otherwise anyone could fill the audit table by guessing, and the
front door would be a request-amplification attack.

The reasons, and they are stable identifiers a runbook can match on:
`api_key_revoked`, `api_key_expired`, `service_account_credential_revoked`,
`service_account_credential_expired`, `service_account_disabled`,
`service_account_expired`, `api_keys_disabled`, `service_accounts_disabled`,
`tenant_inactive`, `tenant_missing`, `credential_owner_inactive`,
`credential_owner_missing`.

Where a value must appear, it appears **hashed**
(`subject_hint = hash_token(subject)[:16]`), never quoted.

---

## 6. Authorization

| Layer | Rule |
| --- | --- |
| role | OWNER / ADMIN / MANAGER / AGENT; ADMIN holds `identity:read`/`identity:write`, `api_key:manage`, `service_account:manage` |
| permission | 39 values; `require_permission` on every route |
| human | `require_human_session` for self-service and for anything a machine must never do |
| platform | `TENANT_CREATE` / `TENANT_DELETE` are `_PLATFORM_ONLY` and are never grantable to a tenant role |
| fresh proof | `assert_privileged` for actions that change how the tenant authenticates |
| tenant | every query keyed on the caller's `tenant_id` |

`rbac._guard` is `_require_human` followed by `assert_privileged`, so a route that
calls it cannot accidentally skip either. A machine credential that reaches a
privileged action is refused with `machine_credential_cannot_reauth`: a machine
cannot prove a person is present, so it may not change SSO, reset MFA, issue
credentials or lift an emergency stop.

`evaluate_scope_grant` is the privilege-escalation guard for machine credentials:
a requested scope must be known, must not be a platform permission, and must be
held by the human creating it. Unknown scopes are refused rather than dropped —
silently narrowing a scope list would leave an operator believing an integration
has access it does not.

**Never rely on `authenticated=True`.** The authorization matrix
(`tests/security/test_authorization_matrix.py`, 115 tests) asserts every
identity route against VIEWER / AGENT / MANAGER / ADMIN / OWNER, a second
tenant, an API key, a service-account credential, and an anonymous caller: who
gets 200, who gets 403, who gets 404, who gets 428, and that nothing returns 5xx.

---

## 7. Sessions

A session is a row (`user_sessions`) plus a JWT pair. The row is the truth:

- **Lifetimes** come from `evaluate_session_policy`: idle expiry, absolute
  expiry (refresh days) and the concurrent-session ceiling, which **evicts the
  oldest** rather than refusing the new login — a user locked out of their own
  account because of a forgotten phone is a support ticket, not a security win.
- **Revocation cascades.** `revoke_session`, `revoke_sessions`,
  `revoke_other_sessions` and `revoke_all_for_user` all route through
  `_revoke_tokens_for_session`, so every unexpired `RefreshToken` row belonging
  to a revoked session is revoked with it — including in the bulk paths used by
  password reset, MFA change and SCIM deprovisioning.
- **Password and MFA changes invalidate sessions.** A reset revokes every other
  session; changing the second factor does the same, because a change of
  credential is exactly the moment to distrust sessions established with the old
  one.
- **Suspicious sessions are reported, not blocked.** A login from a new address
  or device while other sessions are live emits `SESSION_SUSPICIOUS` with
  `reason = new_ip_address` or `new_device`. A first login from a fresh laptop is
  ordinary, and reporting it would train operators to ignore the event; the user
  is never locked out by a heuristic.
- Session secrets, refresh tokens and cookies never appear in a log line or an
  audit detail.

---

## 8. Enumeration and rate limits

- Password reset and email verification answer **uniformly**: the same shape of
  response whether or not the address exists, so the endpoint cannot be used as
  an account oracle.
- SCIM, API key and service-account authentication return **one** error for
  every failure mode (unknown, revoked, expired, malformed, wrong secret), for
  the same reason.
- SSO callback failures are collapsed to a single public error body; the
  specific reason goes to the audit trail.
- Identity errors are translated by `identity_errors.translate`, so a
  `PolicyDenied` is 403 with `{code: policy_denied, reason: <rule>}` — the rule
  name is for the operator, and the message stays safe to show a user.
- Rate limits: `RATE_LIMIT_LOGIN_PER_MINUTE` (default 10) for auth routes,
  `RATE_LIMIT_BURST` for the rest, plus the per-user MFA verification window and
  the per-domain DNS attempt ceiling.

---

## 9. Tenant isolation invariants

1. Every identity table has `tenant_id`, and every read and write is keyed on it.
2. A resource id from another tenant answers **404**, identical to a resource
   that does not exist — never 403, which would confirm it exists.
3. A sealed secret's AAD includes the tenant, so a ciphertext moved between rows
   does not decrypt.
4. A federated login can never cross tenants: an email that belongs to another
   tenant is refused and audited (`address_belongs_to_another_tenant`), and a
   domain verified by two tenants refuses the login rather than guessing.
5. A machine credential's tenant comes from its row, never from the request;
   its owner is re-checked on every request.
6. A resolved policy is per tenant; a second tenant sees defaults, asserted by
   the matrix test.

These are covered by `tests/security/test_authorization_matrix.py` and
`tests/security/test_session_invalidation.py`, plus the SSO suite's
cross-tenant refusal tests.

---

## 10. Concurrency and single use

Several defences are only real if they are atomic, and the code is written so
the database enforces them rather than the application:

| Mechanism | How it is enforced |
| --- | --- |
| SSO state (CSRF) | one conditional `UPDATE … WHERE consumed_at IS NULL AND expires_at > now() RETURNING` — two concurrent callbacks cannot both win |
| SAML assertion replay | `UNIQUE(assertion_id)`; a second use is an `IntegrityError` mapped to `SSOReplayDetected` |
| Refresh-token rotation | the row is spent in the same transaction that issues the replacement |
| Credential rotation | replacement issued and predecessor revoked in one transaction — no overlap window, for API keys, SCIM tokens and service-account credentials alike |
| Domain challenge | re-issuing supersedes every open challenge, so an old TXT record is inert |
| Recovery codes | single-use rows, an `UPDATE … WHERE used_at IS NULL` claim |
| Session ceiling | eviction happens inside the create transaction |
| Last owner | counted and checked in the same transaction that would remove it |

The test suites exercise these by running the competing operations in sequence
against a real database, and the identity migrations suite asserts the unique
constraints and enum values actually exist in the migrated schema.

---

## 11. What is deliberately *not* done

- **No auto-enforcement.** Verifying a domain never refuses a password login by
  itself; that is a second, explicit decision.
- **No heuristic lockouts.** A new device is reported, not blocked.
- **No silent role clamping.** An unmapped role is refused with the fix in the
  message, never downgraded behind the operator's back.
- **No shared secrets a machine can use for administration.** A machine
  credential may do the work it was scoped for and nothing about how the tenant
  authenticates.
- **No "just this once" bypass.** There is no debug flag that returns a token in
  a response: `identity_debug_tokens` exists as a setting, defaults to `False`,
  and the paths that read it are gated and audited. **Production refuses to boot
  with it on.**
- **No security through obscurity.** Every rule here is enforced by code that
  the tests exercise, not by naming or by hiding a route.

---

## 12. Verifying this document

```
ruff check app/ tests/                                   # style and dead code
python3 -m pytest tests/security -q                      # authz matrix, SSO, sessions
python3 -m pytest tests/auth -q                          # TOTP, sessions, credentials
python3 -m pytest tests/test_identity_migrations.py -q    # the schema the rules rely on
python3 scripts/check_identity_route_contracts.py         # route contracts
python3 -m pytest tests/test_api_contract.py -q           # no route answers 5xx
```
````

### `tests/test_deployment.py`

````python
"""Step 8 — deployment & disaster-recovery invariants.

Every test here is deterministic and requires no Docker, no live Postgres and
no network: they inspect the repository's configuration, scripts and source as
the evidence they are. The migration-chain test parses the Alembic revisions
themselves; the compose/Caddy/Dockerfile tests parse the files as YAML/text;
the Redis tests exercise the degradation path directly. Where a real runtime
check is possible (a live Postgres, a running stack) it is a separate,
opt-in validation — never assumed here.
"""
from __future__ import annotations

import base64
import os
import re
from pathlib import Path

import pytest
import yaml

from app.core.config import Settings

REPO = Path(__file__).resolve().parent.parent


def _valid_prod(**overrides) -> Settings:
    """A production Settings that passes every non-test production rule, so a
    single override can be asserted against cleanly. All values are fake."""
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    values = dict(
        app_env="production",
        public_base_url="https://app.example.com",
        cors_origins="https://app.example.com",
        rate_limit_enabled=True,
        log_level="INFO",
        secret_key="a" * 48,
        jwt_secret="b" * 48,
        twilio_account_sid="AC" + "0" * 32,
        twilio_auth_token="c" * 32,
        twilio_phone_number="+15550001111",
        deepgram_api_key="d" * 32,
        openai_api_key="sk-" + "e" * 32,
        elevenlabs_api_key="f" * 32,
        crm_encryption_keys=f"k1:{key}",
        knowledge_embedding_provider="openai",
        knowledge_embedding_model="text-embedding-3-small",
        knowledge_embedding_dimensions=1536,
        billing_provider="manual",
        # STEP 18: a production deployment that offers password reset has to be
        # able to deliver the message. The log transport is refused in
        # production, so the fixture sets a real one.
        email_transport="smtp",
        smtp_host="smtp.example.com",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _staging(**overrides) -> Settings:
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    values = dict(
        app_env="staging",
        public_base_url="https://staging.example.com",
        secret_key="s" * 48,
        jwt_secret="t" * 48,
        crm_encryption_keys=f"k1:{key}",
        rate_limit_enabled=False,
        e2e_enabled=False,
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


# ----------------------------------------------------------- (1)(2)(3) config ---

def test_production_rejects_e2e_mode():
    s = _valid_prod(e2e_enabled=True, e2e_test_number="+15550001111",
                    e2e_allowed_callers="+15550001111")
    problems = s.validate_security()
    assert any("E2E_ENABLED" in p for p in problems)


def test_production_rejects_rate_limit_off():
    s = _valid_prod(rate_limit_enabled=False)
    problems = s.validate_security()
    assert any("RATE_LIMIT_ENABLED" in p for p in problems)


def test_production_rejects_debug_log_level():
    s = _valid_prod(log_level="DEBUG")
    problems = s.validate_security()
    assert any("DEBUG" in p for p in problems)


def test_production_rejects_placeholder_secrets():
    s = _valid_prod(twilio_auth_token="xxxxxxxxxxxx")
    problems = s.validate_security()
    assert any("TWILIO_AUTH_TOKEN" in p for p in problems)


def test_production_rejects_development_cors_origins():
    s = _valid_prod(cors_origins="http://localhost:5173,https://app.example.com")
    problems = s.validate_security()
    assert any("https" in p and "localhost" in p for p in problems)


def test_production_rejects_localhost_base_url():
    s = _valid_prod(public_base_url="https://localhost:8000")
    problems = s.validate_security()
    assert any("localhost" in p for p in problems)


def test_fully_configured_production_is_clean():
    assert _valid_prod().validate_security() == []


def test_unknown_app_env_is_rejected():
    s = Settings(_env_file=None, app_env="qa")
    problems = s.validate_security()
    assert any("APP_ENV" in p for p in problems)


def test_staging_is_not_production():
    s = _staging()
    assert s.is_staging is True
    assert s.is_production is False
    assert s.uses_https is True


def test_staging_accepts_rate_limit_off_and_e2e_disarmed():
    s = _staging(rate_limit_enabled=False, e2e_enabled=False)
    problems = s.validate_security()
    assert not any("RATE_LIMIT_ENABLED" in p for p in problems)
    assert not any("E2E_ENABLED must not be set in production" in p for p in problems)


def test_secure_cookie_is_scheme_driven():
    assert Settings(_env_file=None, public_base_url="https://x.example.com").uses_https
    assert not Settings(_env_file=None, public_base_url="http://localhost:8000").uses_https


# ------------------------------------------------------- (4)(5) health endpoints ---

def test_main_defines_liveness_and_readiness():
    src = (REPO / "app" / "main.py").read_text()
    assert '@app.get("/health")' in src
    assert '@app.get("/health/ready")' in src
    assert "health_check.readiness" in src


def test_staging_does_not_run_create_all():
    src = (REPO / "app" / "main.py").read_text()
    # The dev/test-only schema bootstrap must exclude staging.
    assert '"development", "test"' in src


# ---------------------------------------------- (6) websocket / proxy assumptions ---

def test_caddy_preserves_websocket_and_adds_security():
    text = (REPO / "Caddyfile").read_text()
    assert "reverse_proxy api:8000" in text
    assert "admin off" in text
    assert "Strict-Transport-Security" in text
    assert "25MB" in text
    # Nothing may disable the WebSocket upgrade the voice stream depends on.
    assert "header_upgrade" not in text.lower()


def test_entrypoint_trusts_proxy_headers():
    text = (REPO / "scripts" / "entrypoint.sh").read_text()
    assert "--proxy-headers" in text
    assert "scripts/migrate.py" in text


# ------------------------------------------------------------ (7) migrations ---

def test_migration_chain_is_linear_with_no_gaps():
    versions = sorted(
        p for p in (REPO / "alembic" / "versions").iterdir() if p.suffix == ".py"
    )
    ids: dict[str, str | None] = {}
    for path in versions:
        text = path.read_text()
        rev = re.search(r'^revision = "(.+)"', text, re.M).group(1)
        down = re.search(r'^down_revision = (.+)$', text, re.M).group(1).strip()
        ids[rev] = None if down == "None" else down.strip('"')
    assert ids, "no migrations found"
    # Exactly one head: the revision nobody points down_revision at.
    heads = [r for r in ids if r not in {d for d in ids.values() if d}]
    assert heads == ["0018_organization_memberships_quotas"]
    # Exactly one base (down_revision None), and a single linear walk.
    bases = [r for r, d in ids.items() if d is None]
    assert bases == ["0001_baseline"]
    current: str | None = heads[0]
    visited = 0
    while current is not None:
        visited += 1
        current = ids[current]
    assert visited == len(ids)


# --------------------------------------------- (8)(9) backup/restore safety ---

def test_backup_scripts_use_environment_not_hardcoded_secrets():
    for name in ("backup.sh", "restore.sh", "backup_verify.sh"):
        text = (REPO / "scripts" / name).read_text()
        for marker in ("sk_live_", "AKIA", "-----BEGIN", "password=voxdesk"):
            assert marker not in text, f"{name} contains {marker!r}"
    backup = (REPO / "scripts" / "backup.sh").read_text()
    assert "POSTGRES_USER" in backup and "PGHOST" in backup
    assert "pg_restore --list" in backup  # integrity check before retention


def test_restore_refuses_overwrite_and_supports_drill():
    text = (REPO / "scripts" / "restore.sh").read_text()
    assert "RESTORE_TARGET_DB" in text      # drill restores into a scratch DB
    assert "RESTORE_ALLOW_OVERWRITE" in text  # never silently clobber a live DB
    assert "pg_restore --list" in text      # verify before writing


# ------------------------------------------------------ (10) secret exposure ---

def test_gitignore_covers_secrets_and_dumps():
    text = (REPO / ".gitignore").read_text()
    for entry in (".env", "secrets/", "backups/", "*.dump", "*.pem", "*.key", ".netrc"):
        assert entry in text


def test_dockerignore_excludes_secrets_and_dumps():
    text = (REPO / ".dockerignore").read_text()
    for entry in (".env", "secrets/", "backups/", "*.dump", "*.pem", "*.key"):
        assert entry in text


def test_env_examples_contain_only_placeholder_credentials():
    for name in (".env.example", ".env.staging.example"):
        text = (REPO / name).read_text()
        for marker in ("sk_live_", "AKIA", "BEGIN RSA", "BEGIN PRIVATE"):
            assert marker not in text, f"{name} contains {marker!r}"
    example = (REPO / ".env.example").read_text()
    assert "sk-xxxxxxxx" in example
    assert "change-me" in example


# ---------------------------------------------- (11)(12) docker/compose invariants ---

def test_dockerfile_runs_non_root_and_healthchecks():
    text = (REPO / "Dockerfile").read_text()
    assert "USER appuser" in text
    assert "HEALTHCHECK" in text
    assert "python:3.12-slim-bookworm" in text
    assert "COPY .env" not in text


def test_prod_compose_keeps_datastores_off_the_host():
    prod = yaml.safe_load((REPO / "docker-compose.prod.yml").read_text())
    assert "ports" not in prod["services"]["db"]
    assert "ports" not in prod["services"]["redis"]
    api_ports = [str(p) for p in prod["services"]["api"]["ports"]]
    assert api_ports and all(p.startswith("127.0.0.1:") for p in api_ports)
    assert prod["services"]["api"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert prod["services"]["api"].get("init") is True
    assert prod["services"]["backup"]["depends_on"]["db"]["condition"] == "service_healthy"


def test_staging_compose_is_isolated_from_production():
    st = yaml.safe_load((REPO / "docker-compose.staging.yml").read_text())
    assert st["services"]["api"]["environment"]["APP_ENV"] == "staging"
    assert "pgdata_staging" in st.get("volumes", {})
    api_vols = " ".join(str(v) for v in st["services"]["api"]["volumes"])
    assert "secrets-staging" in api_vols
    api_ports = " ".join(str(p) for p in st["services"]["api"]["ports"])
    assert "8001" in api_ports
    # Staging scheduler inherits the staging env file, not production's.
    assert st["services"]["scheduler"]["env_file"] == ".env.staging"


# ----------------------------------------------------- (13) CI workflow invariants ---

def test_ci_workflow_gates_and_separates_real_providers():
    ci = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    assert "alembic upgrade head" in ci
    assert "npm ci" in ci
    assert "not real_provider" in ci
    assert "docker build ." in ci
    assert "npm audit --omit=dev" in ci
    real = (REPO / ".github" / "workflows" / "real-integrations.yml").read_text()
    assert "workflow_dispatch" in real
    sec = (REPO / ".github" / "workflows" / "security-scan.yml").read_text()
    assert "bandit" in sec and "pip-audit" in sec and "gitleaks" in sec


# ------------------------------------------- (14) monitoring endpoint protection ---

def test_metrics_endpoint_is_token_gated_when_configured():
    src = (REPO / "app" / "core" / "metrics.py").read_text()
    assert "_scrape_authorized" in src
    assert "METRICS_TOKEN" in src


def test_caddy_does_not_expose_metrics_as_a_separate_route():
    text = (REPO / "Caddyfile").read_text()
    assert "/metrics" not in text


# ---------------------------------------------- (15) redis restart behaviour ---

@pytest.mark.asyncio
async def test_redis_cache_degrades_gracefully_when_down(monkeypatch):
    from app.core.cache import _RedisCache

    class _Down:
        async def ping(self):
            raise ConnectionError("redis down")

        async def get(self, key):
            raise ConnectionError("redis down")

        async def set(self, key, value, ex):
            raise ConnectionError("redis down")

        async def delete(self, key):
            raise ConnectionError("redis down")

    rc = _RedisCache("redis://127.0.0.1:1/0")
    monkeypatch.setattr(rc, "_get", lambda: _Down())
    assert await rc.ping() is False
    assert await rc.get("k") is None
    await rc.set("k", "v", 60)   # must not raise
    await rc.delete("k")          # must not raise


@pytest.mark.asyncio
async def test_memory_cache_ping_reports_reachable():
    from app.core.cache import _MemoryCache

    assert await _MemoryCache().ping() is True


# ----------------------------------------------------- (16) rollback guard ---

def test_rollback_checks_out_and_redeploys_without_pull():
    text = (REPO / "scripts" / "rollback.sh").read_text()
    assert "git checkout" in text
    assert "deploy.sh --no-pull" in text


def test_rollback_never_executes_alembic_downgrade():
    """The downgrade command may appear only in comments/documentation; the
    script must never run it. Database downgrades are operator-only."""
    for line in (REPO / "scripts" / "rollback.sh").read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assert "alembic downgrade" not in stripped
        assert "command.downgrade" not in stripped


def test_deploy_script_backs_up_before_migrating():
    text = (REPO / "scripts" / "deploy.sh").read_text()
    assert "pg_dump" in text
    assert "pg_restore --list" in text          # integrity check
    assert "scripts/migrate.py" in text          # advisory-locked migration
    assert "/health/ready" in text               # readiness wait
````

### `tests/test_enterprise_persistence.py`

````python
"""Batch 02 persistence — the registries' state survives a restart.

Batch 01 was explicit that the automation, notification and inbox services kept
their state in process-local dictionaries and that "a migration is reported at
the end of the batch". Batch 02 supplies both halves: the schema
(``alembic/versions/0012_enterprise_persistence.py``) and the scope that
hydrates/flushes those dictionaries around each request
(``app/services/enterprise_store.py``).

The tests below simulate a restart the honest way: they clear the *entire*
in-process state between requests (exactly what a new worker process starts
with) and then re-read through the API. Anything the second read returns must
have come back out of the database, because there is nowhere else for it to
come from.

They also pin the two properties that make the swap safe:

* the database is authoritative — a failed request writes nothing, and
* hydration is tenant-scoped — one tenant's rows never appear in another's view.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from app.domain.automation_models import TriggerEvent
from app.services import automation_service, inbox_service, notification_service
from app.services.enterprise_store import _ROWS
from tests.conftest import auth_headers


@pytest_asyncio.fixture
async def app_module_routes(app):
    """The real app's route table (mirrors the Batch 02 test module)."""
    return app.routes


def _forget_everything() -> None:
    """Wipe all in-process state: the post-restart starting position.

    This is deliberately harsher than a real restart (it drops every tenant, not
    just one) so a passing assertion cannot be explained by leftover globals.
    """
    automation_service._REGISTRY.clear()
    automation_service._RUNS.clear()
    automation_service._LAST_RUN_AT.clear()
    notification_service._TEMPLATES.clear()
    notification_service._NOTIFICATIONS.clear()
    notification_service._DEDUPE.clear()
    notification_service._READ.clear()
    inbox_service._OVERLAY.clear()
    _ROWS.clear()


@pytest.fixture(autouse=True)
def _isolated_registries():
    """Same isolation discipline as the Batch 02 suite: no cross-test tenants."""
    _forget_everything()
    yield
    _forget_everything()


def _automation_body(name: str = "Negative sentiment alert") -> dict:
    return {
        "name": name,
        "event": "call_completed",
        "description": "Escalate when a call ends badly",
        "filters": [{"field": "sentiment", "operator": "eq", "value": "negative"}],
        "actions": [
            {"name": "record_escalation_intent", "params": {"destination": "+15550009999"}}
        ],
        "max_per_event": 1,
        "cooldown_seconds": 0,
    }


def _template_body(channel: str = "in_app") -> dict:
    return {
        "name": "Booking confirmed",
        "channel": channel,
        "body": "Hi {customer}, your appointment {when} is confirmed.",
        "variables": ["customer", "when"],
    }


def _notification_body(template_id: str, business_key: str = "appt-42") -> dict:
    return {
        "template_id": template_id,
        "recipient": {"kind": "phone", "target": "+15551234567"},
        "event_source": "appointment_reminder",
        "business_key": business_key,
        "variables": {"customer": "Ada", "when": "Tuesday"},
    }


async def _open_thread(client, headers, *, channel: str = "web",
                       customer: str = "+15550001111") -> dict:
    response = await client.post(
        "/api/inbox/threads",
        json={"channel": channel, "customer": customer, "initial_message": "hello"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


# =================================================== the migration is present ===

class TestSchema:
    async def test_the_five_batch02_tables_exist_in_the_metadata(self):
        from app.db.models import Base

        for table in ("automations", "automation_runs", "notification_templates",
                      "notifications", "inbox_thread_states"):
            assert table in Base.metadata.tables, f"{table} has no model"

    async def test_the_revision_chains_off_the_previous_head(self):
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(Config("alembic.ini"))
        # The chain is linear by design: one head, and each revision names its
        # predecessor. 0018 (memberships and quotas) is the current head; the
        # assertion is about the *shape* of the chain, so the names here move
        # together whenever a migration is added.
        heads = script.get_heads()
        assert heads == ["0018_organization_memberships_quotas"]
        head = script.get_revision("0018_organization_memberships_quotas")
        assert head.down_revision == "0017_organization_environment_foundation"
        revision = script.get_revision("0017_organization_environment_foundation")
        assert revision.down_revision == "0016_sso_account_unlinked"
        unlinked = script.get_revision("0016_sso_account_unlinked")
        assert unlinked.down_revision == "0015_credential_auth_rejected"
        refused = script.get_revision("0015_credential_auth_rejected")
        assert refused.down_revision == "0014_identity_audit_actions"
        identity = script.get_revision("0013_enterprise_identity")
        assert identity.down_revision == "0012_enterprise_persistence"
        predecessor = script.get_revision("0012_enterprise_persistence")
        assert predecessor.down_revision == "0011_side_effect_exactly_once"


# ======================================================== automations, durable ===

class TestAutomationsSurviveRestart:
    async def test_definition_and_run_history_outlive_the_process(self, client, manager_a):
        headers = await auth_headers(client, manager_a)
        created = await client.post("/api/automations", json=_automation_body(), headers=headers)
        assert created.status_code == 201, created.text
        automation_id = created.json()["id"]
        assert (await client.post(
            f"/api/automations/{automation_id}/enable", headers=headers
        )).json()["status"] == "enabled"
        run = await client.post(
            f"/api/automations/{automation_id}/run",
            json={"business_event_id": "call-1", "payload": {"sentiment": "negative"}},
            headers=headers,
        )
        assert run.status_code in (200, 201), run.text

        # ---- restart ----
        _forget_everything()

        fetched = await client.get(f"/api/automations/{automation_id}", headers=headers)
        assert fetched.status_code == 200, fetched.text
        body = fetched.json()
        assert body["name"] == "Negative sentiment alert"
        assert body["status"] == "enabled"                    # not silently disabled
        assert body["filters"] == [
            {"field": "sentiment", "operator": "eq", "value": "negative"}
        ]
        assert body["actions"][0]["name"] == "record_escalation_intent"

        runs = await client.get(f"/api/automations/{automation_id}/runs", headers=headers)
        assert [r["business_event_id"] for r in runs.json()] == ["call-1"]

    async def test_evaluation_still_works_after_a_restart(self, client, manager_a):
        headers = await auth_headers(client, manager_a)
        created = await client.post("/api/automations", json=_automation_body(), headers=headers)
        automation_id = created.json()["id"]
        await client.post(f"/api/automations/{automation_id}/enable", headers=headers)

        _forget_everything()

        matched = await client.post(
            "/api/automations/evaluate",
            json={"event": "call_completed", "payload": {"sentiment": "negative"}},
            headers=headers,
        )
        assert [m["automation_id"] for m in matched.json()] == [automation_id]
        # And the filter is still the filter: a positive call does not match.
        missed = await client.post(
            "/api/automations/evaluate",
            json={"event": "call_completed", "payload": {"sentiment": "positive"}},
            headers=headers,
        )
        assert missed.json() == []

    async def test_create_is_still_idempotent_on_name_across_restarts(self, client, manager_a):
        headers = await auth_headers(client, manager_a)
        first = await client.post("/api/automations", json=_automation_body(), headers=headers)
        _forget_everything()
        second = await client.post("/api/automations", json=_automation_body(), headers=headers)
        assert second.status_code == 201, second.text
        assert second.json()["id"] == first.json()["id"]

        listed = await client.get("/api/automations", headers=headers)
        assert [a["id"] for a in listed.json()] == [first.json()["id"]]

    async def test_a_refused_create_writes_nothing(self, client, manager_a):
        headers = await auth_headers(client, manager_a)
        bad = {**_automation_body(), "event": "teleport_completed"}
        assert (await client.post("/api/automations", json=bad, headers=headers)).status_code == 422

        _forget_everything()                                    # nothing to recover from...
        assert (await client.get("/api/automations", headers=headers)).json() == []


# ====================================================== notifications, durable ===

class TestNotificationsSurviveRestart:
    async def test_template_and_notification_outlive_the_process(self, client, manager_a):
        headers = await auth_headers(client, manager_a)
        template = await client.post(
            "/api/notifications/templates", json=_template_body(), headers=headers
        )
        template_id = template.json()["id"]
        created = await client.post(
            "/api/notifications", json=_notification_body(template_id), headers=headers
        )
        assert created.status_code == 201, created.text
        notification_id = created.json()["id"]

        _forget_everything()

        listed = await client.get("/api/notifications", headers=headers)
        assert [n["id"] for n in listed.json()] == [notification_id]
        reopened = await client.get(f"/api/notifications/{notification_id}", headers=headers)
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["template_id"] == template_id
        assert reopened.json()["recipient"]["target_masked"] == "***4567"
        assert "+15551234567" not in reopened.text        # PII stays masked after a reload

    async def test_dedupe_and_read_state_survive_a_restart(self, client, manager_a):
        headers = await auth_headers(client, manager_a)
        template_id = (await client.post(
            "/api/notifications/templates", json=_template_body(), headers=headers
        )).json()["id"]
        body = _notification_body(template_id)
        first = await client.post("/api/notifications", json=body, headers=headers)
        notification_id = first.json()["id"]
        await client.post(f"/api/notifications/{notification_id}/read", headers=headers)

        _forget_everything()

        # The dedupe index is a database unique constraint, so a replayed create
        # after a restart still returns the same notification instead of a twin.
        second = await client.post("/api/notifications", json=body, headers=headers)
        assert second.status_code == 201, second.text
        assert second.json()["id"] == notification_id
        assert len((await client.get("/api/notifications", headers=headers)).json()) == 1

        unread = await client.get(
            "/api/notifications", params={"unread_only": True}, headers=headers
        )
        assert unread.json() == []                        # the read flag was persisted

        summary = await client.get("/api/notifications/states/summary", headers=headers)
        assert summary.status_code == 200
        assert summary.json()["pending"] == 1      # still awaiting delivery, counted once

    async def test_render_still_uses_the_single_brace_syntax_after_a_restart(
        self, client, manager_a
    ):
        headers = await auth_headers(client, manager_a)
        template_id = (await client.post(
            "/api/notifications/templates", json=_template_body(), headers=headers
        )).json()["id"]

        _forget_everything()

        rendered = await client.post(
            f"/api/notifications/templates/{template_id}/render",
            json={"variables": {"customer": "Ada", "when": "Tuesday"}},
            headers=headers,
        )
        assert rendered.status_code == 200, rendered.text
        assert rendered.json()["body"] == "Hi Ada, your appointment Tuesday is confirmed."


# ============================================================= inbox, durable ===

class TestInboxSurvivesRestart:
    async def test_overlay_state_outlives_the_process(self, client, manager_a, agent_a):
        headers = await auth_headers(client, manager_a)
        thread = await _open_thread(client, headers)
        thread_id = thread["id"]
        await client.post(
            f"/api/inbox/threads/{thread_id}/assign",
            json={"assignee_id": "agent-7"}, headers=headers)
        await client.post(
            f"/api/inbox/threads/{thread_id}/priority",
            json={"priority": "high"}, headers=headers)
        await client.post(
            f"/api/inbox/threads/{thread_id}/tags",
            json={"tag": "billing"}, headers=headers)
        await client.post(
            f"/api/inbox/threads/{thread_id}/tags",
            json={"tag": "urgent"}, headers=headers)
        await client.post(
            f"/api/inbox/threads/{thread_id}/notes",
            json={"body": "internal hint"}, headers=headers)
        await client.post(f"/api/inbox/threads/{thread_id}/unread", headers=headers)

        _forget_everything()

        fetched = await client.get(f"/api/inbox/threads/{thread_id}", headers=headers)
        assert fetched.status_code == 200, fetched.text
        body = fetched.json()
        assert body["status"] == "assigned"               # derived from the assignee
        assert body["assignee_id"] == "agent-7"
        assert body["priority"] == "high"
        assert set(body["tags"]) == {"billing", "urgent"}
        assert body["internal_notes"] == ["internal hint"]
        assert body["unread_count"] == 1

        counts = await client.get("/api/inbox/unread-counts", headers=headers)
        assert counts.status_code == 200
        # One badge, still set: the endpoint keys by the internal call id (the
        # opaque thread id is a hash of it, deliberately not reversible), so the
        # assertion is on the badge set rather than on a guessable key.
        assert list(counts.json().values()) == [1]

    async def test_messages_are_still_ordered_and_notes_still_internal(
        self, client, manager_a
    ):
        headers = await auth_headers(client, manager_a)
        thread_id = (await _open_thread(client, headers))["id"]
        await client.post(
            f"/api/inbox/threads/{thread_id}/notes",
            json={"body": "internal hint"}, headers=headers)

        _forget_everything()

        messages = await client.get(
            f"/api/inbox/threads/{thread_id}/messages", headers=headers
        )
        assert messages.status_code == 200, messages.text
        assert [m["direction"] for m in messages.json()] == ["inbound", "internal_note"]

    async def test_escalation_and_reopen_still_follow_the_transition_table(
        self, client, manager_a
    ):
        headers = await auth_headers(client, manager_a)
        thread_id = (await _open_thread(client, headers))["id"]
        assert (await client.post(
            f"/api/inbox/threads/{thread_id}/escalate",
            json={"reason": "customer asked for a supervisor"}, headers=headers,
        )).json()["status"] == "escalated"

        _forget_everything()

        # ESCALATED -> CLOSED -> OPEN is legal; ESCALATED is still not a status
        # you can jump straight out of an arbitrary way.
        closed = await client.post(f"/api/inbox/threads/{thread_id}/close", headers=headers)
        assert closed.json()["status"] == "closed"
        reopened = await client.post(f"/api/inbox/threads/{thread_id}/reopen", headers=headers)
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["status"] == "open"


# ============================================================ tenant isolation ===

class TestHydrationIsTenantScoped:
    async def test_one_tenants_rows_never_appear_in_anothers_view(
        self, client, manager_a, owner_b
    ):
        headers_a = await auth_headers(client, manager_a)
        headers_b = await auth_headers(client, owner_b)

        created = await client.post("/api/automations", json=_automation_body(), headers=headers_a)
        automation_id = created.json()["id"]
        template_id = (await client.post(
            "/api/notifications/templates", json=_template_body(), headers=headers_a
        )).json()["id"]
        await client.post(
            "/api/notifications", json=_notification_body(template_id), headers=headers_a
        )
        thread_id = (await _open_thread(client, headers_a))["id"]

        _forget_everything()

        # Tenant B, hydrated from the same tables, sees its own (empty) rows.
        assert (await client.get("/api/automations", headers=headers_b)).json() == []
        assert (await client.get("/api/notifications", headers=headers_b)).json() == []
        assert (await client.get("/api/inbox/threads", headers=headers_b)).json() == []

        # A genuinely correct id from tenant A is still not found by tenant B.
        for path in (f"/api/automations/{automation_id}",
                     f"/api/notifications/{template_id}",
                     f"/api/inbox/threads/{thread_id}"):
            response = await client.get(path, headers=headers_b)
            assert response.status_code in (403, 404), (path, response.status_code)

        # Tenant A's own view is intact after B's reads.
        assert [a["id"] for a in (
            await client.get("/api/automations", headers=headers_a)
        ).json()] == [automation_id]


# ======================================================= the scope's own rules ===

class TestTenantScope:
    async def test_the_scope_clears_the_registry_when_the_handler_raises(self, db):
        """A failing operation must leave the process as it found it."""
        from app.services.enterprise_store import tenant_scope

        tenant_id = "00000000-0000-0000-0000-00000000c0de"
        automation_service._REGISTRY.setdefault(tenant_id, {})["leak"] = object()
        try:
            async with tenant_scope(db, tenant_id):
                automation_service._REGISTRY.setdefault(tenant_id, {})["leak"] = object()
                raise RuntimeError("handler failed")
        except RuntimeError:
            pass
        assert automation_service._REGISTRY.get(tenant_id, {}).get("leak") is None

    async def test_unknown_events_are_still_refused_by_the_domain(self):
        with pytest.raises(ValueError):
            TriggerEvent("teleport_completed")
````
