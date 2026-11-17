"""PART 14 / P14-04 — pre-call budget, post-call durability, executor."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.workflows import executor, post_call, pre_call


class Clock:
    """A monotonic clock that advances by a fixed step on every read."""

    def __init__(self, step: float = 0.0) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


@pytest.mark.asyncio
async def test_fast_fetchers_all_run_and_merge_variables() -> None:
    async def contact(_ctx):
        return {"contact_name": "Rahim"}

    async def memory(_ctx):
        return {"last_order": "refill"}

    result = await pre_call.run_pre_call(
        {"phone": "+8801712345678"},
        {"contact": contact, "memory": memory},
        budget_ms=500,
    )
    assert result.variables == {"contact_name": "Rahim", "last_order": "refill"}
    assert [step.status for step in result.steps] == ["ok", "ok"]
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_a_slow_fetcher_times_out_and_uses_its_fallback() -> None:
    async def slow(_ctx):
        await asyncio.sleep(5)
        return {"never": True}

    async def fast(_ctx):
        return {"ok": True}

    result = await pre_call.run_pre_call(
        {},
        {"slow": slow, "fast": fast},
        budget_ms=100,
        fallbacks={"slow": {"last_seen": "unknown"}},
    )
    assert result.timed_out is True
    assert result.fallback_used is True
    assert result.variables["last_seen"] == "unknown"
    assert result.variables["ok"] is True
    assert [step.status for step in result.steps] == ["timeout", "ok"]


@pytest.mark.asyncio
async def test_a_raising_fetcher_never_drops_the_call() -> None:
    async def broken(_ctx):
        raise RuntimeError("CRM 503")

    result = await pre_call.run_pre_call({}, {"crm": broken}, budget_ms=200)
    assert result.steps[0].status == "error"
    assert "CRM 503" in (result.steps[0].error or "")
    assert result.variables == {}


@pytest.mark.asyncio
async def test_one_slow_step_cannot_starve_the_rest_of_the_budget() -> None:
    """A step may take at most 60 % of the budget, and the next step still runs."""

    async def slow(_ctx):
        await asyncio.sleep(0.5)
        return {"a": 1}

    async def fast(_ctx):
        return {"b": 2}

    result = await pre_call.run_pre_call(
        {}, {"first": slow, "second": fast}, budget_ms=100
    )
    assert result.steps[0].status == "timeout"
    assert result.steps[0].elapsed_ms < 100
    assert result.steps[1].status == "ok"
    assert result.variables == {"b": 2}
    assert result.timed_out is True


@pytest.mark.asyncio
async def test_a_zero_budget_is_a_programming_error() -> None:
    with pytest.raises(ValueError):
        await pre_call.run_pre_call({}, {}, budget_ms=0)


@pytest.mark.asyncio
async def test_post_call_records_each_step_and_survives_one_failure(db, tenant_a) -> None:
    call_id = uuid.uuid4()

    async def ok(_payload, _ctx):
        return {"posted": True}

    async def boom(_payload, _ctx):
        raise RuntimeError("slack 500")

    steps = [post_call.Step("notify", payload={"channel": "#ops"}), post_call.Step("crm")]
    results = await post_call.run_post_call(
        db,
        tenant_id=tenant_a.id,
        call_id=call_id,
        steps=steps,
        handlers={"notify": ok, "crm": boom},
    )
    assert results[0]["status"] == "succeeded"
    assert results[1]["status"] == "failed"
    assert results[1]["retryable"] is True

    history = await executor.step_history(db, tenant_a.id, call_id)
    assert {row["step"] for row in history} == {"notify", "crm"}
    assert any(row["status"] == "failed" for row in history)


@pytest.mark.asyncio
async def test_the_same_step_is_never_run_twice_for_one_call(db, tenant_a) -> None:
    call_id = uuid.uuid4()
    calls = {"count": 0}

    async def handler(_payload, _ctx):
        calls["count"] += 1
        return {"ok": True}

    step = post_call.Step("notify", install_id=str(uuid.uuid4()))
    first = await post_call.run_step(
        db, tenant_id=tenant_a.id, call_id=call_id, step=step, handler=handler
    )
    second = await post_call.run_step(
        db, tenant_id=tenant_a.id, call_id=call_id, step=step, handler=handler
    )
    assert first["status"] == "succeeded"
    assert second["status"] == "skipped"
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_the_idempotency_key_changes_when_the_payload_changes() -> None:
    call_id = uuid.uuid4()
    left = post_call.Step("notify", payload={"channel": "#a"})
    right = post_call.Step("notify", payload={"channel": "#b"})
    assert left.idempotency_key(call_id) != right.idempotency_key(call_id)


def test_plan_steps_maps_installs_through_the_workflow_table() -> None:
    class Install:
        def __init__(self, key: str) -> None:
            self.id = uuid.uuid4()
            self.workflow_key = key
            self.config = {"channel": "#ops"}

    steps = post_call.plan_steps(
        [Install("slack"), Install("hubspot")],
        {"slack": ["notify"], "hubspot": ["upsert_contact"], "unknown": ["x"]},
    )
    assert [step.name for step in steps] == ["notify", "upsert_contact"]
    assert steps[0].payload == {"channel": "#ops"}


@pytest.mark.asyncio
async def test_the_executor_enqueues_one_durable_job_per_step(db, tenant_a) -> None:
    call_id = uuid.uuid4()
    steps = [post_call.Step("notify"), post_call.Step("audit")]
    first = await executor.enqueue_post_call(
        db, tenant_id=tenant_a.id, call_id=call_id, steps=steps
    )
    assert [row.created for row in first] == [True, True]
    again = await executor.enqueue_post_call(
        db, tenant_id=tenant_a.id, call_id=call_id, steps=steps
    )
    assert [row.created for row in again] == [False, False]
    assert await executor.outstanding(db, tenant_a.id) == 0
