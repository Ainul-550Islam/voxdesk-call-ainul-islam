"""Small real-API helpers shared by the Prompt 8 integration journeys.

The helpers never insert Agent or AgentVersion rows directly. Builder drafts,
validation, publication, and version pins all travel through the application's
persisted service/API paths.
"""
from __future__ import annotations

from typing import Any

from httpx import AsyncClient


async def create_published_voice_agent(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    name: str,
    greeting: str = "Hello, how can I help you today?",
    system_prompt: str = "You are a helpful voice support agent.",
    knowledge_base: dict[str, Any] | None = None,
    transfer_phone_number: str | None = None,
    environment_id: str | None = None,
    environment: str = "production",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Create and publish an agent through the authenticated builder API."""
    create_payload: dict[str, Any] = {
        "name": name,
        "description": "Prompt 8 persisted E2E fixture",
        "agent_type": "voice",
    }
    if environment_id is not None:
        create_payload["environment_id"] = environment_id
    created = await client.post(
        "/api/v1/agents",
        json=create_payload,
        headers=headers,
    )
    assert created.status_code == 201, created.text
    agent = created.json()
    agent_id = agent["id"]

    builder_response = await client.get(
        f"/api/v1/agents/{agent_id}/builder", headers=headers
    )
    assert builder_response.status_code == 200, builder_response.text
    builder = builder_response.json()

    identity = dict(builder["identity"])
    identity.update(
        {
            "name": name,
            "description": "Prompt 8 persisted E2E fixture",
            "persona": system_prompt,
            "greeting": greeting,
        }
    )
    model = dict(builder["model"])
    model.update(
        {
            "provider": "openai",
            "model_name": "gpt-4o-mini",
            "temperature": 0.2,
            "system_prompt": system_prompt,
            "response_style": "conversational",
        }
    )
    voice = dict(builder["voice"])
    voice.update({"provider": "openai", "voice_id": "alloy", "language": "en-US"})
    call_handling = dict(builder["call_handling"])
    if transfer_phone_number is not None:
        call_handling["transfer_phone_number"] = transfer_phone_number
    security = dict(builder["security"])
    security["redact_pii"] = True

    update: dict[str, Any] = {
        "identity": identity,
        "model": model,
        "voice": voice,
        "tools": [
            {
                "tool_id": "book_appointment",
                "name": "Book appointment",
                "description": "Controlled local E2E tool configuration",
                "parameters": [
                    {
                        "name": "customer_name",
                        "type": "string",
                        "description": "Customer name",
                        "required": True,
                    }
                ],
                "enabled": True,
            }
        ],
        "call_handling": call_handling,
        "security": security,
    }
    if knowledge_base is not None:
        update["knowledge_bases"] = [
            {
                "kb_id": str(knowledge_base["id"]),
                "name": str(knowledge_base.get("title") or "E2E knowledge source"),
                "priority": 1,
                "top_k": 3,
                "similarity_threshold": 0.1,
            }
        ]

    etag = builder_response.headers.get("etag") or builder.get("draft_etag")
    assert etag
    updated = await client.patch(
        f"/api/v1/agents/{agent_id}/builder",
        json=update,
        headers={**headers, "If-Match": etag},
    )
    assert updated.status_code == 200, updated.text

    validated = await client.post(
        f"/api/v1/agents/{agent_id}/validate", headers=headers
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["valid"] is True, validated.text

    publish_payload: dict[str, Any] = {
        "release_notes": "Prompt 8 persisted E2E publish",
        "environment": environment,
    }
    if updated.json().get("environment_id"):
        publish_payload["environment_id"] = updated.json()["environment_id"]
    published = await client.post(
        f"/api/v1/agents/{agent_id}/publish",
        json=publish_payload,
        headers=headers,
    )
    assert published.status_code == 200, published.text
    assert published.json()["version"] >= 1

    version = await client.get(
        f"/api/v1/agents/{agent_id}/versions/{published.json()['version']}",
        headers=headers,
    )
    assert version.status_code == 200, version.text
    assert version.json()["status"] == "published"
    snapshot = version.json()["config_snapshot"]
    assert snapshot.get("greeting") or snapshot.get("identity", {}).get("greeting")
    assert snapshot.get("system_prompt") or snapshot.get("model", {}).get("system_prompt")
    return updated.json(), published.json(), version.json()


async def publish_builder_revision(
    client: AsyncClient,
    headers: dict[str, str],
    agent_id: str,
    *,
    greeting: str,
    system_prompt: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Save, validate, and publish a second immutable version through the API."""
    builder_response = await client.get(
        f"/api/v1/agents/{agent_id}/builder", headers=headers
    )
    assert builder_response.status_code == 200, builder_response.text
    builder = builder_response.json()
    identity = dict(builder["identity"])
    identity.update({"greeting": greeting, "persona": system_prompt})
    model = dict(builder["model"])
    model["system_prompt"] = system_prompt
    update = await client.patch(
        f"/api/v1/agents/{agent_id}/builder",
        json={"identity": identity, "model": model},
        headers={**headers, "If-Match": builder_response.headers.get("etag", builder["draft_etag"])},
    )
    assert update.status_code == 200, update.text
    validation = await client.post(f"/api/v1/agents/{agent_id}/validate", headers=headers)
    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"] is True, validation.text
    body: dict[str, Any] = {"release_notes": "Prompt 8 second version", "environment": "production"}
    if update.json().get("environment_id"):
        body["environment_id"] = update.json()["environment_id"]
    published = await client.post(
        f"/api/v1/agents/{agent_id}/publish", json=body, headers=headers
    )
    assert published.status_code == 200, published.text
    version = await client.get(
        f"/api/v1/agents/{agent_id}/versions/{published.json()['version']}",
        headers=headers,
    )
    assert version.status_code == 200, version.text
    return published.json(), version.json()
