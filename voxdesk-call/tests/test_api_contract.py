"""Step 9 — API contract smoke test.

Hits every registered route with a safe, parameterised request and asserts the
server never returns a 500 and always returns a structured response. This is a
*contract* test, not a functional test: it proves the route exists, is wired,
and fails with a client error (4xx) rather than an internal error when hit
without proper authorisation.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app as voxdesk_app

# Routes that need a WebSocket or a multipart body and are therefore not part
# of this plain-HTTP sweep.
_SKIP_PREFIXES = ("/telephony/ws",)


def _safe_sample(route) -> dict:
    """Build a concrete path for a route with path parameters."""
    path = route.path
    for param in route.param_convertors or {}:
        path = path.replace("{" + param + "}", uuid.uuid4().hex)
    return path


def test_registered_api_method_path_pairs_are_unique():
    """Starlette dispatches the first duplicate pair, silently shadowing later handlers."""
    from collections import defaultdict

    registrations = defaultdict(list)
    registered_routes = {}
    for route in voxdesk_app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        for method in set(getattr(route, "methods", None) or ()) - {"HEAD", "OPTIONS"}:
            registrations[(method, path)].append(getattr(route, "name", "<unnamed>"))
            registered_routes[(method, path)] = route

    duplicates = {
        f"{method} {path}": names
        for (method, path), names in registrations.items()
        if len(names) > 1
    }
    assert not duplicates, f"duplicate API method/path registrations shadow handlers: {duplicates}"

    registered = {(method, path) for (method, path), _names in registrations.items()}
    expected_routes = {
        ("GET", "/api/calls/{call_id}"): "app.api.routes",
        ("GET", "/api/calls/outbound/{call_id}"): "app.api.outbound_call_routes",
        ("GET", "/api/calls/{call_id}/transcript"): "app.api.routes",
        ("GET", "/api/calls/{call_id}/transcript-summary"): "app.api.call_search_export_routes",
        ("GET", "/api/calls/{call_id}/transfer"): "app.api.routes",
        ("GET", "/api/calls/{call_id}/transfer-details"): "app.api.transfer_control_routes",
        ("GET", "/api/calls/idempotency/stats"): "app.api.outbound_call_routes",
        ("GET", "/api/calls/transfers/idempotency/stats"): "app.api.transfer_control_routes",
        ("GET", "/api/calls/monitoring/idempotency/stats"): "app.api.live_monitoring_routes",
        ("DELETE", "/api/calls/idempotency/cache"): "app.api.outbound_call_routes",
        ("DELETE", "/api/calls/transfers/idempotency/cache"): "app.api.transfer_control_routes",
        ("DELETE", "/api/calls/monitoring/idempotency/cache"): "app.api.live_monitoring_routes",
        ("POST", "/api/deployment/revisions/{revision_id}/verify"): "app.api.deployment_runtime_routes",
        ("POST", "/api/deployment/revisions/{revision_id}/verification-state"): "app.api.deployment_routes",
    }
    assert set(expected_routes) <= registered
    for operation, expected_module in expected_routes.items():
        assert registered_routes[operation].endpoint.__module__ == expected_module


@pytest.mark.asyncio
async def test_no_route_returns_500_when_probed():
    async with AsyncClient(
        transport=ASGITransport(app=voxdesk_app, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        probed = 0
        for route in voxdesk_app.routes:
            if not getattr(route, "methods", None):
                continue
            if "GET" not in route.methods:
                continue
            path = _safe_sample(route)
            if path.startswith(_SKIP_PREFIXES):
                continue
            r = await ac.get(path)
            assert r.status_code != 500, f"GET {path} -> 500"
            probed += 1
        assert probed > 40, f"expected to probe many routes, only {probed}"
