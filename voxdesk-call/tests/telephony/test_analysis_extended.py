"""Legacy analysis URLs now exercise real scoped data and canonical admission."""
import uuid

import pytest
from sqlalchemy import select, func

from app.core.config import settings
from app.db.enterprise_models import AnalysisSchema, AnalysisResult, BackfillJob
from app.db.models import DurableJob, RequestIdempotencyReceipt
from app.jobs.types import JobType
from app.jobs.worker import JobWorker
from app.services import post_call_analysis_service as analysis
from tests.conftest import auth_headers
from tests.telephony.test_post_call_pipeline import prepared
from tests.webhooks.conftest import webhook_redis_url  # noqa: F401


@pytest.fixture(autouse=True)
def configuration(monkeypatch, request):
    monkeypatch.setattr(settings, "redis_url", request.getfixturevalue("webhook_redis_url"))


async def seed(client, db, tenant, owner):
    call = await prepared(db, tenant)
    headers = await auth_headers(client, owner)
    response = await client.post("/api/analysis/schemas", headers=headers, json={"name": "Intent",
        "fields": [{"name": "requested", "type": "boolean", "required": True}]})
    assert response.status_code == 201, response.text
    return call, response.json(), headers


@pytest.mark.parametrize("route", ["health", "stats", "config", "metrics", "audit", "list"])
async def test_read_paths_query_only_authenticated_scope(client, db, tenant_a, owner_a, owner_b, route):
    call, schema, headers = await seed(client, db, tenant_a, owner_a)
    own = await client.get(f"/api/analysis/extended/{route}", headers=headers)
    foreign = await client.get(f"/api/analysis/extended/{route}", headers=await auth_headers(client, owner_b))
    assert own.status_code == foreign.status_code == 200
    a, b = own.json(), foreign.json()
    assert a.get("environment_id", str(call.environment_id)) == str(call.environment_id)
    assert str(schema["id"]) not in foreign.text
    if route == "health":
        assert a["schemas"] == 1 and b["schemas"] == 0
        assert a["database"] == "reachable" and a["provider_connectivity"] == "not_probed"
        assert "healthy" not in own.text
    elif route == "stats":
        assert a["counts"]["schemas"] == 1 and b["counts"]["schemas"] == 0
        assert a["counts"]["results"] == 0 and a["result_provenance"] == []
    elif route == "config":
        assert a["backfill_max_calls"] == analysis.MAX_BACKFILL_CALLS
        assert a["definition_contract"]["additionalProperties"] is False
        assert a["environment_id"] != b["environment_id"]
    elif route == "audit":
        assert a["total"] == 1 and b["total"] == 0
        assert a["logs"][0]["event_type"] == "analysis.schema_created"
        assert set(a["logs"][0]) == {"id", "created_at", "event_type", "resource_type", "resource_id", "result"}
    elif route == "list":
        assert a["total"] == 1 and b["total"] == 0
        assert a["items"][0]["id"] == schema["id"]
    else:
        assert a["jobs"] == b["jobs"] == [] and a["steps"] == b["steps"] == []


@pytest.mark.parametrize("fields", [[], [{"name": "x", "type": "enum", "enum_values": ["a", "a"]}],
    [{"name": "x", "type": "boolean", "examples": ["not a boolean"]}],
    [{"name": "x", "type": "text"}, {"name": "x", "type": "text"}]])
async def test_validate_compiles_real_schema_and_does_not_write(client, db, owner_a, owner_b, fields):
    for user in (owner_a, owner_b):
        response = await client.post("/api/analysis/extended/validate", headers=await auth_headers(client, user),
            json={"name": "Bad", "fields": fields})
        assert response.status_code == 200 and response.json()["valid"] is False
        assert response.json()["errors"]
    assert await db.scalar(select(func.count()).select_from(AnalysisSchema)) == 0


async def test_validate_returns_same_compiler_output(client, db, owner_a):
    response = await client.post("/api/analysis/extended/validate", headers=await auth_headers(client, owner_a),
        json={"name": "Good", "fields": [{"name": "requested", "type": "boolean", "required": True}]})
    assert response.json()["valid"] is True
    assert response.json()["compiled_schema"] == analysis.compile_fields([{"name": "requested", "type": "boolean", "required": True}])
    assert await db.scalar(select(func.count()).select_from(AnalysisSchema)) == 0


async def test_bulk_alias_shares_receipt_jobs_and_real_worker(client, db, tenant_a, owner_a, owner_b, sessionmaker_):
    call, schema, headers = await seed(client, db, tenant_a, owner_a)
    body = {"schema_id": schema["id"], "call_ids": [str(call.id)], "idempotency_key": "extended-bulk-001"}
    first = await client.post("/api/analysis/extended/bulk", headers=headers, json=body)
    assert first.status_code == 200, first.text
    repeat = await client.post("/api/analysis/backfill", headers=headers, json=body)
    assert repeat.status_code == 201 and first.json()["id"] == repeat.json()["id"]
    assert first.json()["status"] == "queued" and len(first.json()["job_ids"]) == 1
    assert (await client.post("/api/analysis/extended/bulk", headers=await auth_headers(client, owner_b), json=body)).status_code == 404
    batch = await db.get(BackfillJob, uuid.UUID(first.json()["id"]))
    batch.status, batch.processed_calls = "completed", 999  # Legacy counters are not execution evidence.
    await db.commit()
    metrics = (await client.get("/api/analysis/extended/metrics", headers=headers)).json()
    assert metrics["jobs"] == [{"job_type": JobType.ANALYSIS_BACKFILL, "status": "queued", "count": 1}]
    assert (await client.get("/api/analysis/extended/metrics", headers=await auth_headers(client, owner_b))).json()["jobs"] == []
    async def executor(*args):
        return {"text": '{"requested":true}', "tokens": 7}
    async def handler(job):
        return await analysis.execute_backfill(job, sessionmaker_, executor=executor)
    worker = JobWorker(sessionmaker_, handlers={JobType.ANALYSIS_BACKFILL: handler}, job_types=(JobType.ANALYSIS_BACKFILL,))
    assert (await worker.run_once()).status == "succeeded"
    metrics = (await client.get("/api/analysis/extended/metrics", headers=headers)).json()
    assert metrics["jobs"] == [{"job_type": JobType.ANALYSIS_BACKFILL, "status": "succeeded", "count": 1}]
    assert metrics["steps"] == [{"status": "completed", "count": 1}]
    stats = (await client.get("/api/analysis/extended/stats", headers=headers)).json()
    assert stats["counts"] == {"schemas": 1, "results": 1, "backfill_manifests": 1}
    assert stats["result_provenance"] == [{"provenance": "provider", "count": 1}]
    assert (await db.scalars(select(AnalysisResult))).one().result == {"requested": True}


async def test_bulk_is_atomic_and_redis_fail_closed(client, db, tenant_a, owner_a, monkeypatch):
    from app.api import post_call_analysis_routes as routes
    call, schema, headers = await seed(client, db, tenant_a, owner_a)
    body = {"schema_id": schema["id"], "call_ids": [str(call.id)], "idempotency_key": "extended-rollback-001"}
    async def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(routes, "record_event", fail)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await client.post("/api/analysis/extended/bulk", headers=headers, json=body)
    for model in (DurableJob, BackfillJob, RequestIdempotencyReceipt):
        assert await db.scalar(select(func.count()).select_from(model)) == 0
    monkeypatch.setattr(settings, "redis_url", "")
    refused = await client.post("/api/analysis/extended/bulk", headers=headers, json=body)
    assert refused.status_code == 501 and refused.json()["detail"]["code"] == "NOT_CONFIGURED"


async def test_cache_is_unsupported_and_does_not_delete_evidence(client, db, tenant_a, owner_a, owner_b, viewer_a):
    _, _, headers = await seed(client, db, tenant_a, owner_a)
    for auth in (headers, await auth_headers(client, owner_b)):
        response = await client.delete("/api/analysis/extended/cache", headers=auth)
        assert response.status_code == 501 and response.json()["detail"]["code"] == "UNSUPPORTED_CAPABILITY"
    assert await db.scalar(select(func.count()).select_from(AnalysisSchema)) == 1
    viewer = await auth_headers(client, viewer_a)
    assert (await client.post("/api/analysis/extended/bulk", headers=viewer, json={})).status_code == 403
    assert (await client.delete("/api/analysis/extended/cache", headers=viewer)).status_code == 403
    assert (await client.get("/api/analysis/extended/audit", headers=viewer)).status_code == 403


async def test_pagination_is_bounded_and_audit_page_is_real(client, db, tenant_a, owner_a):
    _, _, headers = await seed(client, db, tenant_a, owner_a)
    for path in ("list", "audit"):
        response = await client.get(f"/api/analysis/extended/{path}?limit=1&offset=1", headers=headers)
        assert response.status_code == 200 and response.json()["total"] == 1
        assert response.json()["items" if path == "list" else "logs"] == []
        assert (await client.get(f"/api/analysis/extended/{path}?limit=201", headers=headers)).status_code == 422


async def test_other_environment_is_excluded_and_revocation_is_enforced(client, db, tenant_a, owner_a):
    from app.db.models import Environment, EnvironmentMembership, UserRole
    from app.audit.service import record_event
    call, _, headers = await seed(client, db, tenant_a, owner_a)
    dev = Environment(tenant_id=tenant_a.id, name="Development", slug="development", kind="development")
    db.add(dev)
    await db.flush()
    await analysis.create_schema(db, tenant_id=tenant_a.id, environment_id=dev.id, name="Private development",
        description="", fields=[{"name": "x", "type": "text"}])
    await record_event(db, tenant_id=tenant_a.id, environment_id=dev.id, event_type="analysis.schema_created",
        actor_user_id=owner_a.id, resource_type="analysis_schema", resource_id=uuid.uuid4())
    await db.commit()
    for route in ("list", "audit"):
        response = await client.get(f"/api/analysis/extended/{route}", headers=headers)
        assert response.json()["total"] == 1 and "Private development" not in response.text
    assert (await client.get("/api/analysis/extended/stats", headers=headers)).json()["counts"]["schemas"] == 1
    db.add(EnvironmentMembership(environment_id=call.environment_id, user_id=owner_a.id, role=UserRole.OWNER, status="revoked"))
    await db.commit()
    for route in ("health", "stats", "config", "metrics", "audit", "list"):
        assert (await client.get(f"/api/analysis/extended/{route}", headers=headers)).status_code == 404
    assert (await client.post("/api/analysis/extended/validate", headers=headers, json={})).status_code == 404
    assert (await client.delete("/api/analysis/extended/cache", headers=headers)).status_code == 404


async def test_failed_database_query_never_returns_zero_success(client, db, tenant_a, owner_a, monkeypatch):
    from sqlalchemy.exc import OperationalError
    _, _, headers = await seed(client, db, tenant_a, owner_a)
    async def unavailable(*args, **kwargs):
        raise OperationalError("query", {}, RuntimeError("database unavailable"))
    monkeypatch.setattr(analysis, "observed_stats", unavailable)
    response = await client.get("/api/analysis/extended/stats", headers=headers)
    assert response.status_code == 503, response.text
    assert "counts" not in response.json() and "database unavailable" not in response.text

    from sqlalchemy.ext.asyncio import AsyncSession
    original_scalar = AsyncSession.scalar
    async def failed_health_count(self, statement, *args, **kwargs):
        if "count(analysis_schemas.id)" in str(statement):
            raise OperationalError("query", {}, RuntimeError("database unavailable"))
        return await original_scalar(self, statement, *args, **kwargs)
    monkeypatch.setattr(AsyncSession, "scalar", failed_health_count)
    health = await client.get("/api/analysis/extended/health", headers=headers)
    assert health.status_code == 503 and "reachable" not in health.text
