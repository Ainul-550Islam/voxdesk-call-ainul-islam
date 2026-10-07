"""Real post-call -> existing CRM queue -> acknowledged provider note contracts."""
import json
import os
import uuid

import pytest
from sqlalchemy import select, func

from app.core.config import settings
from app.db.models import CrmEvent, CrmSync, CrmSyncStatus
from app.db.telephony_models import PostCallStepRun
from app.integrations.crm import service
from app.telephony.post_call import PostCallPipeline
from tests.conftest import FakeTransport, make_integration
from tests.telephony.test_post_call_pipeline import prepared, completed_executor, scope
from tests.webhooks.conftest import webhook_redis_url  # noqa: F401


@pytest.fixture(autouse=True)
def distributed(monkeypatch, request):
    monkeypatch.setattr(settings, "redis_url", request.getfixturevalue("webhook_redis_url"))


async def admitted(db, tenant, maker, *, bound=True):
    call = await prepared(db, tenant)
    integration = await make_integration(db, tenant, config={"url": "https://hooks.example.com/voxdesk",
        "post_call_analysis_environment_id": str(call.environment_id) if bound else str(uuid.uuid4())})
    pipeline = PostCallPipeline(maker, executor=completed_executor)
    assert (await pipeline.run(call.id, **scope(call, tenant))).success
    assert (await pipeline.run(call.id, **scope(call, tenant))).success
    return call, integration


async def test_pipeline_admits_once_and_actual_provider_note_is_required(db, tenant_a, sessionmaker_, monkeypatch):
    call, _ = await admitted(db, tenant_a, sessionmaker_)
    event = (await db.scalars(select(CrmEvent))).one()
    sync = (await db.scalars(select(CrmSync))).one()
    assert event.payload["analysis"]["summary"]["summary"] == "Caller requested Tuesday."
    assert sync.status == CrmSyncStatus.PENDING and sync.external_id is None
    transport = FakeTransport((200, {"id": "contact-real-response"}), (200, {"id": "note-real-response"})).install(monkeypatch)
    outcome = await service.process_sync(db, sync)
    assert outcome.status == CrmSyncStatus.SYNCED and outcome.external_id == "note-real-response"
    assert [r["json"]["type"] for r in transport.requests] == ["contact.upserted", "note.created"]
    note = transport.requests[1]
    assert note["method"] == "POST" and note["url"] == "https://hooks.example.com/voxdesk"
    assert str(call.id) in note["json"]["data"]["title"]
    assert json.loads(note["json"]["data"]["body"])["sentiment"] == {"sentiment": "positive"}
    assert "X-VoxDesk-Signature" in note["headers"]
    await service.process_sync(db, sync)
    assert len(transport.requests) == 2


async def test_note_failure_is_not_hidden_by_successful_contact(db, tenant_a, sessionmaker_, monkeypatch):
    await admitted(db, tenant_a, sessionmaker_)
    sync = (await db.scalars(select(CrmSync))).one()
    FakeTransport((200, {"id": "contact"}), (503, {"error": "offline"})).install(monkeypatch)
    outcome = await service.process_sync(db, sync)
    assert outcome.status == CrmSyncStatus.FAILED and sync.external_id is None
    assert sync.next_attempt_at is not None


async def test_wrong_environment_and_foreign_tenant_never_admit(db, tenant_a, tenant_b, sessionmaker_):
    call, _ = await admitted(db, tenant_a, sessionmaker_, bound=False)
    await make_integration(db, tenant_b, config={"url": "https://hooks.example.com/voxdesk",
        "post_call_analysis_environment_id": str(call.environment_id)})
    assert await db.scalar(select(func.count()).select_from(CrmSync)) == 0
    assert await db.scalar(select(func.count()).select_from(CrmEvent)) == 0


@pytest.mark.parametrize("reason", ["scope", "redis"])
async def test_dispatch_rechecks_scope_and_configuration(db, tenant_a, sessionmaker_, monkeypatch, reason):
    _, integration = await admitted(db, tenant_a, sessionmaker_)
    if reason == "scope":
        integration.config = {**integration.config, "post_call_analysis_environment_id": str(uuid.uuid4())}
        await db.commit()
    else:
        monkeypatch.setattr(settings, "redis_url", "")
    transport = FakeTransport((200, {"id": "must-not-be-used"})).install(monkeypatch)
    sync = (await db.scalars(select(CrmSync))).one()
    outcome = await service.process_sync(db, sync)
    assert outcome.status == CrmSyncStatus.PERMANENT_FAILURE
    assert outcome.error_code == ("scope_changed" if reason == "scope" else "not_configured")
    assert not transport.requests


async def test_admission_audit_failure_rolls_back_event_and_sync(db, tenant_a, sessionmaker_, monkeypatch):
    from app.audit import service as audit
    call = await prepared(db, tenant_a)
    await make_integration(db, tenant_a, config={"url": "https://hooks.example.com/voxdesk",
        "post_call_analysis_environment_id": str(call.environment_id)})
    original = audit.record_event
    async def fail(*args, **kwargs):
        if kwargs.get("event_type") == "crm.analysis_admitted":
            raise RuntimeError("audit unavailable")
        return await original(*args, **kwargs)
    monkeypatch.setattr(audit, "record_event", fail)
    result = await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **scope(call, tenant_a))
    assert not result.success
    assert await db.scalar(select(func.count()).select_from(CrmEvent)) == 0
    assert await db.scalar(select(func.count()).select_from(CrmSync)) == 0
    row = (await db.scalars(select(PostCallStepRun).where(PostCallStepRun.step == "crm_admission"))).one()
    assert row.status == "failed" and row.output == {}


@pytest.mark.live
async def test_live_post_call_crm_note(db, tenant_a, sessionmaker_):
    if os.getenv("VOXDESK_LIVE_CRM_ANALYSIS") != "1" or not os.getenv("CRM_ANALYSIS_WEBHOOK_URL") or not os.getenv("CRM_ANALYSIS_WEBHOOK_SECRET"):
        pytest.skip("Requires explicit CRM analysis live opt-in and an HTTPS signed receiver")
    call = await prepared(db, tenant_a)
    await make_integration(db, tenant_a, credentials={"signing_secret": os.environ["CRM_ANALYSIS_WEBHOOK_SECRET"]},
        config={"url": os.environ["CRM_ANALYSIS_WEBHOOK_URL"], "post_call_analysis_environment_id": str(call.environment_id)})
    assert (await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **scope(call, tenant_a))).success
    sync = (await db.scalars(select(CrmSync))).one()
    outcome = await service.process_sync(db, sync)
    assert outcome.status == CrmSyncStatus.SYNCED and outcome.external_id


async def test_custom_field_values_not_just_references_reach_crm(db, tenant_a, sessionmaker_):
    from app.services import post_call_analysis_service as analysis
    call = await prepared(db, tenant_a)
    await make_integration(db, tenant_a, config={"url": "https://hooks.example.com/voxdesk",
        "post_call_analysis_environment_id": str(call.environment_id)})
    schema = await analysis.create_schema(db, tenant_id=tenant_a.id, environment_id=call.environment_id,
        name="Booking", description="", fields=[{"name": "requested", "type": "boolean", "required": True}])
    await db.commit()
    async def executor(choice, prompt, timeout_ms):
        if '"requested"' in prompt:
            return {"text": '{"requested":true}', "tokens": 7}
        return await completed_executor(choice, prompt, timeout_ms)
    assert (await PostCallPipeline(sessionmaker_, executor=executor).run(call.id, **scope(call, tenant_a))).success
    event = (await db.scalars(select(CrmEvent))).one()
    assert event.payload["analysis"][f"schema:{schema.id}:v1"]["fields"] == {"requested": True}


async def test_emitter_rollback_preserves_outer_transaction_ownership(db, tenant_a):
    from app.integrations.crm import events
    from app.db.models import Call, CrmEventType
    call = await prepared(db, tenant_a)
    call_id = call.id
    await make_integration(db, tenant_a)
    call.summary = "Uncommitted business change"
    await events.emit(db, tenant_id=tenant_a.id, event_type=CrmEventType.CALL_COMPLETED,
        entity_id=call_id, payload=events.call_payload(call, tenant_a))
    await db.rollback()
    assert (await db.get(Call, call_id)).summary is None
    assert await db.scalar(select(func.count()).select_from(CrmEvent)) == 0
    assert await db.scalar(select(func.count()).select_from(CrmSync)) == 0


async def test_missing_provider_secret_is_not_configured(db, tenant_a, sessionmaker_, monkeypatch):
    _, integration = await admitted(db, tenant_a, sessionmaker_)
    integration.credentials_encrypted = None
    await db.commit()
    transport = FakeTransport((200, {"id": "must-not-be-used"})).install(monkeypatch)
    sync = (await db.scalars(select(CrmSync))).one()
    result = await service.process_sync(db, sync)
    assert result.status == CrmSyncStatus.PERMANENT_FAILURE and result.error_code == "not_configured"
    assert sync.external_id is None and not transport.requests


async def test_delivery_audit_failure_does_not_commit_synced(db, tenant_a, sessionmaker_, monkeypatch):
    from app.audit import service as audit
    await admitted(db, tenant_a, sessionmaker_)
    sync = (await db.scalars(select(CrmSync))).one()
    sync_id = sync.id
    FakeTransport((200, {"id": "contact"}), (200, {"id": "acknowledged-note"})).install(monkeypatch)
    async def fail(*args, **kwargs):
        raise RuntimeError("delivery audit unavailable")
    monkeypatch.setattr(audit, "record_event", fail)
    with pytest.raises(RuntimeError, match="delivery audit unavailable"):
        await service.process_sync(db, sync)
    await db.rollback()
    persisted = await db.get(CrmSync, sync_id)
    assert persisted.status == CrmSyncStatus.PROCESSING and persisted.external_id is None


async def test_real_configuration_api_binds_only_owned_environment(client, db, tenant_a, owner_a, owner_b, viewer_a, sessionmaker_):
    from tests.conftest import auth_headers
    call = await prepared(db, tenant_a)
    body = {"config": {"url": "https://hooks.example.com/voxdesk", "post_call_analysis_environment_id": str(call.environment_id)}}
    headers = await auth_headers(client, owner_a)
    foreign = await client.put("/api/integrations/crm/webhook", json=body, headers=await auth_headers(client, owner_b))
    assert foreign.status_code == 404, foreign.text
    assert (await client.put("/api/integrations/crm/webhook", json=body, headers=await auth_headers(client, viewer_a))).status_code == 403
    response = await client.put("/api/integrations/crm/webhook", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["config"]["post_call_analysis_environment_id"] == str(call.environment_id)
    assert (await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call.id, **scope(call, tenant_a))).success
    assert await db.scalar(select(func.count()).select_from(CrmSync)) == 1


async def test_configuration_audit_failure_does_not_save_binding(client, db, tenant_a, owner_a, monkeypatch):
    from tests.conftest import auth_headers
    from app.api import integration_routes
    from app.db.models import CrmIntegration
    call = await prepared(db, tenant_a)
    headers = await auth_headers(client, owner_a)
    async def fail(*args, **kwargs):
        raise RuntimeError("configuration audit failure")
    monkeypatch.setattr(integration_routes, "record_audit", fail)
    with pytest.raises(RuntimeError, match="configuration audit failure"):
        await client.put("/api/integrations/crm/webhook", headers=headers, json={"config": {
            "url": "https://hooks.example.com/voxdesk", "post_call_analysis_environment_id": str(call.environment_id)}})
    assert await db.scalar(select(func.count()).select_from(CrmIntegration)) == 0
