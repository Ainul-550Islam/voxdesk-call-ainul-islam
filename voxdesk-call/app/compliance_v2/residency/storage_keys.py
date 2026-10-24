"""Region-scoped object keys.

Every object this product writes — a recording, a transcript, an export, a
bundle — carries its region as the first path segment::

    regions/<region>/<kind>/<tenant_id>/<name>

Three consequences, all intentional:

* an object's region is readable from its key alone, so an audit can answer
  "where is this stored" without a database lookup;
* a key can be checked against a tenant's pin before any storage call, which is
  the cross-region guard;
* a legacy key without the prefix is *visible* as legacy rather than assumed
  correct — :func:`classify` returns ``legacy`` and the caller decides.

Nothing in this module performs I/O; it produces and validates names.
"""

from __future__ import annotations

import dataclasses
import re
import uuid

from app.compliance_v2.residency.regions import RegionRegistry, registry as default_registry

ROOT = "regions"

#: Object kinds this product stores under a region prefix.
KINDS = frozenset(
    {
        "recordings",
        "transcripts",
        "exports",
        "audit-bundles",
        "dsar-bundles",
        "evidence",
        "attachments",
    }
)

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,400}$")


class StorageKeyError(ValueError):
    """A key is malformed, or it names the wrong region for its tenant."""


class CrossRegionKey(StorageKeyError):
    """A key belongs to a region other than the tenant's pin."""


@dataclasses.dataclass(frozen=True)
class ParsedKey:
    region: str
    kind: str
    tenant_id: str
    name: str

    @property
    def key(self) -> str:
        return f"{ROOT}/{self.region}/{self.kind}/{self.tenant_id}/{self.name}"


def build_key(
    *,
    region: str,
    kind: str,
    tenant_id: uuid.UUID | str,
    name: str,
    registry: RegionRegistry | None = None,
) -> str:
    """Build a region-scoped key, validating every segment."""
    active = registry or default_registry()
    resolved = active.get(region) if active.configured else None
    if resolved is None:
        raise StorageKeyError(f"region {region!r} is not configured for this deployment")
    if kind not in KINDS:
        raise StorageKeyError(f"unknown object kind {kind!r}")
    tenant = str(tenant_id)
    try:
        uuid.UUID(tenant)
    except (ValueError, AttributeError, TypeError) as exc:
        raise StorageKeyError("tenant id must be a UUID") from exc
    cleaned = (name or "").strip().lstrip("/")
    if not _SAFE_NAME.match(cleaned) or ".." in cleaned.split("/"):
        raise StorageKeyError("object name is not a safe relative path")
    return f"{ROOT}/{resolved.code}/{kind}/{tenant}/{cleaned}"


def classify(key: str) -> str:
    """``"regional"``, ``"legacy"`` or ``"invalid"``."""
    if not isinstance(key, str) or not key.strip():
        return "invalid"
    if key.startswith(f"{ROOT}/"):
        try:
            parse(key)
        except StorageKeyError:
            return "invalid"
        return "regional"
    return "legacy"


def parse(key: str) -> ParsedKey:
    """Split a region-scoped key. Raises :class:`StorageKeyError`."""
    parts = (key or "").strip("/").split("/")
    if len(parts) < 5 or parts[0] != ROOT:
        raise StorageKeyError("key is not region-scoped")
    region, kind, tenant = parts[1], parts[2], parts[3]
    name = "/".join(parts[4:])
    if kind not in KINDS:
        raise StorageKeyError(f"unknown object kind {kind!r}")
    try:
        uuid.UUID(tenant)
    except (ValueError, TypeError) as exc:
        raise StorageKeyError("tenant id segment is not a UUID") from exc
    return ParsedKey(region=region, kind=kind, tenant_id=tenant, name=name)


def region_of(key: str) -> str:
    """The region named by a key, or ``""`` for a legacy key."""
    try:
        return parse(key).region
    except StorageKeyError:
        return ""


def assert_region(
    key: str,
    *,
    expected_region: str,
    tenant_id: uuid.UUID | str | None = None,
) -> ParsedKey:
    """Refuse a key that is not in ``expected_region`` for ``tenant_id``."""
    parsed = parse(key)
    if parsed.region != expected_region:
        raise CrossRegionKey(
            f"object is stored in {parsed.region!r}, but this tenant is pinned to "
            f"{expected_region!r}"
        )
    if tenant_id is not None and parsed.tenant_id != str(tenant_id):
        raise CrossRegionKey("object belongs to a different tenant")
    return parsed


def rewrite_to_region(
    key: str, *, region: str, registry: RegionRegistry | None = None
) -> str:
    """Move a parsed key to another region (used by an approved relocation)."""
    parsed = parse(key)
    return build_key(
        region=region,
        kind=parsed.kind,
        tenant_id=parsed.tenant_id,
        name=parsed.name,
        registry=registry,
    )


__all__ = [
    "KINDS",
    "ROOT",
    "CrossRegionKey",
    "ParsedKey",
    "StorageKeyError",
    "assert_region",
    "build_key",
    "classify",
    "parse",
    "region_of",
    "rewrite_to_region",
]
