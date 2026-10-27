"""Mount point for the Batch 07 workforce-management API.

The routes live in :mod:`app.platform_v2.wfm.router` so that the domain package
owns its own HTTP surface; this module is the stable import path ``app/main.py``
uses (``from app.api.wfm_routes import router``). It re-exports the same router
object rather than building a second one — a duplicate assembly would let the
manifest list an area the application does not actually serve.
"""

from __future__ import annotations

from app.platform_v2.router import AREAS
from app.platform_v2.wfm.router import router

PREFIX = AREAS["wfm"]

__all__ = ["PREFIX", "router"]
