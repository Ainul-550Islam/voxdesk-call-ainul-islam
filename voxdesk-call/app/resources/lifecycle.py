"""How a resource behaves when its environment is active, suspended or archived.

Nothing here deletes a row because the environment changed state.
"""

from __future__ import annotations

from app.db.models import Environment, UserRole
from app.resources.exceptions import EnvironmentArchived, EnvironmentSuspended

_CLOSED = frozenset({"suspended", "archived"})


def accepts_create(environment: Environment) -> bool:
    return environment.status == "active"


def accepts_write(environment: Environment) -> bool:
    return environment.status == "active"


def read_allowed(environment: Environment, role: UserRole | None) -> bool:
    if role is None:
        return False
    if environment.status == "active":
        return True
    if environment.status == "suspended":
        return True
    if environment.status == "archived":
        return role in (UserRole.OWNER, UserRole.ADMIN)
    return False


def assert_write_lifecycle(environment: Environment) -> None:
    if environment.status == "suspended":
        raise EnvironmentSuspended()
    if environment.status == "archived":
        raise EnvironmentArchived()
    if environment.status in _CLOSED:
        raise EnvironmentSuspended()
