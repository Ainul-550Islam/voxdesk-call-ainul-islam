"""Backward-compatible import path for the Batch 03 compliance aggregator.

The single compliance-v2 router now lives in :mod:`app.api.compliance_v2` (the
package ``__init__``), which ``app/main.py`` mounts as one router. Batch 06 moved
the composition there so there is exactly one aggregator per package. This
module keeps the historical ``app.api.compliance_v2_routes`` import path working
for callers of ``router``, ``AREAS`` and ``MANIFEST_FORMAT``; it defines no
APIRouter of its own and is intentionally not mounted directly.
"""

from __future__ import annotations

from app.api.compliance_v2 import AREAS, MANIFEST_FORMAT, router

__all__ = ["AREAS", "MANIFEST_FORMAT", "router"]
