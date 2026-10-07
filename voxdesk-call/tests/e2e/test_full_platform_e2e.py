from __future__ import annotations

import uuid

import pytest

from app.db.models import Tenant
import app.db.enterprise_models  # noqa: F401 — register parity models before test schema creation
from app.db.telephony_models import TelephonyCallSession
from tests.acd_support import production
from tests.conftest import TEST_PASSWORD, add_document, auth_headers
from tests.e2e._support import create_published_voice_agent, publish_builder_revision


@pytest.mark.asyncio
async def test_master_new_customer_to_simulated_call_and_truthful_cross_module_state(
    client, db, billing_plans
):
    email = f"prompt8-master-{uuid.uuid4().hex[:12]}@example.com"
    signup = await client.post(
        "/auth/signup",
        json={
            "organization_name": f"Prompt 8 Master {uuid.uuid4().hex[:8]}",
            "full_name": "Prompt 8 Master Owner",
            "email": email,
            "password": TEST_PASSWORD,
            "industry": "technology",
        },
    )
    assert signup.status_code == 201, signup.text
    owner_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}
    tenant_id = uuid.UUID(signup.json()["tenant_id"])
    tenant = await db.get(Tenant, tenant_id)
    assert tenant is not None

    me = await client.get("/auth/me", headers=owner_headers)
    assert me.status_code == 200, me.text
    assert me.json()["tenant"]["id"] == str(tenant_id)
    environment = await production(db, tenant)

    public_catalog = await client.get("/api/v1/public/use-cases?page=1&page_size=5")
    assert public_catalog.status_code == 200, public_catalog.text
    assert public_catalog.json()["data"]["items"]

    document = await add_document(
        db,
        tenant,
        title="Prompt 8 master test knowledge",
        filename="master-knowledge.md",
        text="Master flow test fact: the fictional support desk opens Monday through Friday at nine AM.",
    )
    knowledge_search = await client.post(
        "/api/knowledge/search",
        headers=owner_headers,
        json={"query": "When does the fictional support desk open?", "environment_id": str(environment.id)},
    )
    assert knowledge_search.status_code == 200, knowledge_search.text
    assert any("nine AM" in hit["text"] for hit in knowledge_search.json()["results"])

    agent, published, version = await create_published_voice_agent(
        client,
        owner_headers,
        name="Prompt 8 Master Voice Agent",
        greeting="Hello, I can answer from the published support facts.",
        system_prompt="PROMPT8_MASTER_SIGNATURE: Use the attached knowledge source and do not claim a real call occurred.",
        knowledge_base={"id": document.id, "title": document.title},
        transfer_phone_number="+14155550124",
        environment_id=str(environment.id),
        environment="production",
    )
    agent_id = agent["agent_id"]
    version_number = published["version"]
    assert version["version_number"] == version_number

    restored_builder = await client.get(
        f"/api/v1/agents/{agent_id}/builder", headers=owner_headers
    )
    assert restored_builder.status_code == 200, restored_builder.text
    assert restored_builder.json()["environment_id"] == str(environment.id)
    assert restored_builder.json()["knowledge_bases"][0]["kb_id"] == str(document.id)

    simulation = await client.post(
        "/api/v1/testing/playground/run",
        headers=owner_headers,
        json={
            "agent_id": agent_id,
            "agent_kind": "voice",
            "agent_version_number": version_number,
            "user_message": "What does the published master support fact say?",
            "evaluation_rules": [
                {
                    "name": "Uses the pinned published prompt",
                    "rule_type": "contains",
                    "config": {"substring": "PROMPT8_MASTER_SIGNATURE"},
                }
            ],
        },
    )
    assert simulation.status_code == 200, simulation.text
    assert simulation.json()["status"] == "passed"
    assert simulation.json()["agent_version_number"] == version_number
    assert simulation.json()["pinned_config_snapshot"]["system_prompt"].startswith(
        "PROMPT8_MASTER_SIGNATURE"
    )

    number = await client.post(
        "/api/v1/telephony/phone-numbers",
        headers=owner_headers,
        json={
            "number": "+14155550191",
            "provider": "SIMULATED",
            "environment_id": str(environment.id),
            "inbound_agent_id": agent_id,
            "outbound_agent_id": agent_id,
            "metadata": {"purpose": "explicit deterministic test fixture"},
        },
    )
    assert number.status_code == 201, number.text
    assert number.json()["provider"] == "SIMULATED"
    assert number.json()["status"] == "READY"
    assert number.json()["environment_id"] == str(environment.id)

    call_response = await client.post(
        "/api/v1/telephony/calls/outbound",
        headers=owner_headers,
        json={
            "to_number": "+14155550192",
            "phone_number_id": number.json()["id"],
            "agent_id": agent_id,
            "agent_version_number": version_number,
            "provider": "SIMULATED",
            "is_simulation": True,
            "environment_id": str(environment.id),
            "idempotency_key": "prompt8-master-simulated-call-001",
            "metadata": {"journey": "master-smoke"},
        },
    )
    assert call_response.status_code == 201, call_response.text
    call_id = call_response.json()["id"]
    assert call_response.json()["is_simulation"] is True
    assert call_response.json()["status"].lower() == "dialing"
    assert call_response.json()["agent_version_number"] == version_number

    call_read = await client.get(
        f"/api/v1/telephony/calls/{call_id}", headers=owner_headers
    )
    assert call_read.status_code == 200, call_read.text
    assert call_read.json()["id"] == call_id
    assert call_read.json()["metadata"]["resolved_agent_version_id"] == version["id"]

    hangup = await client.post(
        f"/api/v1/telephony/calls/{call_id}/hangup",
        headers=owner_headers,
        json={"reason": "prompt8_test_cleanup"},
    )
    assert hangup.status_code == 200, hangup.text
    assert hangup.json()["status"].lower() == "cancelled"

    # Local contact memory is a persisted product path; the tenant has no CRM
    # credential, so no remote CRM write is represented as successful.
    contact = await client.post(
        "/api/contacts",
        headers=owner_headers,
        json={"phone": "+14155550193", "name": "Prompt 8 Master Contact"},
    )
    assert contact.status_code == 201, contact.text
    contact_id = contact.json()["id"]
    memory = await client.put(
        f"/api/contacts/{contact_id}/memory/preferred_language",
        headers=owner_headers,
        json={
            "value": "Bengali",
            "value_type": "string",
            "source": "agent",
            "confidence": 0.9,
            "importance": 50,
        },
    )
    assert memory.status_code == 200, memory.text

    crm_inventory = await client.get(
        "/api/v1/parity/integrations", headers=owner_headers
    )
    assert crm_inventory.status_code == 200, crm_inventory.text
    salesforce = next(
        item for item in crm_inventory.json()["items"]
        if item["integration_type"] == "crm" and item["provider"] == "salesforce"
    )
    assert salesforce["status"] == "NOT_CONFIGURED"
    assert salesforce["credentials_present"] is False

    inspector = await client.get(
        "/api/v1/parity/e2e/inspect",
        headers=owner_headers,
        params={"agent_id": agent_id, "call_id": call_id},
    )
    assert inspector.status_code == 200, inspector.text
    inspected = inspector.json()
    assert inspected["environment_id"] == str(environment.id)
    assert inspected["call_id"] == call_id
    assert inspected["call_agent_version_number"] == version_number
    assert inspected["is_simulation"] is True
    assert inspected["side_effects_performed"] is False
    assert any(step["key"] == "pinned_call_version" and step["status"] == "PASS" for step in inspected["steps"])
    assert any(step["key"] == "crm_writeback" and step["status"] == "NOT_CONFIGURED" for step in inspected["steps"])
    assert inspected["overall_status"] == "PARTIAL"

    analytics = await client.get(
        "/api/analytics/overview?range=last_30_days", headers=owner_headers
    )
    assert analytics.status_code == 200, analytics.text
    assert analytics.json()["calls"]["total"] == 0
    billing = await client.get("/api/billing/usage", headers=owner_headers)
    assert billing.status_code == 200, billing.text
    voice_usage = next(
        metric for metric in billing.json()["metrics"] if metric["metric"] == "voice_minute"
    )
    assert voice_usage["used"] == 0.0

    audit = await client.get(
        "/api/v1/audit/events", headers=owner_headers, params={"limit": 200}
    )
    assert audit.status_code == 200, audit.text
    assert any(
        item["detail"].get("event") == "agent.created"
        and item["detail"].get("resource_id") == agent_id
        for item in audit.json()["items"]
    )


@pytest.mark.asyncio
async def test_call_pin_keeps_superseded_snapshot_and_unpinned_call_uses_persisted_pointer(
    client, db, tenant_a, owner_a
):
    headers = await auth_headers(client, owner_a)
    environment = await production(db, tenant_a)
    agent, published_v1, version_v1 = await create_published_voice_agent(
        client,
        headers,
        name="Prompt 8 Exact Version Pin",
        greeting="Hello from the first published snapshot.",
        system_prompt="PROMPT8_PIN_V1: Preserve this exact call version.",
        environment_id=str(environment.id),
        environment="production",
    )
    agent_id = agent["agent_id"]
    assert published_v1["version"] == 1

    published_v2, version_v2 = await publish_builder_revision(
        client,
        headers,
        agent_id,
        greeting="Hello from the second published snapshot.",
        system_prompt="PROMPT8_PIN_V2: This is the current published pointer.",
    )
    assert published_v2["version"] == 2
    assert version_v2["id"] != version_v1["id"]

    superseded_v1 = await client.get(
        f"/api/v1/agents/{agent_id}/versions/1", headers=headers
    )
    assert superseded_v1.status_code == 200, superseded_v1.text
    assert superseded_v1.json()["status"] == "superseded"

    pinned_call_response = await client.post(
        "/api/v1/telephony/calls/outbound",
        headers=headers,
        json={
            "to_number": "+14155550161",
            "from_number": "+14155550162",
            "agent_id": agent_id,
            "agent_version_number": 1,
            "provider": "SIMULATED",
            "is_simulation": True,
            "environment_id": str(environment.id),
            "idempotency_key": f"prompt8-pin-v1-{uuid.uuid4().hex}",
        },
    )
    assert pinned_call_response.status_code == 201, pinned_call_response.text
    pinned_call = pinned_call_response.json()
    assert pinned_call["agent_version_number"] == 1
    assert pinned_call["metadata"]["resolved_agent_version_id"] == version_v1["id"]
    assert pinned_call["metadata"]["resolved_agent_version_status"] == "superseded"

    inspection = await client.get(
        "/api/v1/parity/e2e/inspect",
        headers=headers,
        params={
            "agent_id": agent_id,
            "call_id": pinned_call["id"],
            "environment_id": str(environment.id),
        },
    )
    assert inspection.status_code == 200, inspection.text
    inspection_body = inspection.json()
    assert inspection_body["published_version_id"] == version_v2["id"]
    assert inspection_body["call_agent_version_number"] == 1
    assert inspection_body["call_agent_version_id"] == version_v1["id"]
    pinned_step = next(
        step for step in inspection_body["steps"] if step["key"] == "pinned_call_version"
    )
    assert pinned_step["status"] == "PASS"
    assert "status=superseded" in pinned_step["detail"]

    # Corrupt metadata is a negative fixture: an existing version number must
    # not be used as a fallback when the call carries an invalid exact version ID.
    pinned_session = await db.get(TelephonyCallSession, uuid.UUID(pinned_call["id"]))
    assert pinned_session is not None
    pinned_session.metadata_json = {
        **(pinned_session.metadata_json or {}),
        "resolved_agent_version_id": "not-a-valid-uuid",
    }
    await db.commit()
    corrupt_pin_inspection = await client.get(
        "/api/v1/parity/e2e/inspect",
        headers=headers,
        params={
            "agent_id": agent_id,
            "call_id": pinned_call["id"],
            "environment_id": str(environment.id),
        },
    )
    assert corrupt_pin_inspection.status_code == 200, corrupt_pin_inspection.text
    corrupt_pin_body = corrupt_pin_inspection.json()
    corrupt_pin_step = next(
        step for step in corrupt_pin_body["steps"] if step["key"] == "pinned_call_version"
    )
    assert corrupt_pin_step["status"] == "FAIL"
    assert "no version-number fallback" in corrupt_pin_step["detail"]
    assert corrupt_pin_body["call_agent_version_id"] is None

    unpinned_call_response = await client.post(
        "/api/v1/telephony/calls/outbound",
        headers=headers,
        json={
            "to_number": "+14155550163",
            "from_number": "+14155550162",
            "agent_id": agent_id,
            "provider": "SIMULATED",
            "is_simulation": True,
            "environment_id": str(environment.id),
            "idempotency_key": f"prompt8-current-pointer-{uuid.uuid4().hex}",
        },
    )
    assert unpinned_call_response.status_code == 201, unpinned_call_response.text
    unpinned_call = unpinned_call_response.json()
    assert unpinned_call["agent_version_number"] == 2
    assert unpinned_call["metadata"]["resolved_agent_version_id"] == version_v2["id"]
    assert unpinned_call["metadata"]["agent_version_resolution_source"] == "current_published_pointer"

    unavailable_pin = await client.post(
        "/api/v1/telephony/calls/outbound",
        headers=headers,
        json={
            "to_number": "+14155550164",
            "from_number": "+14155550162",
            "agent_id": agent_id,
            "agent_version_number": 999,
            "provider": "SIMULATED",
            "is_simulation": True,
            "environment_id": str(environment.id),
            "idempotency_key": f"prompt8-missing-pin-{uuid.uuid4().hex}",
        },
    )
    assert unavailable_pin.status_code == 422, unavailable_pin.text
    assert "AGENT_VERSION_NOT_FOUND" in unavailable_pin.text

    for call_id in (pinned_call["id"], unpinned_call["id"]):
        ended = await client.post(
            f"/api/v1/telephony/calls/{call_id}/hangup",
            headers=headers,
            json={"reason": "prompt8_version_pin_test_cleanup"},
        )
        assert ended.status_code == 200, ended.text
