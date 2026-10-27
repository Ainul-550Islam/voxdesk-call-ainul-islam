"""Router assembly for Batch 07 (workforce, white-label, mobile).

One composition site, the same way the compliance and contact-centre packages are
assembled: the thin modules in ``app.api`` re-export these objects and
``app/main.py`` mounts them. Nothing else builds a second router, because a second
assembly is a second thing to keep in sync — and the thing that drifts is whether a
feature is actually served.

Three mount groups, because they have genuinely different auth:

``tenant_routers``
    Everything behind a session and a permission check: ``/api/wfm``,
    ``/api/whitelabel``, ``/api/mobile``.

``public_routers``
    Surfaces a browser or the edge calls without a tenant session:
    ``/api/platform/tls`` (edge ``ask``/``status``, token- or permission-gated inside
    the router) and ``/api/whitelabel/public`` (the widget loader, resolved strictly
    from the request ``Host``).

``AREAS`` is the manifest the wiring audit reads: each entry names a router and the
prefix it answers on, so ``scripts/verify_no_dead_routers.py`` can tell "built but
never mounted" from "mounted and serving".
"""

from __future__ import annotations

from fastapi import APIRouter

from app.platform_v2.mobile.router import router as mobile_router
from app.platform_v2.whitelabel.router import public_router as tls_router
from app.platform_v2.whitelabel.router import router as whitelabel_router
from app.platform_v2.whitelabel.router import widget_public_router
from app.platform_v2.wfm.router import router as wfm_router

MANIFEST_FORMAT = 1

#: Router name -> prefix, exactly as mounted by ``app/main.py``.
AREAS: dict[str, str] = {
    "wfm": "/api/wfm",
    "whitelabel": "/api/whitelabel",
    "whitelabel_tls": "/api/platform/tls",
    "whitelabel_widget": "/api/whitelabel/public",
    "mobile": "/api/mobile",
}

#: Mounted behind a tenant session. Order is the order the audit prints.
tenant_routers: tuple[APIRouter, ...] = (wfm_router, whitelabel_router, mobile_router)

#: Mounted without a tenant session; each carries its own authorisation.
public_routers: tuple[APIRouter, ...] = (tls_router, widget_public_router)


def build_router() -> APIRouter:
    """Aggregate the tenant-scoped surfaces into one router."""
    aggregate = APIRouter()
    for area in tenant_routers:
        aggregate.include_router(area)
    for area in public_routers:
        aggregate.include_router(area)
    return aggregate


router = build_router()


__all__ = ["AREAS", "MANIFEST_FORMAT", "build_router", "public_routers", "router", "tenant_routers"]
