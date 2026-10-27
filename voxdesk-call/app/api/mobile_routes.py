"""Mount point for the Batch 07 mobile API.

Re-exports :mod:`app.platform_v2.mobile.router` — devices, push, and the PKCE
mobile sign-in flow — under the stable import path ``app/main.py`` uses.
"""

from __future__ import annotations

from app.platform_v2.mobile.router import router
from app.platform_v2.router import AREAS

PREFIX = AREAS["mobile"]

__all__ = ["PREFIX", "router"]
