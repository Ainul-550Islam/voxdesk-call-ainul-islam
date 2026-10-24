"""Compliance control plane (Batch 03).

The package exposes exactly one thing at import time — the settings accessor —
so importing it from a route module never pulls in an SDK. Sub-modules
(``siem``, ``kms``, ``soc2``, ...) import their provider libraries lazily inside
the functions that use them.
"""

from __future__ import annotations

from app.compliance_v2.settings import (
    ComplianceV2Settings,
    get_compliance_v2_settings,
    reset_compliance_v2_settings,
)

__all__ = [
    "ComplianceV2Settings",
    "get_compliance_v2_settings",
    "reset_compliance_v2_settings",
]
