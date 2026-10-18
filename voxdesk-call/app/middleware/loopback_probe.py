"""Host-header validation that exempts the in-container liveness probe.

``TRUSTED_HOSTS`` is mandatory in production and enables Starlette's
``TrustedHostMiddleware``, which rejects any request whose ``Host`` header is not
on the list. The image's ``HEALTHCHECK`` calls ``http://127.0.0.1:8000/health``,
so without an exemption the probe receives 400 and the container is reported
unhealthy forever.

This subclass lets exactly one request shape bypass the Host check: ``GET``-style
requests to ``/health`` whose TCP peer is loopback. Requests arriving through
Caddy have a non-loopback peer (uvicorn only rewrites ``scope["client"]`` from
``X-Forwarded-For`` for the configured proxy), so they are still host-checked.
"""

from __future__ import annotations

from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import Receive, Scope, Send

LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1"})
PROBE_PATHS = frozenset({"/health"})


class LoopbackProbeTrustedHostMiddleware(TrustedHostMiddleware):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") in PROBE_PATHS:
            client = scope.get("client")
            if client and client[0] in LOOPBACK_PEERS:
                await self.app(scope, receive, send)
                return
        await super().__call__(scope, receive, send)
