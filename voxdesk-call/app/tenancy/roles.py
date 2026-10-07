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
