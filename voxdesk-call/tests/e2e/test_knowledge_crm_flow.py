from __future__ import annotations

import pytest

import app.db.enterprise_models  # noqa: F401 — register integration models before test schema creation
from tests.conftest import add_document, auth_headers
from tests.e2e._support import create_published_voice_agent


@pytest.mark.asyncio
async def test_ingested_knowledge_builder_association_contact_memory_and_crm_readiness(
    client, db, tenant_a, owner_a
):
    headers = await auth_headers(client, owner_a)
    document = await add_document(
        db,
        tenant_a,
        title="E2E opening hours",
        filename="opening-hours.md",
        text="Prompt 8 sample business hours are Monday to Friday, nine AM to five PM Dhaka time.",
    )

    search = await client.post(
        "/api/knowledge/search",
        headers=headers,
        json={"query": "What are the sample business hours?"},
    )
    assert search.status_code == 200, search.text
    assert any("nine AM to five PM" in hit["text"] for hit in search.json()["results"])
    assert all("embedding" not in hit for hit in search.json()["results"])

    agent, published, version = await create_published_voice_agent(
        client,
        headers,
        name="Knowledge and CRM E2E",
        greeting="Hello, I can look up configured business information.",
        system_prompt="PROMPT8_KB_SIGNATURE: Use authorized knowledge when available.",
        knowledge_base={"id": document.id, "title": document.title},
    )
    builder = await client.get(
        f"/api/v1/agents/{agent['agent_id']}/builder", headers=headers
    )
    assert builder.status_code == 200
    assert builder.json()["knowledge_bases"][0]["kb_id"] == str(document.id)
    assert version["version_number"] == published["version"]

    contact = await client.post(
        "/api/contacts",
        headers=headers,
        json={
            "phone": "+14155550144",
            "name": "Prompt 8 Contact",
            "email": "prompt8-contact@example.test",
            "company": "E2E Fixture",
            "custom_fields": {"origin": "knowledge-crm-e2e"},
        },
    )
    assert contact.status_code == 201, contact.text
    contact_id = contact.json()["id"]
    memory = await client.put(
        f"/api/contacts/{contact_id}/memory/preferred_language",
        headers=headers,
        json={
            "value": "Bengali",
            "value_type": "string",
            "source": "agent",
            "confidence": 0.9,
            "importance": 60,
        },
    )
    assert memory.status_code == 200, memory.text
    memory_list = await client.get(
        f"/api/contacts/{contact_id}/memory", headers=headers
    )
    assert memory_list.status_code == 200
    assert [(row["key"], row["value"]) for row in memory_list.json()] == [
        ("preferred_language", "Bengali")
    ]
    contact_list = await client.get("/api/contacts", headers=headers)
    assert any(row["id"] == contact_id for row in contact_list.json())

    # Contact/memory persistence is local. No external CRM write is asserted.
    inventory = await client.get("/api/v1/parity/integrations", headers=headers)
    assert inventory.status_code == 200, inventory.text
    salesforce = [
        item for item in inventory.json()["items"]
        if item["provider"] == "salesforce" and item["integration_type"] == "crm"
    ]
    assert salesforce
    assert salesforce[0]["status"] == "NOT_CONFIGURED"
    assert salesforce[0]["credentials_present"] is False
