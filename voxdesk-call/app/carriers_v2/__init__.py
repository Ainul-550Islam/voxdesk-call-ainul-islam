"""Batch 02 carrier configuration surface.

``app/carriers_v2/settings.py`` owns every environment variable Batch 02
introduces. Keeping them out of ``app/core/config.py`` means the shared
config file can be edited by another batch at the same time without a
merge conflict, and it keeps the carrier/S2S feature switches in one
reviewable place.
"""

from __future__ import annotations

from app.carriers_v2.settings import (
    CarrierV2Settings,
    get_carrier_v2_settings,
    looks_like_placeholder,
    reset_carrier_v2_settings_cache,
)

__all__ = [
    "CarrierV2Settings",
    "get_carrier_v2_settings",
    "looks_like_placeholder",
    "reset_carrier_v2_settings_cache",
]
