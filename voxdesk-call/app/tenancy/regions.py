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
