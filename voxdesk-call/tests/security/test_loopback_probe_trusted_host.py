"""TrustedHost policy: only a loopback GET/HEAD /health probe may bypass the Host check.

Regression for the container HEALTHCHECK receiving 400 under TRUSTED_HOSTS because it
sends ``Host: 127.0.0.1:8000``. Uses a minimal Starlette app, so it does not import
app.main (which takes minutes to load).
"""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.middleware.loopback_probe import LoopbackProbeTrustedHostMiddleware

ALLOWED = ["voxdesk.example.com"]


async def _ok(request):
    return PlainTextResponse("ok")


def _app() -> Starlette:
    app = Starlette(
        routes=[
            Route("/health", _ok, methods=["GET", "HEAD"]),
            Route("/api/calls", _ok, methods=["GET"]),
        ]
    )
    app.add_middleware(LoopbackProbeTrustedHostMiddleware, allowed_hosts=ALLOWED)
    return app


def _client(peer: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=_app(), client=(peer, 40000))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


@pytest.mark.asyncio
async def test_loopback_health_probe_with_ip_host_is_allowed():
    async with _client("127.0.0.1") as c:
        r = await c.get("/health", headers={"host": "127.0.0.1:8000"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_ipv6_loopback_health_probe_is_allowed():
    async with _client("::1") as c:
        r = await c.get("/health", headers={"host": "[::1]:8000"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_non_loopback_health_probe_with_bad_host_is_rejected():
    async with _client("203.0.113.9") as c:
        r = await c.get("/health", headers={"host": "evil.example.net"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_loopback_peer_cannot_bypass_host_check_on_api_paths():
    async with _client("127.0.0.1") as c:
        r = await c.get("/api/calls", headers={"host": "evil.example.net"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_allowed_host_still_served():
    async with _client("203.0.113.9") as c:
        r = await c.get("/api/calls", headers={"host": "voxdesk.example.com"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_post_to_health_is_not_exempt():
    async with _client("127.0.0.1") as c:
        r = await c.post("/health", headers={"host": "evil.example.net"})
    assert r.status_code in (400, 405)
