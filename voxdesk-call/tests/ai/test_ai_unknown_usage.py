"""Unknown provider usage remains unknown and does not reconcile to zero."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ai import circuit_breaker
from app.ai.gateway import save_policy
from app.ai.models import AIAdmissionCounter
from app.ai.runtime import invoke
from app.auth.dependencies import TenantContext
from app.db.models import UsageEvent, UsageMetric

pytestmark = pytest.mark.asyncio


def _ctx(tenant, user):
    return TenantContext(user=user, tenant=tenant, organization_id=tenant.organization_id)


@pytest.fixture(autouse=True)
def _reset_breaker():
    circuit_breaker.reset()
    yield
    circuit_breaker.reset()


async def test_missing_usage_is_none_and_keeps_conservative_reservation(db, tenant_a, owner_a):
    tenant_a.llm_preset = "fast"
    await save_policy(db, tenant_a.id, token_ceiling=20, ceiling_supplied=True)
    await db.commit()

    async def executor(choice, text, timeout_ms):
        return {"text": "completed"}  # provider reported no usage block

    result = await invoke(
        db,
        _ctx(tenant_a, owner_a),
        text="hello",
        executor=executor,
        tokens_estimate=3,
        request_id="unknown-usage-case",
    )
    assert result.executed is True
    assert result.telemetry["tokens"] is None
    counter = await db.get(AIAdmissionCounter, tenant_a.id)
    assert counter is not None
    await db.refresh(counter)
    assert counter.tokens_reserved == 3
    events = (
        await db.execute(
            select(UsageEvent).where(
                UsageEvent.tenant_id == tenant_a.id,
                UsageEvent.metric == UsageMetric.LLM_TOKEN,
            )
        )
    ).scalars().all()
    assert events == []
