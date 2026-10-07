"""Real POST_CALL -> pinned workflow job -> transactional existing interpreter."""
import uuid

import pytest
from sqlalchemy import select, func

from app.builder.workflow_repository import WorkflowRepository
from app.core.config import settings
from app.db.models import DurableJob, WorkflowExecution, WorkflowVersion, LeadStatus, Environment, AuditLog
from app.db.enterprise_models import WorkflowTrigger
from app.services import post_call_workflow as runtime, workflow_service
from app.telephony.post_call import PostCallPipeline
from app.jobs.worker import JobWorker, build_handlers
from app.jobs.types import JobType
from tests.conftest import auth_headers, make_lead
from tests.telephony.test_post_call_pipeline import prepared, completed_executor, scope, step_rows
from tests.webhooks.conftest import webhook_redis_url  # noqa: F401


@pytest.fixture(autouse=True)
def configured(monkeypatch, request):
    monkeypatch.setattr(settings, "redis_url", request.getfixturevalue("webhook_redis_url"))


async def setup(client, db, tenant, owner, action="update_lead_status"):
    lead = await make_lead(db, tenant, status=LeadStatus.CALLED)
    call = await prepared(db, tenant, lead_id=lead.id)
    graph = {"id": "flow-" + uuid.uuid4().hex, "tenant_id": str(tenant.id), "name": "Post-call qualification",
             "entry_node": "start", "nodes": [
                 {"id": "start", "type": "trigger", "next": "act"},
                 {"id": "act", "type": "action", "action": {"name": action, "params": {"status": "qualified"}}, "next": "end"},
                 {"id": "end", "type": "terminal"}]}
    repo = WorkflowRepository(db)
    flow = await repo.create_workflow(tenant.id, graph["name"], graph, slug=graph["id"])
    version = await repo.create_version(flow["db_id"], graph, 1, tenant_id=tenant.id)
    await repo.publish_version(tenant.id, flow["db_id"], version["id"])
    await db.commit()
    headers = {**await auth_headers(client, owner), "Idempotency-Key": "workflow-" + uuid.uuid4().hex}
    body = {"workflow_id": str(flow["db_id"]), "event_type": "after_call", "config": {runtime.BINDING: str(call.environment_id)}}
    return call, lead, flow, version, headers, body


async def admit(client, db, tenant, owner, maker):
    call, lead, flow, version, headers, body = await setup(client, db, tenant, owner)
    response = await client.post("/api/workflows/triggers", headers=headers, json=body)
    assert response.status_code == 201, response.text
    assert (await client.post("/api/workflows/triggers", headers=headers, json=body)).json()["id"] == response.json()["id"]
    result = await PostCallPipeline(maker, executor=completed_executor).run(call.id, **scope(call, tenant))
    assert result.success
    jobs = list((await db.scalars(select(DurableJob).where(DurableJob.job_type == JobType.WORKFLOW_EXECUTION))).all())
    assert len(jobs) == 1
    return call, lead, flow, version, headers, body, jobs[0], response.json()


async def worker(maker):
    async def handler(job):
        return await runtime.execute(job, maker)
    return await JobWorker(maker, handlers={JobType.WORKFLOW_EXECUTION: handler}, job_types=(JobType.WORKFLOW_EXECUTION,)).run_once()


async def test_real_effect_pinned_version_and_replay(client, db, tenant_a, owner_a, sessionmaker_):
    call, lead, flow, version, headers, body, job, trigger = await admit(client, db, tenant_a, owner_a, sessionmaker_)
    graph = {**version["graph_config"], "nodes": [{"id": "end", "type": "terminal"}], "entry_node": "end"}
    repo = WorkflowRepository(db)
    newer = await repo.create_version(flow["db_id"], graph, 2, tenant_id=tenant_a.id)
    await repo.publish_version(tenant_a.id, flow["db_id"], newer["id"])
    await db.commit()
    result = await worker(sessionmaker_)
    assert result.status == "succeeded", result.last_error_category
    await db.refresh(lead)
    assert lead.status == LeadStatus.QUALIFIED  # Old version really executed, not today's terminal-only graph.
    execution = (await db.scalars(select(WorkflowExecution))).one()
    assert execution.workflow_version_id == version["id"] and execution.status == "completed"
    assert execution.output_metadata["effects_committed"] is True
    assert execution.attempt_count == 1
    assert await workflow_service.execution_history(str(tenant_a.id), session=db) == []
    again = await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **scope(call, tenant_a))
    assert again.success and await worker(sessionmaker_) is None
    assert await db.scalar(select(func.count()).select_from(WorkflowExecution)) == 1
    assert (await step_rows(db, call))["workflow_admission"].output["job_ids"] == [str(job.id)]
    audits = list((await db.scalars(select(AuditLog).where(AuditLog.event_type == "workflow.post_call_completed"))).all())
    assert len(audits) == 1 and audits[0].environment_id == call.environment_id


@pytest.mark.parametrize("change,category", [("disabled", "workflow_not_configured"), ("version", "workflow_version_changed"),
    ("environment", "workflow_scope_unavailable"), ("redis", "workflow_redis_not_configured"), ("evidence", "workflow_analysis_changed")])
async def test_fail_closed_without_effects(client, db, tenant_a, owner_a, sessionmaker_, monkeypatch, change, category):
    call, lead, flow, version, headers, body, job, trigger = await admit(client, db, tenant_a, owner_a, sessionmaker_)
    if change == "disabled":
        row = await db.get(WorkflowTrigger, uuid.UUID(trigger["id"]))
        row.is_enabled = False
    elif change == "version":
        row = await db.get(WorkflowVersion, version["id"])
        row.graph_config = {**row.graph_config, "description": "changed outside publication"}
    elif change == "environment":
        (await db.get(Environment, call.environment_id)).status = "suspended"
    elif change == "redis":
        monkeypatch.setattr(settings, "redis_url", "")
    else:
        row = (await step_rows(db, call))["summary"]
        row.output = {**row.output, "outcome": "changed"}
    await db.commit()
    handled = await worker(sessionmaker_)
    assert handled.status == "dead_letter" and handled.last_error_category == category
    await db.refresh(lead)
    assert lead.status == LeadStatus.CALLED
    assert await db.scalar(select(func.count()).select_from(WorkflowExecution)) == 0


async def test_audit_failure_rolls_back_effect_and_execution(client, db, tenant_a, owner_a, sessionmaker_, monkeypatch):
    call, lead, *_ = await admit(client, db, tenant_a, owner_a, sessionmaker_)
    async def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(runtime, "record_event", fail)
    result = await worker(sessionmaker_)
    assert result.status != "succeeded"
    await db.refresh(lead)
    assert lead.status == LeadStatus.CALLED
    assert await db.scalar(select(func.count()).select_from(WorkflowExecution)) == 0


async def test_admission_audit_rollback_keeps_analysis(client, db, tenant_a, owner_a, sessionmaker_, monkeypatch):
    call, lead, flow, version, headers, body = await setup(client, db, tenant_a, owner_a)
    assert (await client.post("/api/workflows/triggers", headers=headers, json=body)).status_code == 201
    async def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(runtime, "record_event", fail)
    result = await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **scope(call, tenant_a))
    assert not result.success
    assert await db.scalar(select(func.count()).select_from(DurableJob).where(DurableJob.job_type == JobType.WORKFLOW_EXECUTION)) == 0
    rows = await step_rows(db, call)
    assert rows["analyzed_webhook"].status == "completed" and rows["workflow_admission"].status == "failed"


async def test_trigger_permissions_isolation_and_atomic_audit(client, db, tenant_a, tenant_b, owner_a, owner_b, viewer_a, monkeypatch):
    from app.api import workflow_event_routes as routes
    call, lead, flow, version, headers, body = await setup(client, db, tenant_a, owner_a)
    assert (await client.post("/api/workflows/triggers", headers=await auth_headers(client, owner_b), json=body)).status_code == 404
    assert (await client.post("/api/workflows/triggers", headers=await auth_headers(client, viewer_a), json=body)).status_code == 403
    assert (await client.post("/api/workflows/triggers", headers=await auth_headers(client, owner_a), json=body)).status_code == 422
    wrong = {**body, "config": {runtime.BINDING: str(uuid.uuid4())}}
    assert (await client.post("/api/workflows/triggers", headers=headers, json=wrong)).status_code == 404
    async def fail(*args, **kwargs):
        raise RuntimeError("audit unavailable")
    with monkeypatch.context() as patch:
        patch.setattr(routes, "record_event", fail)
        with pytest.raises(RuntimeError):
            await client.post("/api/workflows/triggers", headers=headers, json=body)
    assert await db.scalar(select(func.count()).select_from(WorkflowTrigger)) == 0
    response = await client.post("/api/workflows/triggers", headers=headers, json=body)
    assert response.status_code == 201
    url = "/api/workflows/triggers/" + response.json()["id"]
    foreign = await auth_headers(client, owner_b)
    assert (await client.get(url, headers=foreign)).status_code == 404
    assert (await client.patch(url, headers=foreign, json={"is_enabled": False})).status_code == 404
    assert (await client.delete(url, headers=foreign)).status_code == 404
    assert (await client.get("/api/workflows/triggers", headers=foreign)).json()["total"] == 0


@pytest.mark.parametrize("action", ["mark_resolved", "add_conversation_tag", "enqueue_notification"])
async def test_unimplemented_actions_are_not_certified(client, db, tenant_a, owner_a, action):
    call, lead, flow, version, headers, body = await setup(client, db, tenant_a, owner_a, action)
    response = await client.post("/api/workflows/triggers", headers=headers, json=body)
    assert response.status_code == 501 and response.json()["detail"]["code"] == "UNSUPPORTED_CAPABILITY"
    assert await db.scalar(select(func.count()).select_from(WorkflowTrigger)) == 0


def test_default_worker_registry():
    assert callable(build_handlers()[JobType.WORKFLOW_EXECUTION])


async def test_execution_read_scope_and_checkpoint_ack_separation(client, db, tenant_a, owner_a, owner_b, sessionmaker_):
    call, lead, flow, version, headers, body, job, trigger = await admit(client, db, tenant_a, owner_a, sessionmaker_)
    url = f"/api/workflows/calls/{call.id}/executions"
    before = await client.get(url, headers=headers)
    assert before.status_code == 200 and before.json()["total"] == 1
    assert before.json()["items"][0]["execution"] is None and before.json()["items"][0]["job_status"] == "queued"
    assert (await client.get(url, headers=await auth_headers(client, owner_b))).status_code == 404
    assert (await worker(sessionmaker_)).status == "succeeded"
    after = (await client.get(url, headers=headers)).json()["items"][0]
    assert after["execution"]["status"] == "completed" and after["job_status"] == "succeeded"
    assert (await client.get(url + "?limit=201", headers=headers)).status_code == 422
    assert (await client.get(url + "?offset=1", headers=headers)).json()["items"] == []


async def test_environment_revocation_and_foreign_lead_fail_closed(client, db, tenant_a, tenant_b, owner_a, sessionmaker_):
    from app.db.models import EnvironmentMembership, UserRole
    call, lead, flow, version, headers, body = await setup(client, db, tenant_a, owner_a)
    other = Environment(tenant_id=tenant_a.id, name="Development", slug="dev", kind="development")
    db.add(other)
    await db.flush()
    other_lead = await make_lead(db, tenant_a, environment_id=other.id, status=LeadStatus.CALLED)
    call.lead_id = other_lead.id
    await db.commit()
    response = await client.post("/api/workflows/triggers", headers=headers, json=body)
    assert response.status_code == 201
    trigger = response.json()
    assert (await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **scope(call, tenant_a))).success
    handled = await worker(sessionmaker_)
    assert handled.status == "dead_letter" and handled.last_error_category == "workflow_lead_not_found"
    await db.refresh(lead)
    assert lead.status == LeadStatus.CALLED
    db.add(EnvironmentMembership(environment_id=call.environment_id, user_id=owner_a.id, role=UserRole.OWNER, status="revoked"))
    await db.commit()
    assert (await client.get("/api/workflows/triggers/" + trigger["id"], headers=headers)).status_code == 404
    assert (await client.get(f"/api/workflows/calls/{call.id}/executions", headers=headers)).status_code == 404


async def test_graph_failure_rolls_back_prior_actions(client, db, tenant_a, owner_a, sessionmaker_):
    call, lead, flow, version, headers, body = await setup(client, db, tenant_a, owner_a)
    graph = version["graph_config"]
    graph["nodes"][1]["next"] = "reject"
    graph["nodes"].insert(2, {"id": "reject", "type": "condition", "condition": {"field": "sentiment", "operator": "eq", "value": "negative"}, "next": "end"})
    (await db.get(WorkflowVersion, version["id"])).graph_config = graph
    await db.commit()
    assert (await client.post("/api/workflows/triggers", headers=headers, json=body)).status_code == 201
    assert (await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **scope(call, tenant_a))).success
    result = await worker(sessionmaker_)
    assert result.status == "dead_letter" and result.last_error_category == "workflow_graph_failed"
    await db.refresh(lead)
    assert lead.status == LeadStatus.CALLED
    execution = (await db.scalars(select(WorkflowExecution))).one()
    assert execution.status == "failed" and execution.output_metadata["effects_committed"] is False


@pytest.mark.parametrize("event,booked,status,expected", [
    ("on_booking", False, "completed", False), ("on_booking", True, "completed", True),
    ("on_completion", False, "completed", True), ("on_failure", False, "completed", False),
    ("on_failure", False, "no_answer", True), ("after_call", False, "failed", True)])
def test_event_matching_is_not_model_outcome(event, booked, status, expected):
    from types import SimpleNamespace
    assert runtime.matches(event, SimpleNamespace(booked=booked, status=status)) is expected


async def test_config_idempotency_conflict_and_disable(client, db, tenant_a, owner_a, sessionmaker_):
    call, lead, flow, version, headers, body, job, trigger = await admit(client, db, tenant_a, owner_a, sessionmaker_)
    assert (await client.post("/api/workflows/triggers", headers=headers, json={**body, "event_type": "on_completion"})).status_code == 409
    url = "/api/workflows/triggers/" + trigger["id"]
    assert (await client.patch(url, headers=headers, json={"is_enabled": False})).status_code == 200
    replay = await client.post("/api/workflows/triggers", headers=headers, json=body)
    assert replay.status_code == 201 and replay.json()["is_enabled"] is False
    assert (await worker(sessionmaker_)).last_error_category == "workflow_not_configured"
    assert (await client.delete(url, headers=headers)).status_code == 200
    assert (await client.get(url, headers=headers)).status_code == 404


async def test_complete_call_has_analysis_qa_workflow_and_signed_webhook(client, db, tenant_a, owner_a, sessionmaker_, monkeypatch):
    import asyncio
    import base64
    import os
    import json
    import httpx
    from app.services import post_call_analysis_service as analysis
    from app.db.enterprise_models import AnalysisResult
    from app.qa.models import AutoReviewRun, QAReview
    from app.outbox.models import OutboxEvent
    from app.outbox.dispatcher import deliver_event
    from app.webhooks.repository import create_subscription
    from app.webhooks.delivery import deliver
    from app.webhooks.signing import verify
    from tests.qa.test_post_call_runtime import card_for, automatic
    monkeypatch.setattr(settings, "identity_encryption_keys", "test:" + base64.urlsafe_b64encode(os.urandom(32)).decode())
    call, lead, flow, version, headers, body = await setup(client, db, tenant_a, owner_a)
    assert (await client.post("/api/workflows/triggers", headers=headers, json=body)).status_code == 201
    await analysis.create_schema(db, tenant_id=tenant_a.id, environment_id=call.environment_id, name="Request",
        description="", fields=[{"name": "requested", "type": "boolean", "required": True}])
    await automatic(db, tenant_a, call, await card_for(db, tenant_a, call))
    secret = "integration-test-signing-secret"
    await create_subscription(db, tenant_id=tenant_a.id, environment_id=call.environment_id,
        endpoint="https://receiver.example.com/hooks", secret=secret, event_types=["call_analyzed"])
    await db.commit()
    async def model(choice, prompt, timeout_ms):
        if '"requested"' in prompt:
            return {"text": '{"requested":true}', "tokens": 7}
        return await completed_executor(choice, prompt, timeout_ms)
    assert (await PostCallPipeline(sessionmaker_, executor=model).run(call.id, **scope(call, tenant_a))).success
    assert (await db.scalars(select(AnalysisResult))).one().result == {"requested": True}
    assert (await db.scalars(select(QAReview))).one().call_id == call.id
    assert (await db.scalars(select(AutoReviewRun))).one().status == "queued"
    rows = await step_rows(db, call)
    assert rows["summary"].output == {"summary": "Caller requested Tuesday.", "outcome": "booking_requested"}
    assert rows["sentiment"].output == {"sentiment": "positive"}
    assert (await worker(sessionmaker_)).status == "succeeded"
    await db.refresh(lead)
    assert lead.status == LeadStatus.QUALIFIED
    received = []
    async def receive(reader, writer):
        head = await reader.readuntil(b"\r\n\r\n")
        wire_headers = {}
        for line in head.decode().split("\r\n")[1:]:
            if ":" in line:
                key, value = line.split(":", 1)
                wire_headers[key.lower()] = value.strip()
        raw = await reader.readexactly(int(wire_headers["content-length"]))
        received.append((wire_headers, raw))
        writer.write(b"HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    server = await asyncio.start_server(receive, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    class Relay(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            async with httpx.AsyncHTTPTransport() as transport:
                response = await transport.handle_async_request(httpx.Request(request.method,
                    f"http://127.0.0.1:{port}/hooks", headers=request.headers, content=await request.aread()))
                await response.aread()
                return response
    try:
        event = (await db.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "call_analyzed"))).one()
        async with httpx.AsyncClient(transport=Relay()) as http:
            async def send(**kwargs):
                return await deliver(**kwargs, client=http)
            assert await deliver_event(db, event, transport=send) == "delivered"
            await db.commit()
        assert len(received) == 1
        wire_headers, raw = received[0]
        assert verify(secret, raw, wire_headers["x-voxdesk-signature"])
        assert json.loads(raw)["payload"]["call_id"] == str(call.id)
        assert len(json.loads(raw)["payload"]["analysis_result_ids"]) == 1
    finally:
        server.close()
        await server.wait_closed()


async def test_arbitrary_job_cannot_bypass_admission(client, db, tenant_a, owner_a, sessionmaker_):
    call, lead, flow, version, headers, body, job, trigger = await admit(client, db, tenant_a, owner_a, sessionmaker_)
    job.idempotency_key = "forged-new-execution-identity"
    await db.commit()
    result = await worker(sessionmaker_)
    assert result.status == "dead_letter" and result.last_error_category == "workflow_admission_missing"
    await db.refresh(lead)
    assert lead.status == LeadStatus.CALLED
