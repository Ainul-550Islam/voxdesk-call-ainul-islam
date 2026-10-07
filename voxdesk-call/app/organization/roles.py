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
