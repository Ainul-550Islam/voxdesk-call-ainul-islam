"""Mount point for the Batch 07 white-label API.

Three routers, deliberately separate, because they have different audiences:

* ``router`` — tenant-scoped (domains, branding, assets, sender domains, template
  previews, widget admin). Permission-checked and audited.
* ``public_router`` — the on-demand TLS decision the edge calls. No tenant session;
  authorised by the deployment's ask token (``X-Voxdesk-Ask-Token``).
* ``widget_public_router`` — the widget loader a tenant's website fetches. No tenant
  session; the tenant is resolved from the request ``Host`` and the key must match
  that host, so an unknown host is a 404 rather than somebody's branding.

All three are re-exported from :mod:`app.platform_v2.whitelabel.router`.
"""

from __future__ import annotations

from app.platform_v2.router import AREAS
from app.platform_v2.whitelabel.router import public_router, router, widget_public_router

PREFIX = AREAS["whitelabel"]
PUBLIC_PREFIX = AREAS["whitelabel_tls"]
WIDGET_PREFIX = AREAS["whitelabel_widget"]

__all__ = ["PREFIX", "PUBLIC_PREFIX", "WIDGET_PREFIX", "public_router", "router", "widget_public_router"]
