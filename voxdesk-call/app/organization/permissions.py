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
