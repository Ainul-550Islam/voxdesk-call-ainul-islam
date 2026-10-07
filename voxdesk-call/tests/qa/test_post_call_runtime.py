"""Canonical post-call QA admission and real governed worker contracts."""
import json
import socket
import uuid

import httpx
import pytest
from sqlalchemy import select, delete

from app.agent.errors import ProviderTimeoutError
from app.ai import circuit_breaker, post_call_llm
from app.core.config import settings
from app.db.models import DurableJob, Turn, UsageEvent
from app.db.telephony_models import PostCallStepRun
from app.jobs.types import JobType
from app.jobs.worker import JobWorker, build_handlers
from app.outbox.models import OutboxEvent
from app.qa import auto_review, sampling, rubrics, service
from app.qa.exceptions import InvalidScore
from app.qa.models import AutoReviewRun, QAReview, QAReviewItem, SampleSelection, ScorecardItem
from app.telephony.post_call import PostCallPipeline, _WorkerAIContext
from tests.conftest import auth_headers
from tests.telephony.test_post_call_pipeline import prepared, scope, completed_executor
from tests.webhooks.conftest import webhook_redis_url  # noqa: F401


@pytest.fixture(autouse=True)
def configured(monkeypatch, request):
    circuit_breaker.reset()
    monkeypatch.setattr(settings, "redis_url", request.getfixturevalue("webhook_redis_url"))
    for name in ("openai_api_key", "anthropic_api_key", "google_api_key"):
        monkeypatch.setattr(settings, name, "")
    yield
    circuit_breaker.reset()


async def card_for(db, tenant, call, *, count=1):
    card = await rubrics.create_scorecard(db, tenant_id=tenant.id, environment_id=call.environment_id,
        name="Card-" + uuid.uuid4().hex[:8], sections=[{"name": "Opening", "items": [
            {"name": f"Criterion {n}", "required": True, "min_score": 0, "max_score": 100} for n in range(count)]}])
    await db.commit()
    return card


async def automatic(db, tenant, call, card, *, percent=100):
    rule = await sampling.create_rule(db, tenant_id=tenant.id, environment_id=call.environment_id,
        name="Rule-" + uuid.uuid4().hex[:8], kind="percentage", percent=percent, scorecard_id=card.id)
    await db.commit()
    return rule


async def review_run(db, tenant, *, count=1):
    call = await prepared(db, tenant)
    card = await card_for(db, tenant, call, count=count)
    review, _ = await service.create_review(db, tenant_id=tenant.id, call_id=call.id,
        scorecard_id=card.id, actor_id=None, environment_id=call.environment_id)
    run, _ = await auto_review.enqueue(db, tenant_id=tenant.id, review=review)
    await db.commit()
    return call, card, review, run


async def test_terminal_pipeline_admits_once_then_http_worker_suggests_only(db, tenant_a, sessionmaker_, monkeypatch):
    call = await prepared(db, tenant_a)
    card = await card_for(db, tenant_a, call)
    await automatic(db, tenant_a, call, card)
    pipeline = PostCallPipeline(sessionmaker_, executor=completed_executor)
    assert (await pipeline.run(call.id, **scope(call, tenant_a))).success
    assert (await pipeline.run(call.id, **scope(call, tenant_a))).success
    run = (await db.scalars(select(AutoReviewRun))).one()
    review = (await db.scalars(select(QAReview))).one()
    assert review.created_by is None and review.status == "created" and review.overall_score is None
    assert len(list((await db.scalars(select(SampleSelection))).all())) == 1
    jobs = list((await db.scalars(select(DurableJob).where(DurableJob.job_type == JobType.QA_AUTO_REVIEW))).all())
    assert len(jobs) == 1 and jobs[0].environment_id == call.environment_id
    assert callable(build_handlers()[JobType.QA_AUTO_REVIEW])
    requests = []
    monkeypatch.setattr(settings, "openai_api_key", "qa-test-only")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])

    async def receive(request):
        assert request.method == "POST" and request.url.host == "api.openai.com"
        assert request.headers["authorization"] == "Bearer qa-test-only"
        body = json.loads(request.content)
        prompt = json.loads(body["messages"][0]["content"])
        assert prompt["rubric"] == run.rubric_snapshot
        assert "Please book Tuesday" in prompt["turns"][0]["text"]
        requests.append(body)
        value = {"items": [{"item_id": prompt["rubric"]["items"][0]["id"], "score": 80,
                             "turn_ids": [prompt["turns"][0]["turn_id"]]}]}
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(value)}}], "usage": {"total_tokens": 17}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
        adapter = post_call_llm.StructuredExecutor(auto_review.response_schema(run.rubric_snapshot), client=client)
        worker = JobWorker(sessionmaker_, handlers={JobType.QA_AUTO_REVIEW: auto_review.make_auto_review_handler(sessionmaker_, executor=adapter)}, job_types=(JobType.QA_AUTO_REVIEW,))
        assert (await worker.run_once()).status == "succeeded"
        assert await worker.run_once() is None
    await db.refresh(run)
    await db.refresh(review)
    item = (await db.scalars(select(QAReviewItem))).one()
    assert len(requests) == 1 and run.status == "completed" and run.token_count == 17
    assert item.ai_score == 80 and item.human_score is None and item.accepted_source == "unset"
    assert review.overall_score is None and review.status == "created"
    assert len(list((await db.scalars(select(UsageEvent))).all())) == 3  # Two built-in calls plus measured QA.


async def test_default_handler_records_missing_provider_without_scores(db, tenant_a, sessionmaker_, monkeypatch):
    import app.db.session as sessions
    monkeypatch.setattr(sessions, "get_sessionmaker", lambda: sessionmaker_)
    _, _, _, run = await review_run(db, tenant_a)
    job = await JobWorker(sessionmaker_, job_types=(JobType.QA_AUTO_REVIEW,)).run_once()
    assert job.status == "dead_letter" and job.last_error_category == "provider_not_configured"
    await db.refresh(run)
    assert run.status == "not_configured" and not run.suggestion
    assert all(item.ai_score is None for item in (await db.scalars(select(QAReviewItem))).all())


async def test_sampling_zero_and_foreign_tenant_do_not_admit(db, tenant_a, tenant_b, sessionmaker_):
    call = await prepared(db, tenant_a)
    other = await prepared(db, tenant_b)
    card = await card_for(db, tenant_a, call)
    other_card = await card_for(db, tenant_b, other)
    await automatic(db, tenant_a, call, card, percent=0)
    await automatic(db, tenant_b, other, other_card, percent=100)
    assert (await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **scope(call, tenant_a))).success
    assert not list((await db.scalars(select(AutoReviewRun))).all())
    assert not list((await db.scalars(select(SampleSelection))).all())


async def test_failed_sampling_admission_rolls_back_all_children_but_not_analysis(db, tenant_a, sessionmaker_, monkeypatch):
    call = await prepared(db, tenant_a)
    card = await card_for(db, tenant_a, call)
    await automatic(db, tenant_a, call, card)
    original = auto_review.enqueue
    async def fail(*args, **kwargs):
        await original(*args, **kwargs)
        raise InvalidScore("injected admission failure")
    monkeypatch.setattr(auto_review, "enqueue", fail)
    result = await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **scope(call, tenant_a))
    assert not result.success
    for model in (SampleSelection, QAReview, AutoReviewRun):
        assert not list((await db.scalars(select(model))).all())
    assert not list((await db.scalars(select(DurableJob).where(DurableJob.job_type == JobType.QA_AUTO_REVIEW))).all())
    step = (await db.scalars(select(PostCallStepRun).where(PostCallStepRun.step == "qa_sampling"))).one()
    assert step.status == "blocked" and step.error == "invalid_score"
    assert len(list((await db.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "call_analyzed"))).all())) == 1


@pytest.mark.parametrize("bad", ["empty", "partial", "duplicate", "foreign_evidence", "boolean", "extra"])
async def test_invalid_response_cannot_partially_apply_scores(db, tenant_a, bad):
    call, _, review, run = await review_run(db, tenant_a, count=2)
    ids = [item["id"] for item in run.rubric_snapshot["items"]]
    rows = [{"item_id": value, "score": 80, "turn_ids": []} for value in ids]
    if bad == "empty":
        rows = []
    elif bad == "partial":
        rows = rows[:1]
    elif bad == "duplicate":
        rows[1]["item_id"] = ids[0]
    elif bad == "foreign_evidence":
        rows[1]["turn_ids"] = [str(uuid.uuid4())]
    elif bad == "boolean":
        rows[1]["score"] = True
    else:
        rows[1]["secret_input"] = "must-not-be-persisted"
    async def executor(*args):
        return {"text": json.dumps({"items": rows}), "tokens": 7}
    result = await auto_review.process_run(db, tenant_id=tenant_a.id, run_id=run.id,
        ctx=_WorkerAIContext(tenant_a, call.environment_id), executor=executor)
    assert result.status == "permanent_failure" and result.error_class == "validation" and result.suggestion == {}
    assert all(item.ai_score is None for item in (await db.scalars(select(QAReviewItem).where(QAReviewItem.review_id == review.id))).all())
    assert len(list((await db.scalars(select(UsageEvent))).all())) == 1  # Bad output was still paid/measured.


async def test_no_transcript_never_invents_a_review(db, tenant_a):
    call, _, _, run = await review_run(db, tenant_a)
    await db.execute(delete(Turn).where(Turn.call_id == call.id))
    await db.commit()
    async def forbidden(*args):
        raise AssertionError("No transcript must not reach inference")
    result = await auto_review.process_run(db, tenant_id=tenant_a.id, run_id=run.id,
        ctx=_WorkerAIContext(tenant_a, call.environment_id), executor=forbidden)
    assert result.status == "failed" and result.error_class == "no_transcript" and result.suggestion == {}


async def test_retry_refuses_changed_transcript_and_pins_rubric(db, tenant_a):
    call, _, _, run = await review_run(db, tenant_a)
    original_rubric = json.loads(json.dumps(run.rubric_snapshot))
    item = (await db.scalars(select(ScorecardItem))).one()
    item.name = "Changed after admission"
    await db.commit()
    async def timeout(choice, text, timeout_ms):
        assert json.loads(text)["rubric"] == original_rubric
        raise ProviderTimeoutError("not logged", provider=choice.provider)
    result = await auto_review.process_run(db, tenant_id=tenant_a.id, run_id=run.id,
        ctx=_WorkerAIContext(tenant_a, call.environment_id), executor=timeout)
    assert result.status == "failed" and result.transcript_snapshot
    await db.commit()
    turn = (await db.scalars(select(Turn).where(Turn.call_id == call.id))).one()
    turn.text = "Changed transcript"
    await db.commit()
    async def forbidden(*args):
        raise AssertionError("Frozen evidence changed")
    result = await auto_review.process_run(db, tenant_id=tenant_a.id, run_id=run.id,
        ctx=_WorkerAIContext(tenant_a, call.environment_id), executor=forbidden)
    assert result.status == "permanent_failure" and result.error_class == "transcript_changed"


async def test_api_rules_scope_rbac_and_durable_receipt(client, db, tenant_a, owner_a, owner_b, viewer_a):
    call = await prepared(db, tenant_a)
    card = await card_for(db, tenant_a, call)
    headers = {**await auth_headers(client, owner_a), "Idempotency-Key": "qa-config-test-001"}
    body = {"name": "Automatic", "scorecard_id": str(card.id), "percent": 100}
    first = await client.post("/api/qa/sampling/automatic", headers=headers, json=body)
    assert first.status_code == 201, first.text
    second = await client.post("/api/qa/sampling/automatic", headers=headers, json=body)
    assert second.status_code == 201 and first.json()["id"] == second.json()["id"]
    assert (await client.post("/api/qa/sampling/automatic", headers=headers, json={**body, "percent": 50})).status_code == 409
    other = {**await auth_headers(client, owner_b), "Idempotency-Key": "qa-config-other-001"}
    assert (await client.get("/api/qa/sampling/automatic", headers=other)).json()["total"] == 0
    assert (await client.post("/api/qa/sampling/automatic", headers=other, json=body)).status_code == 404
    assert (await client.delete("/api/qa/sampling/automatic/" + first.json()["id"], headers=other)).status_code == 404
    viewer = {**await auth_headers(client, viewer_a), "Idempotency-Key": "qa-config-viewer-001"}
    assert (await client.post("/api/qa/sampling/automatic", headers=viewer, json=body)).status_code == 403
    assert (await client.delete("/api/qa/sampling/automatic/" + first.json()["id"], headers=headers)).json()["enabled"] is False
    assert (await client.get("/api/qa/sampling/automatic", headers=headers)).json()["total"] == 1


@pytest.mark.parametrize("boundary", ["tenant", "environment", "version"])
async def test_worker_rejects_forged_scope_before_inference(db, tenant_a, sessionmaker_, boundary):
    _, _, _, run = await review_run(db, tenant_a)
    job = await db.get(DurableJob, run.job_id)
    payload = dict(job.payload)
    if boundary == "version":
        payload["scorecard_version"] = True
    else:
        payload[f"target_{boundary}_id"] = str(uuid.uuid4())
    job.payload = payload
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(job, "payload")  # JSON bool True compares equal to int 1; force the actual malformed persisted value.
    await db.commit()
    async def forbidden(*args):
        raise AssertionError("Invalid scope must not reach a provider")
    worker = JobWorker(sessionmaker_, handlers={JobType.QA_AUTO_REVIEW: auto_review.make_auto_review_handler(sessionmaker_, executor=forbidden)}, job_types=(JobType.QA_AUTO_REVIEW,))
    handled = await worker.run_once()
    expected = {"tenant": "tenant_mismatch", "environment": "environment_mismatch", "version": "qa_scope_invalid"}[boundary]
    assert handled.status == "dead_letter" and handled.last_error_category == expected
    assert not list((await db.scalars(select(UsageEvent))).all())


async def test_two_rules_share_one_durable_review_and_job(db, tenant_a, sessionmaker_):
    call = await prepared(db, tenant_a)
    card = await card_for(db, tenant_a, call)
    await automatic(db, tenant_a, call, card)
    await automatic(db, tenant_a, call, card)
    assert (await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **scope(call, tenant_a))).success
    assert len(list((await db.scalars(select(SampleSelection))).all())) == 2
    assert len(list((await db.scalars(select(QAReview))).all())) == 1
    assert len(list((await db.scalars(select(AutoReviewRun))).all())) == 1
    assert len(list((await db.scalars(select(DurableJob).where(DurableJob.job_type == JobType.QA_AUTO_REVIEW))).all())) == 1


async def test_run_status_uses_scoped_job_ledger_not_run_counter(client, db, tenant_a, owner_a, owner_b):
    _, _, review, run = await review_run(db, tenant_a)
    headers = await auth_headers(client, owner_a)
    url = f"/api/qa/reviews/{review.id}/auto-review"
    assert (await client.get(url, headers=await auth_headers(client, owner_b))).status_code == 404
    first = await client.get(url, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["state"] == "queued" and first.json()["job"]["id"] == str(run.job_id)
    job = await db.get(DurableJob, run.job_id)
    job.status = "cancelled"
    await db.commit()
    assert (await client.get(url, headers=headers)).json()["state"] == "cancelled"
    await db.delete(job)
    await db.commit()
    final = (await client.get(url, headers=headers)).json()
    assert final["state"] == "inconsistent" and final["job"] is None


@pytest.mark.live
async def test_live_qa_provider_roundtrip(db, tenant_a, sessionmaker_, monkeypatch):
    import os
    if os.getenv("VOXDESK_LIVE_QA") != "1" or not os.getenv("QA_LIVE_OPENAI_API_KEY"):
        pytest.skip("Requires VOXDESK_LIVE_QA=1 and QA_LIVE_OPENAI_API_KEY; invokes a paid external model")
    monkeypatch.setattr(settings, "openai_api_key", os.environ["QA_LIVE_OPENAI_API_KEY"])
    _, _, review, run = await review_run(db, tenant_a)
    worker = JobWorker(sessionmaker_, handlers={JobType.QA_AUTO_REVIEW: auto_review.make_auto_review_handler(sessionmaker_)},
                       job_types=(JobType.QA_AUTO_REVIEW,))
    handled = await worker.run_once()
    assert handled.status == "succeeded", handled.last_error_category
    await db.refresh(run)
    await db.refresh(review)
    assert run.status == "completed" and run.suggestion and run.token_count is not None
    assert review.overall_score is None and review.status == "created"


@pytest.mark.parametrize("value", ["21095035-5457-4833-8344-92314695e223", "12345678-1234-4234-8234-123456789012"])
async def test_qa_uuid_numeric_shape_roundtrip(db, tenant_a, value):
    """SQLite UUID DDL has numeric affinity; portable Uuid must preserve hex text."""
    _, card, review, run = await review_run(db, tenant_a)
    probe = AutoReviewRun(id=uuid.UUID(value), tenant_id=tenant_a.id, review_id=review.id,
        scorecard_id=card.id, scorecard_version=card.version, idempotency_key="uuid-storage:" + value,
        job_id=uuid.UUID(value), status="queued", rubric_snapshot=dict(run.rubric_snapshot))
    db.add(probe)
    await db.commit()
    await db.refresh(probe)
    assert probe.id == uuid.UUID(value) and probe.job_id == uuid.UUID(value)
