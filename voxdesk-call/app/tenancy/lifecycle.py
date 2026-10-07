"""Tenant lifecycle. ``deleted`` does not delete the row or its children.

``is_active`` is intentionally left alone. Existing login and JWT checks use
that flag; flipping it here would lock out tokens this change promised to keep
valid. New admin routes enforce ``lifecycle_status`` themselves.
"""

from __future__ import annotations

from app.db.models import Tenant
from app.tenancy.isolation import LifecycleDenied, ValidationFailed

ACTIVE = "active"
SUSPENDED = "suspended"
READ_ONLY = "read_only"
DELETED = "deleted"

TENANT_STATUSES = frozenset({ACTIVE, SUSPENDED, READ_ONLY, DELETED})

_TRANSITIONS: dict[str, frozenset[str]] = {
    ACTIVE: frozenset({ACTIVE, SUSPENDED, READ_ONLY, DELETED}),
    SUSPENDED: frozenset({SUSPENDED, ACTIVE, DELETED}),
    READ_ONLY: frozenset({READ_ONLY, ACTIVE, DELETED}),
    DELETED: frozenset({DELETED}),
}


def parse_status(value: str) -> str:
    if value not in TENANT_STATUSES:
        raise ValidationFailed("Unknown tenant lifecycle status")
    return value


def assert_transition(current: str, target: str) -> None:
    allowed = _TRANSITIONS.get(current)
    if allowed is None or target not in allowed:
        raise LifecycleDenied(f"Cannot move a tenant from {current} to {target}")


def assert_writable(tenant: Tenant) -> None:
    """Metadata and environment writes. Reads are not gated here."""
    if tenant.lifecycle_status != ACTIVE:
        raise LifecycleDenied("Tenant is not writable in this lifecycle state")


def apply_status(tenant: Tenant, target: str) -> bool:
    """Set the status. Returns False when the call was an idempotent no-op.

    Does not delete related rows and does not change ``is_active``.
    """
    desired = parse_status(target)
    assert_transition(tenant.lifecycle_status, desired)
    if tenant.lifecycle_status == desired:
        return False
    tenant.lifecycle_status = desired
    return True


def allowed_targets(current: str) -> frozenset[str]:
    """Statuses a tenant may move to, including an idempotent repeat."""
    return _TRANSITIONS.get(current, frozenset())
