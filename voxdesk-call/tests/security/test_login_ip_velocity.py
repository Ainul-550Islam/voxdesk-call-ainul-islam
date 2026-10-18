"""Per-source velocity limit on POST /auth/login.

Regression: /auth/login had only a per-account lockout, so one client could try many
accounts with no throttle (verified live: 12 attempts, all 401). The limit runs before
any account lookup and must block with 429 and a structured log event.

Uses the real auth router with the account lookup stubbed, so no database is needed.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from structlog.testing import capture_logs

from app.api import auth_routes
from app.auth import service
from app.core.config import settings
from app.core.rate_limit import reset_identity_limiter
from app.db.session import get_session

LIMIT = 3


@pytest.fixture
def login_app(monkeypatch):
    monkeypatch.setattr(settings, "login_ip_limit_per_minute", LIMIT)
    monkeypatch.setattr(settings, "redis_url", "")  # in-process backend for the test
    reset_identity_limiter()

    calls = {"n": 0}

    async def _authenticate(*_a, **_kw):
        calls["n"] += 1
        raise service.AuthError("Invalid email or password.")

    monkeypatch.setattr(service, "authenticate", _authenticate)

    app = FastAPI()
    app.include_router(auth_routes.router)
    app.dependency_overrides[get_session] = lambda: None
    app.state.calls = calls
    yield app
    reset_identity_limiter()


def _client(app: FastAPI, peer: str) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, client=(peer, 40000))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


_BODY = {"email": "nobody@example.com", "password": "wrong-password-x"}


@pytest.mark.asyncio
async def test_attempts_over_the_limit_from_one_address_are_429(login_app):
    async with _client(login_app, "203.0.113.7") as c:
        for _ in range(LIMIT):
            assert (await c.post("/auth/login", json=_BODY)).status_code == 401
        with capture_logs() as logs:
            r = await c.post("/auth/login", json=_BODY)
    assert r.status_code == 429
    assert r.headers.get("Retry-After") == "60"
    assert r.json()["detail"]["code"] == "rate_limited"
    assert any(e["event"] == "auth.login.ip_rate_limited" for e in logs), logs


@pytest.mark.asyncio
async def test_blocked_attempt_never_reaches_account_lookup(login_app):
    async with _client(login_app, "203.0.113.8") as c:
        for _ in range(LIMIT + 5):
            await c.post("/auth/login", json=_BODY)
    # Only the attempts inside the budget reached authenticate().
    assert login_app.state.calls["n"] == LIMIT


@pytest.mark.asyncio
async def test_limit_is_per_address_not_global(login_app):
    async with _client(login_app, "203.0.113.9") as c:
        for _ in range(LIMIT + 2):
            await c.post("/auth/login", json=_BODY)
    async with _client(login_app, "198.51.100.4") as other:
        assert (await other.post("/auth/login", json=_BODY)).status_code == 401
