"""Backfill API admission, canonical worker execution, isolation and receipts."""
import json
import socket
import uuid

import httpx
import pytest
from sqlalchemy import select

from app.ai import circuit_breaker, post_call_llm
from app.core.config import settings
from app.db.enterprise_models import AnalysisResult, BackfillJob
from app.db.models import DurableJob, RequestIdempotencyReceipt, AuditLog, CallStatus
from app.jobs.types import JobType
from app.jobs.worker import JobWorker, build_handlers
from app.services import post_call_analysis_service as analysis
from tests.conftest import auth_headers, make_call
from tests.telephony.test_post_call_pipeline import prepared
from tests.webhooks.conftest import webhook_redis_url  # noqa: F401


@pytest.fixture(autouse=True)
def configured(monkeypatch, request):
    circuit_breaker.reset()
    monkeypatch.setattr(settings, "redis_url", request.getfixturevalue("webhook_redis_url"))
    for name in ("openai_api_key", "google_api_key", "anthropic_api_key"):
        monkeypatch.setattr(settings, name, "")
    yield
    circuit_breaker.reset()


async def definition(db, tenant, call):
    row = await analysis.create_schema(db, tenant_id=tenant.id, environment_id=call.environment_id,
        name="Intent", description="", fields=[{"name": "requested", "type": "boolean", "required": True}])
    await db.commit()
    return row


async def admit(client, user, schema, calls, key="backfill-unique-001"):
    return await client.post("/api/analysis/backfill", headers=await auth_headers(client, user), json={
        "schema_id": str(schema.id), "call_ids": [str(call.id) for call in calls], "idempotency_key": key})


async def test_real_http_worker_version_pinning_and_idempotent_admission(db, tenant_a, owner_a, client, sessionmaker_, monkeypatch):
    call = await prepared(db, tenant_a)
    schema = await definition(db, tenant_a, call)
    first = await admit(client, owner_a, schema, [call])
    assert first.status_code == 201, first.text
    original = first.json()
    assert original["status"] == "queued" and original["processed_calls"] == 0
    duplicate = await admit(client, owner_a, schema, [call])
    assert duplicate.status_code == 201 and duplicate.json()["id"] == original["id"]
    assert len(list((await db.scalars(select(DurableJob))).all())) == 1
    await analysis.update_schema(db, schema, {"fields": [{"name": "new_name", "type": "text"}]})
    await db.commit()
    requests = []
    monkeypatch.setattr(settings, "openai_api_key", "test-backfill-key")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])

    async def receive(request):
        assert request.method == "POST" and request.url.host == "api.openai.com"
        assert request.headers["authorization"] == "Bearer test-backfill-key"
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"]
        assert '"requested"' in prompt and '"new_name"' not in prompt and "Please book Tuesday" in prompt
        requests.append(body)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"requested":true}'}}], "usage": {"total_tokens": 12}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as provider:
        adapter = post_call_llm.StructuredExecutor(analysis.compile_fields([{"name": "requested", "type": "boolean"}]), client=provider)
        async def handler(job):
            return await analysis.execute_backfill(job, sessionmaker_, executor=adapter)
        worker = JobWorker(sessionmaker_, handlers={JobType.ANALYSIS_BACKFILL: handler}, job_types=(JobType.ANALYSIS_BACKFILL,))
        handled = await worker.run_once()
        assert handled.status == "succeeded"
        assert await worker.run_once() is None
    assert len(requests) == 1
    result = (await db.scalars(select(AnalysisResult))).one()
    assert result.schema_version == 1 and result.provenance == "provider" and result.result == {"requested": True}
    status = await client.get("/api/analysis/backfill/" + original["id"], headers=await auth_headers(client, owner_a))
    assert status.json()["status"] == "completed" and status.json()["processed_calls"] == 1
    assert status.json()["completed_at"]


async def test_foreign_and_nonterminal_calls_rejected_without_admission(db, tenant_a, tenant_b, owner_a, owner_b, client):
    call = await prepared(db, tenant_a)
    schema = await definition(db, tenant_a, call)
    foreign = await prepared(db, tenant_b)
    ongoing = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    for user, calls in [(owner_b, [call]), (owner_a, [foreign]), (owner_a, [ongoing])]:
        response = await admit(client, user, schema, calls)
        assert response.status_code == 404, response.text
    assert not list((await db.scalars(select(BackfillJob))).all())
    assert not list((await db.scalars(select(DurableJob))).all())


async def test_request_mismatch_conflicts_and_other_tenant_cannot_read(db, tenant_a, owner_a, owner_b, client):
    call = await prepared(db, tenant_a)
    second = await prepared(db, tenant_a)
    schema = await definition(db, tenant_a, call)
    first = await admit(client, owner_a, schema, [call])
    assert first.status_code == 201
    assert (await admit(client, owner_a, schema, [second])).status_code == 409
    response = await client.get("/api/analysis/backfill/" + first.json()["id"], headers=await auth_headers(client, owner_b))
    assert response.status_code == 404


async def test_missing_provider_default_registered_worker_is_not_success(db, tenant_a, owner_a, client, sessionmaker_, monkeypatch):
    import app.db.session as sessions
    monkeypatch.setattr(sessions, "get_sessionmaker", lambda: sessionmaker_)
    assert callable(build_handlers()[JobType.ANALYSIS_BACKFILL])
    call = await prepared(db, tenant_a)
    schema = await definition(db, tenant_a, call)
    response = await admit(client, owner_a, schema, [call])
    assert response.status_code == 201
    job = await JobWorker(sessionmaker_, job_types=(JobType.ANALYSIS_BACKFILL,)).run_once()
    assert job.status == "dead_letter" and job.last_error_category == "provider_not_configured"
    assert not list((await db.scalars(select(AnalysisResult))).all())
    batch = await db.get(BackfillJob, uuid.UUID(response.json()["id"]))
    progress = await analysis.backfill_status(db, batch)
    assert progress["status"] == "failed" and progress["failed_calls"] == 1 and progress["processed_calls"] == 0


async def test_audit_failure_rolls_back_manifest_jobs_and_receipt(db, tenant_a, owner_a, client, monkeypatch):
    import app.api.post_call_analysis_routes as routes
    call = await prepared(db, tenant_a)
    schema = await definition(db, tenant_a, call)
    async def failure(*args, **kwargs):
        raise RuntimeError("audit storage unavailable")
    monkeypatch.setattr(routes, "record_event", failure)
    with pytest.raises(RuntimeError, match="audit storage unavailable"):
        await admit(client, owner_a, schema, [call])
    await db.rollback()
    for model in (BackfillJob, DurableJob, RequestIdempotencyReceipt):
        assert not list((await db.scalars(select(model))).all())
    assert not list((await db.scalars(select(AuditLog).where(AuditLog.event_type == "analysis.backfill_admitted"))).all())


async def test_redis_missing_rejects_api_and_duplicate_selection(db, tenant_a, owner_a, client, monkeypatch):
    call = await prepared(db, tenant_a)
    schema = await definition(db, tenant_a, call)
    assert (await admit(client, owner_a, schema, [call, call])).status_code == 422
    monkeypatch.setattr(settings, "redis_url", "")
    response = await admit(client, owner_a, schema, [call])
    assert response.status_code == 501 and response.json()["detail"]["code"] == "NOT_CONFIGURED"
    assert not list((await db.scalars(select(BackfillJob))).all())


async def test_legacy_rows_not_certified(db, tenant_a):
    call = await prepared(db, tenant_a)
    schema = await definition(db, tenant_a, call)
    row = BackfillJob(tenant_id=tenant_a.id, schema_id=schema.id, environment_id=call.environment_id,
                      status="completed", total_calls=8, processed_calls=8)
    db.add(row)
    await db.commit()
    status = await analysis.backfill_status(db, row)
    assert status["status"] == "legacy_unverified" and status["processed_calls"] == 0
    assert row.processed_calls == 8  # Historical record preserved, not recertified.


async def test_redis_budget_exhaustion_retries_without_provider(db, tenant_a, owner_a, client, sessionmaker_):
    from app.core.rate_limit import rate_limit
    call = await prepared(db, tenant_a)
    schema = await definition(db, tenant_a, call)
    assert (await admit(client, owner_a, schema, [call])).status_code == 201
    key = f"analysis:backfill:{tenant_a.id}:{call.environment_id}"
    for _ in range(analysis.BACKFILL_PER_MINUTE):
        assert await rate_limit(key, analysis.BACKFILL_PER_MINUTE, 60)
    async def forbidden(*args):
        raise AssertionError("Rate-limited work cannot reach a provider")
    async def handler(job):
        return await analysis.execute_backfill(job, sessionmaker_, executor=forbidden)
    worker = JobWorker(sessionmaker_, handlers={JobType.ANALYSIS_BACKFILL: handler}, job_types=(JobType.ANALYSIS_BACKFILL,))
    job = await worker.run_once()
    assert job.status == "retry_scheduled" and job.last_error_category == "analysis_rate_limited"
    assert not list((await db.scalars(select(AnalysisResult))).all())


async def test_preview_export_are_scoped_and_do_not_admit_jobs(db, tenant_a, owner_a, owner_b, client):
    call = await prepared(db, tenant_a)
    schema = await definition(db, tenant_a, call)
    payload = {"schema_id": str(schema.id), "call_ids": [str(call.id)]}
    headers = await auth_headers(client, owner_a)
    response = await client.post("/api/analysis/backfill/preview", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value["selected_calls"] == 1 and value["estimated_tokens"] > 2048
    assert value["reservation_created"] is False
    if not value["cost"]["known"]:
        assert value["cost"]["provider_cost_usd"] is None
    assert not list((await db.scalars(select(DurableJob))).all())
    assert (await client.post("/api/analysis/backfill/preview", headers=await auth_headers(client, owner_b), json=payload)).status_code == 404
    exported = await client.get("/api/analysis/results/export", headers=headers)
    assert exported.status_code == 200 and exported.json()["total"] == 0
    assert "attachment" in exported.headers["content-disposition"]


async def test_selection_dates_empty_and_oversize_are_not_silently_accepted(db, tenant_a, owner_a, client):
    call = await prepared(db, tenant_a)
    schema = await definition(db, tenant_a, call)
    headers = await auth_headers(client, owner_a)
    for extra in ({"call_ids": []}, {"start_date": "2026-01-01T00:00:00"},
                  {"start_date": "2030-01-01T00:00:00Z", "end_date": "2020-01-01T00:00:00Z"},
                  {"call_ids": [str(uuid.uuid4()) for _ in range(201)]}):
        response = await client.post("/api/analysis/backfill/preview", headers=headers,
                                     json={"schema_id": str(schema.id), **extra})
        assert response.status_code == 422, response.text
    assert not list((await db.scalars(select(DurableJob))).all())


async def test_cancellation_and_missing_ledger_rows_are_honest(db, tenant_a, owner_a, client):
    from app.jobs.queue import cancel
    call = await prepared(db, tenant_a)
    schema = await definition(db, tenant_a, call)
    response = await admit(client, owner_a, schema, [call])
    batch = await db.get(BackfillJob, uuid.UUID(response.json()["id"]))
    job_id = uuid.UUID(batch.job_ids[0])
    assert await cancel(db, tenant_id=tenant_a.id, job_id=job_id) == "cancelled"
    await db.commit()
    status = await analysis.backfill_status(db, batch)
    assert status["status"] == "cancelled" and status["cancelled_calls"] == 1 and status["processed_calls"] == 0
    job = await db.get(DurableJob, job_id)
    await db.delete(job)
    await db.commit()
    status = await analysis.backfill_status(db, batch)
    assert status["status"] == "inconsistent" and status["processed_calls"] == 0


async def test_viewer_cannot_admit_backfill(db, tenant_a, viewer_a, client):
    call = await prepared(db, tenant_a)
    schema = await definition(db, tenant_a, call)
    response = await admit(client, viewer_a, schema, [call])
    assert response.status_code == 403
    assert not list((await db.scalars(select(BackfillJob))).all())


async def test_date_selection_normalizes_offsets_and_export_hides_foreign_scope(db, tenant_a, owner_a, owner_b, client):
    from datetime import datetime, timezone
    call = await prepared(db, tenant_a)
    call.started_at = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)
    schema = await definition(db, tenant_a, call)
    response = await client.post("/api/analysis/backfill/preview", headers=await auth_headers(client, owner_a), json={
        "schema_id": str(schema.id), "start_date": "2026-10-07T15:30:00+06:00", "end_date": "2026-10-07T16:30:00+06:00"})
    assert response.status_code == 200 and response.json()["selected_calls"] == 1
    other = await auth_headers(client, owner_b)
    for query in (f"schema_id={schema.id}", f"call_id={call.id}"):
        response = await client.get("/api/analysis/results/export?" + query, headers=other)
        assert response.status_code == 404
