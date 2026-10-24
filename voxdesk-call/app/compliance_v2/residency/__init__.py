"""Data residency: declared regions, tenant pins and region-scoped object keys.

Three modules, three questions:

* :mod:`regions` — which regions exist in this deployment, and their prefixes;
* :mod:`storage_keys` — how a region is encoded into an object key, and how a
  key is checked against a pin before it is used;
* :mod:`policy` — what a tenant is pinned to, and what happens when a request
  asks for something else (an audited refusal, never a silent fallback).

:mod:`middleware` wires the policy into the request path for the
placement-bearing routes.
"""

from __future__ import annotations

from app.compliance_v2.residency.policy import (
    STATUS_CONFIGURED,
    STATUS_NOT_CONFIGURED,
    CrossRegionViolation,
    ResidencyDecision,
    ResidencyError,
    PinRecord,
    check_region,
    enforce_key,
    enforce_region,
    pin_document,
    region_catalogue,
    resolve_pin,
)
from app.compliance_v2.residency.regions import (
    Region,
    RegionError,
    RegionNotConfigured,
    RegionRegistry,
    build_registry,
    known_regions,
    registry,
)
from app.compliance_v2.residency.storage_keys import (
    KINDS,
    ROOT,
    CrossRegionKey,
    ParsedKey,
    StorageKeyError,
    assert_region,
    build_key,
    classify,
    parse,
    region_of,
    rewrite_to_region,
)

__all__ = [
    "KINDS",
    "ROOT",
    "STATUS_CONFIGURED",
    "STATUS_NOT_CONFIGURED",
    "CrossRegionKey",
    "CrossRegionViolation",
    "ParsedKey",
    "PinRecord",
    "Region",
    "RegionError",
    "RegionNotConfigured",
    "RegionRegistry",
    "ResidencyDecision",
    "ResidencyError",
    "StorageKeyError",
    "assert_region",
    "build_key",
    "build_registry",
    "check_region",
    "classify",
    "enforce_key",
    "enforce_region",
    "known_regions",
    "parse",
    "pin_document",
    "region_catalogue",
    "region_of",
    "registry",
    "resolve_pin",
    "rewrite_to_region",
]
