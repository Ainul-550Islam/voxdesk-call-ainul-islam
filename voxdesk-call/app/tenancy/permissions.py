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
