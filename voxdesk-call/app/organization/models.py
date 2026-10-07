"""Organization vocabulary and the legacy-tenant naming rule.

The ORM mapping for ``organizations`` lives on ``app.db.models.Organization``
so there is one mapping and ``Base.metadata`` sees it. This module is the
closed vocabulary and the pure functions the migration and the insert hook
both call, so a backfilled slug cannot drift from a runtime slug.
"""

from __future__ import annotations

import enum
import re
import uuid

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_NAME_MAX = 200
_SLUG_MAX = 63


class OrganizationStatus(str, enum.Enum):
    """Lifecycle of an organization. ``deleted`` is a state, not a SQL delete."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    READ_ONLY = "read_only"
    DELETED = "deleted"


class InputError(ValueError):
    """A caller-supplied name or slug that cannot be stored."""


def validate_name(name: str, *, max_length: int = _NAME_MAX) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned or len(cleaned) > max_length:
        raise InputError(f"Name must be 1-{max_length} characters")
    return cleaned


def normalize_slug(value: str) -> str:
    slug = (value or "").strip().lower()
    if not _SLUG.match(slug) or not (3 <= len(slug) <= _SLUG_MAX):
        raise InputError(
            "Slug must be 3-63 characters: lowercase letters, digits and hyphens"
        )
    return slug


def slug_from_name(name: str) -> str:
    """A slug derived from a display name. Not guaranteed unique."""
    base = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    base = re.sub(r"-{2,}", "-", base)[:48]
    if len(base) < 3:
        base = (base + "-org").strip("-")
    if len(base) < 3:
        base = "org"
    return base[:_SLUG_MAX]


def legacy_organization_slug(tenant_id: uuid.UUID | str) -> str:
    """Deterministic slug for the organization a pre-hierarchy tenant receives.

    Keyed on the tenant id so two tenants never share an organization and a
    re-run of the backfill produces the same slug. This is a migration label,
    not a customer-chosen brand slug and not a billing identity.
    """
    parsed = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    return f"ws-{parsed.hex}"


def legacy_organization_name(name: str | None) -> str:
    """Copy the tenant's display name. Empty names become a neutral label.

    The copy is a hierarchy label so existing rows have a parent. It is not a
    claim that the legal billing name was collected.
    """
    cleaned = " ".join((name or "").split())
    if not cleaned:
        return "Workspace"
    return cleaned[:_NAME_MAX]


def is_organization_status(value: str) -> bool:
    try:
        OrganizationStatus(value)
    except ValueError:
        return False
    return True
