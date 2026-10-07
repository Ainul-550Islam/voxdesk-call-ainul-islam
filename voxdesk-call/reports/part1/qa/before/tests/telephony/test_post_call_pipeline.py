"""Main runtime contracts: terminal state -> durable job -> real worker/core steps."""
from __future__ import annotations

import json
import socket
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.agent.errors import ProviderTimeoutError
from app.ai import circuit_breaker, post_call_llm
from app.core.config import settings
from app.db.models import Call, CallStatus, DurableJob, Environment, Speaker, Turn, UsageEvent
from app.db.telephony_models import PostCallStepRun
from app.jobs.idempotency import post_call_key
from app.jobs.models import PermanentJobError
from app.jobs.types import JobType
from app.jobs.worker import JobWorker, build_handlers
from app.outbox.models import OutboxEvent
from app.telephony.call_state import apply_status, publish_transition
from app.telephony.post_call import CORE_STEPS, PostCallPipeline, enqueue_post_call, make_post_call_handler
from app.webhooks.call_event_bridge import publish_call_event
from tests.conftest import make_call


@pytest.fixture(autouse=True)
def isolated_provider(monkeypatch):
    circuit_breaker.reset()
    monkeypatch.setattr(settings, "redis_url", "")
    for field in ("openai_api_key", "google_api_key", "anthropic_api_key"):
        monkeypatch.setattr(settings, field, "")
    yield
    circuit_breaker.reset()


async def prepared(db, tenant, **kwargs):
    tenant.llm_preset = "fast"
    await db.commit()
    call = await make_call(db, tenant, summary=None, booked=False, **kwargs)
    db.add(Turn(call_id=call.id, speaker=Speaker.USER, text="Please book Tuesday. Thank you."))
    await db.commit()
    return call


def scope(call, tenant):
    return dict(tenant_id=tenant.id, environment_id=call.environment_id, organization_id=tenant.organization_id)


async def completed_executor(choice, prompt, timeout_ms):
    assert timeout_ms <= 5000
    value = {"summary": "Caller requested Tuesday.", "outcome": "booking_requested"} if '"summary"' in prompt else {"sentiment": "positive"}
    return {"text": json.dumps(value), "tokens": 8}


async def step_rows(db, call):
    return {row.step: row for row in (await db.scalars(select(PostCallStepRun).where(
        PostCallStepRun.call_id == call.id, PostCallStepRun.tenant_id == call.tenant_id,
        PostCallStepRun.environment_id == call.environment_id,
    ))).all()}


async def test_terminal_transition_enqueues_once_in_same_transaction(db, tenant_a):
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    await publish_transition(db, call, apply_status(call, CallStatus.COMPLETED))
    await publish_call_event(db, call, "call_ended")
    jobs = list((await db.scalars(select(DurableJob).where(DurableJob.job_type == JobType.POST_CALL))).all())
    assert len(jobs) == 1
    assert jobs[0].idempotency_key == post_call_key(call.id)
    assert jobs[0].payload == {"call_id": str(call.id), "pipeline_version": 1}
    assert jobs[0].environment_id == call.environment_id
    assert jobs[0].organization_id == tenant_a.organization_id
    call_id = call.id
    await db.rollback()
    assert not list((await db.scalars(select(DurableJob).where(DurableJob.job_type == JobType.POST_CALL))).all())
    assert not list((await db.scalars(select(OutboxEvent))).all())
    assert (await db.get(Call, call_id)).status == CallStatus.IN_PROGRESS


async def test_enqueue_failure_rolls_back_terminal_fact(db, tenant_a, monkeypatch):
    from app.telephony import post_call
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    call_id = call.id

    async def fail(*args, **kwargs):
        raise RuntimeError("job admission failed")

    monkeypatch.setattr(post_call, "enqueue", fail)
    with pytest.raises(RuntimeError, match="job admission failed"):
        await publish_transition(db, call, apply_status(call, CallStatus.COMPLETED))
    await db.rollback()
    assert not list((await db.scalars(select(OutboxEvent))).all())
    assert (await db.get(Call, call_id)).status == CallStatus.IN_PROGRESS


async def test_real_worker_governed_http_persists_output_and_usage(db, tenant_a, sessionmaker_, monkeypatch):
    call = await prepared(db, tenant_a, status=CallStatus.IN_PROGRESS)
    requests = []
    monkeypatch.setattr(settings, "openai_api_key", "test-post-call-only")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])

    async def receive(request):
        assert request.url.host == "api.openai.com"
        assert request.method == "POST" and request.headers["authorization"] == "Bearer test-post-call-only"
        data = json.loads(request.content)
        prompt = data["messages"][0]["content"]
        assert "Please book Tuesday" in prompt
        requests.append(data)
        value = {"summary": "Caller requested Tuesday.", "outcome": "booking_requested"} if '"summary"' in prompt else {"sentiment": "positive"}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(value)}}], "usage": {"total_tokens": 13}})

    await publish_transition(db, call, apply_status(call, CallStatus.COMPLETED))
    await db.commit()
    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
        adapter = post_call_llm.StructuredExecutor(post_call_llm.SUMMARY_SCHEMA, client=client)
        worker = JobWorker(sessionmaker_, handlers={JobType.POST_CALL: make_post_call_handler(sessionmaker_, executor=adapter)}, job_types=(JobType.POST_CALL,))
        job = await worker.run_once()
        assert job is not None and job.status == "succeeded"
        assert await worker.run_once() is None
        again = await PostCallPipeline(sessionmaker_, executor=adapter).run(call.id, **scope(call, tenant_a))
        assert again.success
    await db.refresh(call)
    assert call.summary == "Caller requested Tuesday."
    assert call.booked is False  # A model classification is not booking proof.
    rows = await step_rows(db, call)
    assert set(rows) == set(CORE_STEPS) | {"analyzed_webhook"}
    assert all(row.status == "completed" and row.attempts == 1 for row in rows.values())
    assert rows["summary"].output["outcome"] == "booking_requested"
    assert rows["sentiment"].output == {"sentiment": "positive"}
    assert "Please book" not in json.dumps(rows["transcript"].output)
    assert len(requests) == 2
    usage = list((await db.scalars(select(UsageEvent).where(UsageEvent.tenant_id == tenant_a.id))).all())
    assert len(usage) == 2
    assert all(row.telemetry[0]["tokens"] == 13 for key, row in rows.items() if key in {"summary", "sentiment"})
    # The fact certifies analysis, not the still-pending QA/CRM stages.
    events = list((await db.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "call_analyzed"))).all())
    assert len(events) == 1 and events[0].payload["analysis_result_ids"] == []
    assert rows["analysis_plan"].telemetry == rows["analyzed_webhook"].telemetry == []


async def test_step_failure_isolated_and_retry_skips_completed(db, tenant_a, sessionmaker_):
    call = await prepared(db, tenant_a)
    counts = {"summary": 0, "sentiment": 0}

    async def executor(choice, prompt, timeout_ms):
        key = "summary" if '"summary"' in prompt else "sentiment"
        counts[key] += 1
        if key == "summary":
            raise ProviderTimeoutError("sensitive text must not be saved", provider=choice.provider)
        return await completed_executor(choice, prompt, timeout_ms)

    first = await PostCallPipeline(sessionmaker_, executor=executor).run(call.id, **scope(call, tenant_a))
    assert not first.success and first.failure_class.value == "transient"
    rows = await step_rows(db, call)
    assert rows["summary"].status == "failed" and rows["summary"].retryable
    assert rows["sentiment"].status == "completed"
    assert rows["summary"].error == "timeout"
    call_id, owned_scope = call.id, scope(call, tenant_a)
    await db.rollback()
    second = await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call_id, **owned_scope)
    assert second.success
    db.expire_all()
    rows = {row.step: row for row in (await db.scalars(select(PostCallStepRun))).all()}
    assert rows["summary"].attempts == 2
    assert rows["sentiment"].attempts == 1


async def test_missing_credentials_are_durable_not_success(db, tenant_a, sessionmaker_):
    call = await prepared(db, tenant_a)
    result = await PostCallPipeline(sessionmaker_).run(call.id, **scope(call, tenant_a))
    assert not result.success and result.failure_class.value == "permanent"
    rows = await step_rows(db, call)
    assert rows["transcript"].status == "completed"
    for key in ("summary", "sentiment"):
        assert rows[key].status == "not_configured" and rows[key].output == {}
        assert rows[key].error == "provider_not_configured"
    await db.refresh(call)
    assert call.summary is None


@pytest.mark.parametrize("boundary", ["tenant", "environment", "organization"])
async def test_foreign_scope_cannot_read_or_analyze(db, tenant_a, tenant_b, sessionmaker_, boundary):
    call = await prepared(db, tenant_a)
    values = scope(call, tenant_a)
    if boundary == "tenant":
        values["tenant_id"] = tenant_b.id
    elif boundary == "environment":
        values["environment_id"] = uuid.uuid4()
    else:
        values["organization_id"] = tenant_b.organization_id
    with pytest.raises(PermanentJobError):
        await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **values)
    assert not await step_rows(db, call)


async def test_late_transcript_can_retry_without_replaying_finished_steps(db, tenant_a, sessionmaker_):
    call = await make_call(db, tenant_a, summary=None)
    first = await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **scope(call, tenant_a))
    assert not first.success and first.failure_class.value == "transient"
    db.add(Turn(call_id=call.id, speaker=Speaker.USER, text="Late final transcript"))
    await db.commit()
    result = await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **scope(call, tenant_a))
    assert result.success


async def test_transcript_erasure_blocks_recovery_instead_of_resurrecting_text(db, tenant_a, sessionmaker_):
    call = await prepared(db, tenant_a)
    await PostCallPipeline(sessionmaker_).run(call.id, **scope(call, tenant_a))
    turn = (await db.scalars(select(Turn).where(Turn.call_id == call.id))).one()
    await db.delete(turn)
    await db.commit()
    result = await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **scope(call, tenant_a), retry_nontransient=True)
    assert not result.success
    rows = await step_rows(db, call)
    assert rows["summary"].status == "blocked"
    await db.refresh(call)
    assert call.summary is None


async def test_existing_summary_not_overwritten(db, tenant_a, sessionmaker_):
    call = await prepared(db, tenant_a)
    call.summary = "Human verified summary"
    await db.commit()
    result = await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **scope(call, tenant_a))
    assert result.success
    await db.refresh(call)
    assert call.summary == "Human verified summary"
    assert (await step_rows(db, call))["summary"].output["summary"] == "Caller requested Tuesday."


async def test_no_terminal_job_on_ringing_or_transfer_acceptance(db, tenant_a):
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    await publish_transition(db, call, apply_status(call, CallStatus.TRANSFERRED))
    assert not list((await db.scalars(select(DurableJob).where(DurableJob.job_type == JobType.POST_CALL))).all())
    with pytest.raises(ValueError, match="terminal"):
        await enqueue_post_call(db, call)


def test_main_worker_registry_contains_post_call_without_test_injection():
    assert JobType.POST_CALL in JobType.ALL
    assert callable(build_handlers()[JobType.POST_CALL])


async def test_inactive_environment_blocks_execution(db, tenant_a, sessionmaker_):
    call = await prepared(db, tenant_a)
    environment = await db.get(Environment, call.environment_id)
    environment.status = "suspended"
    await db.commit()
    with pytest.raises(PermanentJobError) as exc:
        await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **scope(call, tenant_a))
    assert exc.value.category == "post_call_scope_inactive"
    assert not await step_rows(db, call)


async def test_default_registered_handler_executes_main_code(db, tenant_a, sessionmaker_, monkeypatch):
    import app.db.session as sessions
    monkeypatch.setattr(sessions, "get_sessionmaker", lambda: sessionmaker_)
    call = await prepared(db, tenant_a)
    job, _ = await enqueue_post_call(db, call)
    job_id = job.id
    await db.commit()
    # No injected handler or executor: the ordinary registry and module run.
    handled = await JobWorker(sessionmaker_, job_types=(JobType.POST_CALL,)).run_once()
    assert handled.id == job_id and handled.status == "dead_letter"
    rows = await step_rows(db, call)
    assert rows["transcript"].status == "completed"
    assert rows["summary"].status == rows["sentiment"].status == "not_configured"
    assert handled.last_error_category == "post_call_step_incomplete"


async def test_real_status_callback_admits_job(db, tenant_a, client):
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    form = {"CallSid": call.call_sid, "CallStatus": "completed", "CallDuration": "30"}
    for _ in range(2):
        response = await client.post("/telephony/status", data=form)
        assert response.status_code == 200
    rows = list((await db.scalars(select(DurableJob).where(
        DurableJob.job_type == JobType.POST_CALL, DurableJob.tenant_id == tenant_a.id,
    ))).all())
    assert len(rows) == 1 and rows[0].payload["call_id"] == str(call.id)


async def test_existing_job_identity_cannot_silently_target_another_call(db, tenant_a):
    call = await prepared(db, tenant_a)
    job, _ = await enqueue_post_call(db, call)
    job.payload = {"call_id": str(uuid.uuid4()), "pipeline_version": 1}
    await db.commit()
    with pytest.raises(PermanentJobError) as error:
        await enqueue_post_call(db, call)
    assert error.value.category == "post_call_scope_conflict"


async def test_summary_repair_failure_does_not_erase_actual_usage(db, tenant_a, sessionmaker_):
    from app.agent.errors import ProviderAuthenticationError
    call = await prepared(db, tenant_a)
    attempts = 0

    async def executor(choice, prompt, timeout_ms):
        nonlocal attempts
        if '"summary"' in prompt:
            attempts += 1
            if attempts == 1:
                return {"text": "invalid JSON", "tokens": 17}
            raise ProviderAuthenticationError("do not persist this detail", provider=choice.provider)
        return await completed_executor(choice, prompt, timeout_ms)

    result = await PostCallPipeline(sessionmaker_, executor=executor).run(call.id, **scope(call, tenant_a))
    assert not result.success
    rows = await step_rows(db, call)
    assert rows["summary"].error == "authentication_error"
    assert rows["sentiment"].status == "completed"
    usage = list((await db.scalars(select(UsageEvent).where(UsageEvent.tenant_id == tenant_a.id))).all())
    assert len(usage) == 2  # invalid-but-billed first summary, plus sentiment


async def test_oversize_transcript_not_silently_truncated(db, tenant_a, sessionmaker_):
    call = await prepared(db, tenant_a)
    turn = (await db.scalars(select(Turn).where(Turn.call_id == call.id))).one()
    turn.text = "x" * 7000
    await db.commit()
    seen = []

    async def executor(*args):
        seen.append(args)
        raise AssertionError("Oversized text must not reach provider")

    result = await PostCallPipeline(sessionmaker_, executor=executor).run(call.id, **scope(call, tenant_a))
    assert not result.success and not seen
    rows = await step_rows(db, call)
    assert rows["transcript"].status == "unsupported"
    assert rows["transcript"].error == "transcript_too_large"
