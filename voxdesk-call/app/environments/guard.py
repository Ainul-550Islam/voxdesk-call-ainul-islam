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
