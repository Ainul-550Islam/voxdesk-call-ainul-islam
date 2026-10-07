"""Environment rules and the baseline each kind carries.

Baselines are data. They do not connect to a cluster, a secret store or a
second configuration service.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.environments.models import EnvironmentKind, EnvironmentStatus
from app.tenancy.isolation import Conflict, LifecycleDenied

_TRANSITIONS: dict[str, frozenset[str]] = {
    EnvironmentStatus.ACTIVE.value: frozenset({
        EnvironmentStatus.ACTIVE.value,
        EnvironmentStatus.SUSPENDED.value,
        EnvironmentStatus.ARCHIVED.value,
    }),
    EnvironmentStatus.SUSPENDED.value: frozenset({
        EnvironmentStatus.SUSPENDED.value,
        EnvironmentStatus.ACTIVE.value,
        EnvironmentStatus.ARCHIVED.value,
    }),
    # Archive is reversible only back to active, and never for production.
    EnvironmentStatus.ARCHIVED.value: frozenset({
        EnvironmentStatus.ARCHIVED.value,
        EnvironmentStatus.ACTIVE.value,
    }),
}


@dataclass(frozen=True)
class EnvironmentBaseline:
    kind: str
    production_constraints: bool
    allows_debug_data: bool
    isolated_from_production_identity: bool
    summary: str


BASELINES: dict[str, EnvironmentBaseline] = {
    EnvironmentKind.DEVELOPMENT.value: EnvironmentBaseline(
        kind=EnvironmentKind.DEVELOPMENT.value,
        production_constraints=False,
        allows_debug_data=True,
        isolated_from_production_identity=True,
        summary="Development may carry debug labels. It is not production and shares no identity with it.",
    ),
    EnvironmentKind.STAGING.value: EnvironmentBaseline(
        kind=EnvironmentKind.STAGING.value,
        production_constraints=False,
        allows_debug_data=False,
        isolated_from_production_identity=True,
        summary="Staging is isolated from production identity and is not a place to execute a release.",
    ),
    EnvironmentKind.PRODUCTION.value: EnvironmentBaseline(
        kind=EnvironmentKind.PRODUCTION.value,
        production_constraints=True,
        allows_debug_data=False,
        isolated_from_production_identity=False,
        summary="Production is the one required environment. It cannot be archived or duplicated.",
    ),
}


def baseline_for(kind: str) -> EnvironmentBaseline:
    found = BASELINES.get(kind)
    if found is None:
        raise LifecycleDenied("Unknown environment kind")
    return found


def assert_transition(current: str, target: str) -> None:
    allowed = _TRANSITIONS.get(current)
    if allowed is None or target not in allowed:
        raise LifecycleDenied(f"Cannot move an environment from {current} to {target}")


def assert_can_archive(kind: str) -> None:
    if kind == EnvironmentKind.PRODUCTION.value:
        raise LifecycleDenied("The production environment cannot be archived")


def assert_kind_available(kind: str, existing_kinds: set[str]) -> None:
    """One of each kind. Production in particular cannot be duplicated."""
    if kind in existing_kinds:
        if kind == EnvironmentKind.PRODUCTION.value:
            raise Conflict("This tenant already has a production environment")
        raise Conflict(f"This tenant already has a {kind} environment")


def assert_default_allowed(status: str) -> None:
    if status == EnvironmentStatus.ARCHIVED.value:
        raise LifecycleDenied("An archived environment cannot be the default")
