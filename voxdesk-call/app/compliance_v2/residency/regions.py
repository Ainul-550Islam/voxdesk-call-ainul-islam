"""The deployment's region catalogue.

``DATA_REGIONS`` names the regions this deployment can hold data in; each entry
gets a deterministic storage prefix, an optional KMS key and a human label. No
region is invented: with ``DATA_REGIONS`` empty there is nothing to pin to, and
every residency decision reports NOT_CONFIGURED rather than defaulting to a
region that does not exist.

The catalogue is also the *only* place a region name becomes a storage key
prefix, so an unknown region cannot leak into an object path (and therefore into
a bucket, a backup, or an export) by way of a query parameter.
"""

from __future__ import annotations

import dataclasses
import re
from functools import lru_cache
from typing import Iterable

from app.compliance_v2.settings import ComplianceV2Settings, get_compliance_v2_settings

#: A region label: letters, digits, dash, underscore, dots. Deliberately narrow,
#: because the value becomes a path segment.
_REGION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,62}$")


class RegionError(ValueError):
    """A region name is unknown or malformed."""


class RegionNotConfigured(RegionError):
    """The deployment has no region catalogue at all."""


@dataclasses.dataclass(frozen=True)
class Region:
    """One region this deployment can hold data in."""

    code: str
    storage_prefix: str
    kms_key_id: str = ""
    endpoint: str = ""
    label: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "storage_prefix": self.storage_prefix,
            "kms_key_id": self.kms_key_id,
            "endpoint": self.endpoint,
            "label": self.label or self.code,
        }


def _normalise(code: str) -> str:
    value = (code or "").strip().lower()
    if not _REGION_RE.match(value):
        raise RegionError(f"region label {code!r} is malformed")
    return value


@dataclasses.dataclass(frozen=True)
class RegionRegistry:
    regions: tuple[Region, ...]
    default: str = ""

    def __post_init__(self) -> None:
        codes = [region.code for region in self.regions]
        if len(codes) != len(set(codes)):
            raise RegionError("DATA_REGIONS contains duplicate codes")
        if self.default and self.default not in codes:
            raise RegionError("DEFAULT_REGION must be one of DATA_REGIONS")

    @property
    def configured(self) -> bool:
        return bool(self.regions)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(region.code for region in self.regions)

    def get(self, code: str) -> Region:
        wanted = _normalise(code)
        for region in self.regions:
            if region.code == wanted:
                return region
        raise RegionError(f"unknown region {code!r}")

    def is_known(self, code: str) -> bool:
        try:
            self.get(code)
        except RegionError:
            return False
        return True

    def default_region(self) -> Region:
        """The region used when a tenant has no pin, or raise NOT_CONFIGURED."""
        if not self.regions:
            raise RegionNotConfigured("no DATA_REGIONS are configured for this deployment")
        if self.default:
            return self.get(self.default)
        return self.regions[0]

    def as_dict(self) -> dict:
        return {
            "configured": self.configured,
            "default": self.default,
            "regions": [region.as_dict() for region in self.regions],
        }


def build_registry(settings: ComplianceV2Settings | None = None) -> RegionRegistry:
    """Build the registry from configuration, accepting ``code`` or ``code:key``."""
    cfg = settings or get_compliance_v2_settings()
    regions: list[Region] = []
    for index, raw in enumerate(cfg.regions):
        entry = raw.strip()
        key_id = ""
        if ":" in entry:
            entry, key_id = entry.split(":", 1)
        code = _normalise(entry)
        regions.append(
            Region(
                code=code,
                storage_prefix=f"regions/{code}",
                kms_key_id=key_id.strip()[:200],
                endpoint="",
                label=code,
            )
        )
    default = _normalise(cfg.default_region) if cfg.default_region else ""
    return RegionRegistry(regions=tuple(regions), default=default)


@lru_cache(maxsize=1)
def registry() -> RegionRegistry:
    """Process-wide registry (tests call ``registry.cache_clear()``)."""
    return build_registry()


def known_regions() -> Iterable[str]:
    try:
        return registry().codes
    except RegionError:
        return ()


__all__ = [
    "Region",
    "RegionError",
    "RegionNotConfigured",
    "RegionRegistry",
    "build_registry",
    "known_regions",
    "registry",
]
