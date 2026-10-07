"""Organization lifecycle and the rules that gate structural changes.

Status values match ``OrganizationStatus``. ``deleted`` is terminal: restoring
a deleted organization is not an implicit undelete.
"""

from __future__ import annotations

from app.organization.models import OrganizationStatus
from app.tenancy.isolation import LifecycleDenied, ValidationFailed

_TRANSITIONS: dict[str, frozenset[str]] = {
    OrganizationStatus.ACTIVE.value: frozenset({
        OrganizationStatus.ACTIVE.value,
        OrganizationStatus.SUSPENDED.value,
        OrganizationStatus.READ_ONLY.value,
        OrganizationStatus.DELETED.value,
    }),
    OrganizationStatus.SUSPENDED.value: frozenset({
        OrganizationStatus.SUSPENDED.value,
        OrganizationStatus.ACTIVE.value,
        OrganizationStatus.DELETED.value,
    }),
    OrganizationStatus.READ_ONLY.value: frozenset({
        OrganizationStatus.READ_ONLY.value,
        OrganizationStatus.ACTIVE.value,
        OrganizationStatus.DELETED.value,
    }),
    OrganizationStatus.DELETED.value: frozenset({OrganizationStatus.DELETED.value}),
}


def parse_status(value: str) -> str:
    try:
        return OrganizationStatus(value).value
    except ValueError as exc:
        raise ValidationFailed("Unknown organization status") from exc


def assert_transition(current: str, target: str) -> None:
    allowed = _TRANSITIONS.get(current)
    if allowed is None or target not in allowed:
        raise LifecycleDenied(
            f"Cannot move an organization from {current} to {target}"
        )


def assert_can_add_tenant(status: str) -> None:
    """A suspended, read-only or deleted organization cannot gain a tenant."""
    if status != OrganizationStatus.ACTIVE.value:
        raise LifecycleDenied(
            "New tenants can only be added to an active organization"
        )


def assert_metadata_writable(status: str) -> None:
    """Rename is not destructive, but a freeze or a deletion is not a rename window."""
    if status in (OrganizationStatus.SUSPENDED.value, OrganizationStatus.DELETED.value):
        raise LifecycleDenied("Organization metadata cannot be changed in this state")


def assert_structure_writable(status: str) -> None:
    """Environments and other structure are only added while the org is active."""
    if status != OrganizationStatus.ACTIVE.value:
        raise LifecycleDenied("Organization structure is frozen in this state")
