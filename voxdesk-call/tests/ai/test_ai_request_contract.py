"""Request identity and fail-closed runtime entry checks (no provider calls)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.ai.context import RuntimeContext
from app.ai.models import GovernanceError
from app.ai.runtime import govern_text_reply
from app.tenancy.isolation import BoundaryDenied


def test_runtime_context_is_immutable_and_rejects_foreign_tenant():
    tenant_id = uuid.uuid4()
    ctx = RuntimeContext(
        tenant_id=tenant_id,
        environment_id=None,
        environment_kind="production",
        channel="text",
        principal="agent_runtime",
        request_id="request-1",
        trace_id="trace-1",
    )
    with pytest.raises((AttributeError, TypeError)):
        ctx.tenant_id = uuid.uuid4()  # type: ignore[misc]
    with pytest.raises(BoundaryDenied):
        ctx.reject_claim(uuid.uuid4())
    ctx.reject_claim(tenant_id)


@pytest.mark.asyncio
async def test_sessionless_text_entry_fails_before_provider_loop():
    called = False

    async def complete_turn(*_args):
        nonlocal called
        called = True
        return {"reply": "should not run"}

    agent = SimpleNamespace(
        handlers=SimpleNamespace(session=None),
        channel="sms",
        tenant=SimpleNamespace(id=uuid.uuid4()),
        provider="openai",
        model="gpt-4o-mini",
        complete_turn=complete_turn,
    )
    with pytest.raises(GovernanceError) as caught:
        await govern_text_reply(agent, [], "hello")
    assert caught.value.code == "runtime_context_missing"
    assert called is False
