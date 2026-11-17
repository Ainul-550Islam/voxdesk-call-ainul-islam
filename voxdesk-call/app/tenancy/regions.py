"""Region labels this process is willing to name.

A label is not a region router, a replica, or proof that bytes sit in a
place. ``knowledge_s3_region`` is an object-store client setting and is
intentionally not read here.
"""

from __future__ import annotations

import re

_LABEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

#: This deployment has no configured physical region. Empty is the honest set.
SUPPORTED_REGIONS: frozenset[str] = frozenset()

#: No sanctions or product restricted list is configured. Callers may pass
#: an explicit set when a policy decision must reject a label anyway.
RESTRICTED_REGIONS: frozenset[str] = frozenset()

PLACEMENT = "unconfigured"


def normalize_label(value: str) -> str:
    """Return a region label, or raise ValueError when the shape is invalid."""
    label = (value or "").strip().lower()
    if not _LABEL.match(label):
        raise ValueError("Region label must be 1-63 lowercase letters, digits and hyphens")
    return label


def supported_regions(catalog: frozenset[str] | None = None) -> frozenset[str]:
    """The deployment catalog, unless the caller is evaluating an explicit set."""
    if catalog is None:
        return SUPPORTED_REGIONS
    return frozenset(catalog)


def is_supported(label: str, *, catalog: frozenset[str] | None = None) -> bool:
    return label in supported_regions(catalog)


def is_restricted(label: str, *, restricted: frozenset[str] | None = None) -> bool:
    chosen = RESTRICTED_REGIONS if restricted is None else frozenset(restricted)
    return label in chosen


# ------------------------------------------------------- write-side enforcement ---


class CrossRegionWrite(RuntimeError):
    """A write would land outside the region the tenant pinned. Nothing was stored.

    This is the enforcement half the module was missing: ``is_supported`` answers
    "may this label be named at all", while ``require_write_region`` answers the
    question a residency claim actually rests on — "is *this* write going to the
    pinned place, and can we prove it".
    """

    def __init__(self, reason: str, *, pinned: str | None, target: str | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.pinned = pinned
        self.target = target


#: Reasons, kept as constants so a caller (and a test) never matches on prose.
WRITE_UNPINNED = "unpinned"                 # no pin: nothing was promised, nothing proven
WRITE_MATCHED = "pinned_region_matched"     # target == pin, and the pin is a configured region
WRITE_TARGET_UNKNOWN = "region_target_unknown"   # pinned, but the write names no region
WRITE_FOREIGN = "cross_region_write"        # pinned, write names a different region
WRITE_UNCONFIGURED = "region_not_configured"     # pin names a region this deployment lacks


def require_write_region(
    pinned: str | None,
    target: str | None,
    *,
    catalog: frozenset[str] | None = None,
) -> str:
    """Return the reason a write is allowed, or raise :class:`CrossRegionWrite`.

    The four refusals are deliberately distinct, because they need different
    fixes: an unstated target is a caller bug, a foreign target is a routing bug,
    and an unconfigured pin is a deployment that promised something it cannot do.
    A pin with no configured catalogue can never be honoured — which is exactly
    this deployment's state, and saying so is the point.
    """
    if pinned is None or str(pinned).strip() == "":
        return WRITE_UNPINNED
    pin = normalize_label(pinned)
    if target is None or str(target).strip() == "":
        raise CrossRegionWrite(WRITE_TARGET_UNKNOWN, pinned=pin, target=None)
    where = normalize_label(target)
    if where != pin:
        raise CrossRegionWrite(WRITE_FOREIGN, pinned=pin, target=where)
    if not is_supported(pin, catalog=catalog):
        raise CrossRegionWrite(WRITE_UNCONFIGURED, pinned=pin, target=where)
    return WRITE_MATCHED
