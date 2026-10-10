"""Rate-limit matrix across all 8 tiers plus fail-closed Redis outage verification."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core import rate_limit
from app.core.config import settings
from app.core.rate_limit import (
    RateLimiter,
    _bucket_for_path,
    add_rate_limit_middleware,
    rate_limit as distributed_rate_limit,
)

pytestmark = pytest.mark.asyncio


RATE_LIMIT_TIERS = [
    (
        "auth",
        "POST",
        "/auth/login",
        "rate_limit_login_per_minute",
        3,
    ),
    (
        "password_reset",
        "POST",
        "/auth/password-reset/request",
        "rate_limit_password_reset_per_minute",
        2,
    ),
    (
        "admin",
        "GET",
        "/api/api-keys",
        "rate_limit_admin_per_minute",
        3,
    ),
    (
        "telephony",
        "POST",
        "/telephony/outbound/call",
        "rate_limit_telephony_per_minute",
        3,
    ),
    (
        "webhook",
        "POST",
        "/api/webhooks",
        "rate_limit_webhook_per_minute",
        4,
    ),
    (
        "upload",
        "POST",
        "/api/kb/documents/upload",
        "rate_limit_upload_per_minute",
        2,
    ),
    (
        "execution",
        "POST",
        "/api/calls",
        "rate_limit_execution_per_minute",
        3,
    ),
    (
        "api",
        "GET",
        "/api/agents",
        "rate_limit_burst",
        4,
    ),
]


def _build_rate_limited_test_app() -> FastAPI:
    app = FastAPI()
    add_rate_limit_middleware(app)

    for _tier, method, path, _setting_attr, _limit in RATE_LIMIT_TIERS:
        if method == "POST":
            app.post(path)(lambda: {"ok": True})
        else:
            app.get(path)(lambda: {"ok": True})
    return app


@pytest.mark.parametrize(
    ("tier", "method", "path", "setting_attr", "configured_limit"),
    RATE_LIMIT_TIERS,
)
async def test_rate_limit_tier_exceeds_and_returns_429_with_retry_after(
    monkeypatch,
    tier: str,
    method: str,
    path: str,
    setting_attr: str,
    configured_limit: int,
):
    """Exceeding the configured limit on every rate-limit tier returns HTTP 429 + Retry-After."""
    assert _bucket_for_path(path, method) == tier

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, setting_attr, configured_limit)
    monkeypatch.setattr(rate_limit, "_limiter", None)
    monkeypatch.setattr(rate_limit, "_limiter_url", None)

    app = _build_rate_limited_test_app()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        for attempt in range(configured_limit):
            res = await ac.request(method, path)
            assert res.status_code == 200, (
                f"tier={tier} attempt={attempt + 1}/{configured_limit} unexpectedly failed: {res.text}"
            )

        exceeded = await ac.request(method, path)
        assert exceeded.status_code == 429, (
            f"tier={tier} expected 429 on request {configured_limit + 1}, got {exceeded.status_code}"
        )
        assert exceeded.headers.get("Retry-After") == "60"
        body = exceeded.json()
        assert body["error"]["code"] == "rate_limited"


async def test_rate_limiter_fails_closed_when_redis_unreachable_in_prod(monkeypatch):
    """When Redis is unreachable and fail_closed_in_prod=True, requests are rejected with HTTP 429."""
    unreachable_redis = "redis://127.0.0.1:1/0?socket_connect_timeout=0.05&socket_timeout=0.05"
    limiter = RateLimiter(unreachable_redis, fail_closed_in_prod=True)
    assert limiter.fail_closed_in_prod is True
    allowed = await limiter.allow("voxdesk:rate:v1:auth:test", limit=10, window=60.0)
    assert allowed is False

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "redis_url", unreachable_redis)
    monkeypatch.setattr(rate_limit, "_limiter", limiter)
    monkeypatch.setattr(rate_limit, "_limiter_url", unreachable_redis)

    app = _build_rate_limited_test_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        res = await ac.post("/auth/login")
        assert res.status_code == 429
        assert res.headers.get("Retry-After") == "60"
        assert res.json()["error"]["code"] == "rate_limited"

    # Control-plane strict distributed limiter also fails closed when Redis is unreachable
    assert await distributed_rate_limit("voxdesk:webhooks:test", 10, 60.0) is False
