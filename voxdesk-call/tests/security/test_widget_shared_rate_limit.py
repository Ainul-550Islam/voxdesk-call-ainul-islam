"""Public widget budgets are enforced through the shared identity limiter.

Regression: the widget limiter kept counts in a per-process dictionary, so with N API
replicas a client could make N times the configured budget. The budget now goes through
``allow_identity_action`` (Redis when configured, in-process otherwise).
"""

from __future__ import annotations

import pytest

from app.domain.public_site_models import PublicDomainError
from app.services import public_widget_service as svc


@pytest.mark.asyncio
async def test_widget_budget_allows_limit_then_returns_429(monkeypatch):
    from app.core.rate_limit import reset_identity_limiter
    from app.core.config import settings

    monkeypatch.setattr(settings, "redis_url", "")
    reset_identity_limiter()
    await svc._enforce_widget_rate_limit("ip:198.51.100.20", 2)
    await svc._enforce_widget_rate_limit("ip:198.51.100.20", 2)
    with pytest.raises(PublicDomainError) as exc:
        await svc._enforce_widget_rate_limit("ip:198.51.100.20", 2)
    assert exc.value.status_code == 429
    assert exc.value.code == "RATE_LIMITED"
    reset_identity_limiter()


@pytest.mark.asyncio
async def test_widget_budget_is_spent_in_the_shared_limiter(monkeypatch):
    calls: list[dict] = []

    async def _deny(**kwargs):
        calls.append(kwargs)
        return False

    monkeypatch.setattr(svc, "allow_identity_action", _deny)
    with pytest.raises(PublicDomainError) as exc:
        await svc._enforce_widget_rate_limit("key:abc", 30)
    assert exc.value.status_code == 429
    assert calls == [{"action": "public_widget", "who": "key:abc", "limit": 30, "window": 60.0}]
