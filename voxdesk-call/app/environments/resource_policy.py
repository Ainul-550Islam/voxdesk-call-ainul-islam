"""Business-resource policy on top of the existing role and identity policy.

Ordinary writes follow RBAC. Production destructive actions require the owner
role. A child environment cannot turn a mandatory parent control off.
"""

from __future__ import annotations

from app.db.models import Environment, UserRole
from app.environments.policy_inheritance import weakens
from app.resources.exceptions import InvalidResourceTransition, ResourceUnauthorized
from app.resources.lifecycle import assert_write_lifecycle, read_allowed

_DESTRUCTIVE = frozenset({"delete", "purge", "archive"})


def write_allowed(
    environment: Environment,
    role: UserRole | None,
    *,
    action: str,
    rbac_allows: bool,
) -> bool:
    if not rbac_allows or role is None:
        return False
    if environment.status != "active":
        return False
    if action in _DESTRUCTIVE and environment.kind == "production":
        return role is UserRole.OWNER
    if environment.kind == "production":
        return role in (UserRole.OWNER, UserRole.ADMIN, UserRole.MANAGER)
    if environment.kind == "staging":
        return role in (UserRole.OWNER, UserRole.ADMIN)
    return role in (UserRole.OWNER, UserRole.ADMIN, UserRole.MANAGER)


def assert_write(
    environment: Environment,
    role: UserRole | None,
    *,
    action: str,
    rbac_allows: bool,
) -> None:
    assert_write_lifecycle(environment)
    if not write_allowed(environment, role, action=action, rbac_allows=rbac_allows):
        raise ResourceUnauthorized()


def assert_read(environment: Environment, role: UserRole | None, *, rbac_allows: bool) -> None:
    if not rbac_allows or not read_allowed(environment, role):
        raise ResourceUnauthorized()


def assert_parent_not_weakened(parent: dict, proposed: dict) -> None:
    if weakens(parent, proposed):
        raise InvalidResourceTransition("An environment cannot weaken a mandatory parent control")


def cross_environment_denied(source_environment_id, target_environment_id) -> bool:
    if not source_environment_id or not target_environment_id:
        return False
    return str(source_environment_id) != str(target_environment_id)
