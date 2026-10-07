from __future__ import annotations

import pytest

from tests.conftest import add_document, auth_headers
from tests.e2e._support import create_published_voice_agent


@pytest.mark.asyncio
async def test_builder_publishes_persisted_snapshot_then_simulated_call_uses_it(
    client, db, tenant_a, owner_a
):
    headers = await auth_headers(client, owner_a)
    document = await add_document(
        db,
        tenant_a,
        title="Billing contact facts",
        filename="billing-facts.md",
        text="For this E2E test, billing support email is billing@example.test.",
    )
    agent, published, version = await create_published_voice_agent(
        client,
        headers,
        name="Builder to Call E2E",
        greeting="Hello from the persisted Builder E2E version.",
        system_prompt="PROMPT8_BUILDER_SIGNATURE: Handle billing questions.",
        knowledge_base={"id": document.id, "title": document.title},
        transfer_phone_number="+14155550123",
    )
    agent_id = agent["agent_id"]
    version_number = published["version"]

    reloaded = await client.get(f"/api/v1/agents/{agent_id}/builder", headers=headers)
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json()["identity"]["greeting"] == "Hello from the persisted Builder E2E version."
    assert reloaded.json()["model"]["system_prompt"] == "PROMPT8_BUILDER_SIGNATURE: Handle billing questions."
    assert reloaded.json()["knowledge_bases"][0]["kb_id"] == str(document.id)
    assert reloaded.json()["tools"][0]["tool_id"] == "book_appointment"
    assert reloaded.json()["call_handling"]["transfer_phone_number"] == "+14155550123"
    assert version["version_number"] == version_number

    simulation = await client.post(
        "/api/v1/testing/playground/run",
        headers=headers,
        json={
            "agent_id": agent_id,
            "agent_kind": "voice",
            "agent_version_number": version_number,
            "user_message": "What did the published support prompt say?",
            "evaluation_rules": [
                {
                    "name": "Uses persisted builder prompt",
                    "rule_type": "contains",
                    "config": {"substring": "PROMPT8_BUILDER_SIGNATURE"},
                }
            ],
        },
    )
    assert simulation.status_code == 200, simulation.text
    simulation_body = simulation.json()
    assert simulation_body["agent_version_number"] == version_number
    assert "PROMPT8_BUILDER_SIGNATURE" in simulation_body["pinned_config_snapshot"]["system_prompt"]
    assert simulation_body["status"] == "passed"

    call_response = await client.post(
        "/api/v1/telephony/calls/outbound",
        headers=headers,
        json={
            "to_number": "+14155550177",
            "from_number": "+14155550101",
            "agent_id": agent_id,
            "agent_version_number": version_number,
            "provider": "SIMULATED",
            "is_simulation": True,
            "idempotency_key": "prompt8-builder-call-001",
        },
    )
    assert call_response.status_code == 201, call_response.text
    call = call_response.json()
    assert call["is_simulation"] is True
    assert call["agent_version_number"] == version_number
    call_id = call["id"]

    media_start = await client.post(
        f"/api/v1/telephony/calls/{call_id}/media-event",
        headers=headers,
        json={"type": "media.start", "encoding": "mulaw", "sample_rate": 8000},
    )
    assert media_start.status_code == 200, media_start.text
    assert media_start.json()["status"] == "IN_PROGRESS"
    assert media_start.json()["media_state"] == "SPEAKING"

    utterance = await client.post(
        f"/api/v1/telephony/calls/{call_id}/media-event",
        headers=headers,
        json={"type": "media.utterance", "text": "Please help me understand my billing statement."},
    )
    assert utterance.status_code == 200, utterance.text
    utterance_body = utterance.json()
    assert utterance_body["synthetic_media"] is True
    assert utterance_body["execution_kind"].lower() == "simulation"
    assert "Understood your request" in utterance_body["agent_text"]

    completed = await client.post(
        f"/api/v1/telephony/calls/{call_id}/hangup", headers=headers, json={}
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"
    assert completed.json()["agent_version_number"] == version_number
