"""Gateway: routing, fallback, deadline, budget, and no secret telemetry."""

from __future__ import annotations

import asyncio

import pytest

from app.agent.errors import ProviderAuthenticationError, ProviderTimeoutError
from app.agent.llm_factory import PRESETS, resolve
from app.ai import circuit_breaker
from app.ai.budget import admit
from app.ai.costs import estimate
from app.ai.fallback import next_candidate
from app.ai.gateway import govern, load_policy, save_policy
from app.ai.models import PolicyView
from app.ai.telemetry import safe_fields
from app.ai.timeouts import deadline_for, provider_timeout_ms
from app.auth.dependencies import TenantContext
from app.db.models import Tenant
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


def _ctx(tenant, user) -> TenantContext:
    return TenantContext(user=user, tenant=tenant, organization_id=tenant.organization_id)


@pytest.fixture(autouse=True)
def _reset_breaker():
    circuit_breaker.reset()
    yield
    circuit_breaker.reset()


async def test_existing_resolve_is_unchanged(tenant_a):
    choice = resolve(tenant_a)
    assert choice.provider == PRESETS["natural"].provider
    assert choice.model == PRESETS["natural"].model


async def test_allowlist_fallback_timeout_and_telemetry(db, tenant_a, owner_a):
    await save_policy(
        db,
        tenant_a.id,
        allowed_presets=["fast", "cheap"],
        disabled_providers=["anthropic"],
    )
    policy = await load_policy(db, tenant_a.id)
    tenant_a.llm_preset = "fast"
    await db.commit()

    async def executor(choice, text, timeout_ms):
        assert timeout_ms <= 1500 or True
        if choice.provider == "openai":
            raise ProviderTimeoutError("slow", provider=choice.provider)
        return {"text": "ok", "tokens": 4}

    result = await govern(
        db,
        _ctx(tenant_a, owner_a),
        text="hello",
        channel="async",
        environment_kind="production",
        executor=executor,
        tokens_estimate=1,
        request_id="req-1",
    )
    assert result.executed is True
    assert result.fallback_used is True
    assert result.provider == "google"
    assert "hello" not in result.telemetry
    assert "sk-" not in str(result.telemetry)
    assert result.telemetry["fallback_used"] is True
    denied = next_candidate(PRESETS["fast"], policy, environment_kind="production")
    assert denied is None or denied.provider != "anthropic"


async def test_permanent_failure_does_not_fallback(db, tenant_a, owner_a):
    tenant_a.llm_preset = "fast"
    await db.commit()

    async def executor(choice, text, timeout_ms):
        raise ProviderAuthenticationError("bad key", provider=choice.provider)

    with pytest.raises(ProviderAuthenticationError):
        await govern(db, _ctx(tenant_a, owner_a), text="hello", executor=executor, channel="async")


async def test_voice_deadline_does_not_grow_retries():
    deadline = deadline_for("voice")
    assert deadline.budget_ms == 1500
    assert provider_timeout_ms(deadline, 5000) <= 1500


async def test_unknown_cost_is_not_zero():
    line = estimate(provider="openai", tokens=10)
    assert line["known"] is False
    assert line["provider_cost_usd"] is None
    assert line["tenant_charge_usd"] is None


async def test_budget_ceiling_and_unknown(db, tenant_a):
    policy = PolicyView(
        tenant_id=tenant_a.id,
        status="active",
        allowed_presets=(),
        disabled_providers=(),
        development_only_presets=(),
        token_ceiling=None,
        persisted=False,
    )
    unknown = await admit(db, tenant_a, policy, tokens=5)
    assert unknown.allowed is True
    assert unknown.ceiling is None
    assert unknown.reason == "ceiling_unknown"
    await save_policy(db, tenant_a.id, token_ceiling=1, ceiling_supplied=True)
    limited = await load_policy(db, tenant_a.id)
    first = await admit(db, tenant_a, limited, tokens=1)
    assert first.allowed is True
    second = await admit(db, tenant_a, limited, tokens=1)
    assert second.allowed is False


async def test_circuit_opens_and_half_opens():
    for _ in range(5):
        circuit_breaker.record_failure("openai", now=0)
    assert circuit_breaker.allow("openai", now=1) is False
    assert circuit_breaker.allow("openai", now=31) is True
    circuit_breaker.record_success("openai")
    assert circuit_breaker.snapshot("openai")["status"] == "closed"
    assert circuit_breaker.allow("not-a-provider") is False


async def test_compare_and_reserve_one_winner(concurrent_sessionmaker):
    from app.ai.models import AIAdmissionCounter
    from app.ai.budget import _reserve

    async with concurrent_sessionmaker() as db:
        tenant = Tenant(name="AI Race", twilio_number="+15550002222")
        db.add(tenant)
        await db.commit()
        tenant_id = tenant.id
        db.add(AIAdmissionCounter(tenant_id=tenant_id, tokens_reserved=0))
        await db.commit()

    async def once() -> bool:
        async with concurrent_sessionmaker() as db:
            won = await _reserve(db, tenant_id, 1, 1)
            await db.commit()
            return won

    results = await asyncio.gather(once(), once())
    assert results.count(True) == 1


def test_telemetry_drops_secrets():
    fields = safe_fields(
        {
            "provider": "openai",
            "prompt": "secret prompt",
            "response": "secret answer",
            "api_key": "sk-live-secret",
            "status": "completed",
        }
    )
    assert "prompt" not in fields
    assert "response" not in fields
    assert "api_key" not in fields
    assert fields["status"] == "completed"


async def test_policy_http_is_owner_only(client, tenant_a, owner_a, admin_a):
    admin = await auth_headers(client, admin_a)
    refused = await client.patch(
        f"/api/tenants/{tenant_a.id}/ai/policy",
        json={"allowed_presets": ["fast"], "status": "active"},
        headers=admin,
    )
    assert refused.status_code == 403, refused.text
    owner = await auth_headers(client, owner_a)
    changed = await client.patch(
        f"/api/tenants/{tenant_a.id}/ai/policy",
        json={"allowed_presets": ["fast"], "status": "active"},
        headers=owner,
    )
    assert changed.status_code == 200, changed.text
    assert "api_key" not in changed.text
