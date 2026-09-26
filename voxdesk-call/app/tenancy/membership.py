"""Tenant membership over the existing ``User.role``.

No second RBAC table and no many-to-many. A machine principal is a member of
the tenant the credential was issued for, and only within the scopes that
credential already carries.
"""

from __future__ import annotations

import uuid

from app.auth.dependencies import TenantContext
from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.models import User, UserRole


def effective_role(user: User) -> UserRole:
    return user.role


def belongs_to_tenant(user: User, tenant_id: uuid.UUID) -> bool:
    return user.tenant_id == tenant_id


def human_role_allows(user: User, permission: Permission) -> bool:
    return has_permission(user.role, permission)


def machine_within_tenant(ctx: TenantContext, tenant_id: uuid.UUID, permission: Permission) -> bool:
    """A key can act only inside its own tenant, and only with a scope it holds."""
    if ctx.tenant_id != tenant_id:
        return False
    return ctx.can(permission)


def assert_member(user: User, tenant_id: uuid.UUID) -> None:
    """Membership is the user's tenant id. A miss is a boundary, not a role error."""
    from app.tenancy.isolation import BoundaryDenied

    if not belongs_to_tenant(user, tenant_id):
        raise BoundaryDenied()
