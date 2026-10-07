"""Version pinning and committed custom result contracts in the main pipeline."""
import json

import pytest
from sqlalchemy import select

from app.db.enterprise_models import AnalysisResult, AnalysisSchema
from app.outbox.models import OutboxEvent
from app.services import post_call_analysis_service as service
from app.telephony.post_call import PostCallPipeline
from tests.conftest import auth_headers
from tests.telephony.test_post_call_pipeline import prepared, scope, completed_executor, isolated_provider  # noqa: F401


@pytest.mark.parametrize("kind,value", [("text", "yes"), ("boolean", True), ("number", 2), ("list", ["x"]), ("custom", {"x": 1}), ("enum", "yes")])
def test_compiler_supports_real_types(kind, value):
    schema = service.compile_fields([{"name": "answer", "type": kind, "required": True,
        "enum_values": ["yes", "no"], "examples": [value]}])
    assert schema["required"] == ["answer"] and schema["additionalProperties"] is False
    assert schema["properties"]["answer"]["examples"] == [value]


@pytest.mark.parametrize("fields", [[], [{"name": "x", "type": "enum"}],
    [{"name": "x", "type": "boolean", "examples": ["yes"]}],
    [{"name": "x", "type": "text"}, {"name": "x", "type": "text"}],
    [{"name": "x", "type": "list", "items_type": "invalid"}]])
def test_invalid_definitions_rejected(fields):
    with pytest.raises(ValueError):
        service.compile_fields(fields)


async def test_api_versions_and_tenant_isolation(client, owner_a, owner_b, db):
    headers = await auth_headers(client, owner_a)
    other = await auth_headers(client, owner_b)
    response = await client.post("/api/analysis/schemas", headers=headers, json={"name": "Preferences",
        "fields": [{"name": "requested", "type": "boolean", "required": True, "examples": [True]}]})
    assert response.status_code == 201, response.text
    original = response.json()
    assert original["version"] == 1 and original["environment_id"]
    path = "/api/analysis/schemas/" + original["id"]
    assert (await client.get(path, headers=other)).status_code == 404
    assert (await client.patch(path, headers=other, json={"name": "stolen"})).status_code == 404
    changed = await client.patch(path, headers=headers, json={"name": "Preferences v2"})
    assert changed.status_code == 200 and changed.json()["version"] == 2
    import uuid
    row = await db.get(AnalysisSchema, uuid.UUID(original["id"]))
    await db.refresh(row)
    assert row.revisions["1"]["name"] == "Preferences"
    assert row.revisions["2"]["name"] == "Preferences v2"
    removed = await client.delete(path, headers=headers)
    assert removed.status_code == 200 and removed.json()["deactivated"] is True
    assert (await client.get(path, headers=headers)).json()["is_active"] is False


async def test_retry_pins_original_version_and_emits_one_real_fact(db, tenant_a, sessionmaker_):
    call = await prepared(db, tenant_a)
    definition = await service.create_schema(db, tenant_id=tenant_a.id, environment_id=call.environment_id,
        name="Booking", description="", fields=[{"name": "requested", "type": "boolean", "required": True}])
    await db.commit()
    call_id, values, schema_id = call.id, scope(call, tenant_a), definition.id
    first = await PostCallPipeline(sessionmaker_).run(call_id, **values)
    assert not first.success
    assert not list((await db.scalars(select(AnalysisResult))).all())
    assert not list((await db.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "call_analyzed"))).all())
    await service.update_schema(db, definition, {"fields": [{"name": "new_field", "type": "text", "required": True}]})
    await db.commit()
    seen = []

    async def executor(choice, prompt, timeout_ms):
        if '"requested"' in prompt:
            assert '"new_field"' not in prompt
            seen.append(prompt)
            return {"text": json.dumps({"requested": True}), "tokens": 6}
        return await completed_executor(choice, prompt, timeout_ms)

    pipeline = PostCallPipeline(sessionmaker_, executor=executor)
    assert (await pipeline.run(call_id, **values, retry_nontransient=True)).success
    assert (await pipeline.run(call_id, **values)).success
    rows = list((await db.scalars(select(AnalysisResult))).all())
    assert len(rows) == 1 and len(seen) == 1
    row = rows[0]
    assert row.schema_id == schema_id and row.schema_version == 1 and row.provenance == "provider"
    assert row.result == {"requested": True}
    events = list((await db.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "call_analyzed"))).all())
    assert len(events) == 1 and events[0].payload["analysis_result_ids"] == [str(row.id)]


async def test_outbox_failure_rolls_back_checkpoint_and_retry_does_not_repeat_provider(db, tenant_a, sessionmaker_, monkeypatch):
    from app.webhooks import call_event_bridge
    call = await prepared(db, tenant_a)
    call_id, values = call.id, scope(call, tenant_a)
    real_publish = call_event_bridge.publish_call_event

    async def failed_publish(session, *args, **kwargs):
        from sqlalchemy.exc import OperationalError
        await real_publish(session, *args, **kwargs)
        raise OperationalError("test rollback", {}, Exception("simulated database failure"))

    monkeypatch.setattr(call_event_bridge, "publish_call_event", failed_publish)
    from sqlalchemy.exc import OperationalError
    with pytest.raises(OperationalError):
        await PostCallPipeline(sessionmaker_, executor=completed_executor).run(call_id, **values)
    assert not list((await db.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "call_analyzed"))).all())
    monkeypatch.setattr(call_event_bridge, "publish_call_event", real_publish)

    async def forbidden_provider(*args):
        raise AssertionError("Completed inference must not be repeated")

    assert (await PostCallPipeline(sessionmaker_, executor=forbidden_provider).run(call_id, **values)).success
    events = list((await db.scalars(select(OutboxEvent).where(OutboxEvent.event_type == "call_analyzed"))).all())
    assert len(events) == 1
