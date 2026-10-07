from __future__ import annotations

import pytest

from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_persisted_chat_message_memory_and_sms_provider_fail_closed(
    client, owner_a
):
    headers = await auth_headers(client, owner_a)
    contact = await client.post(
        "/api/contacts",
        headers=headers,
        json={"phone": "+14155550145", "name": "Chat E2E Contact"},
    )
    assert contact.status_code == 201, contact.text
    contact_id = contact.json()["id"]

    chat_agent = await client.post(
        "/api/chat-agents",
        headers=headers,
        json={
            "name": "Persisted chat E2E",
            "description": "Chat API path test",
            "draft_config": {
                "system_prompt": "You are a support assistant. Never claim an unverified external action.",
                "first_message": "Hello, how can I help?",
                "model": "gpt-4o-mini",
                "temperature": 0.2,
            },
        },
    )
    assert chat_agent.status_code == 201, chat_agent.text
    chat_agent_id = chat_agent.json()["id"]
    validation = await client.post(
        f"/api/chat-agents/{chat_agent_id}/validate", headers=headers
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"] is True
    published = await client.post(
        f"/api/chat-agents/{chat_agent_id}/publish",
        headers=headers,
        json={"change_summary": "Prompt 8 persisted chat flow"},
    )
    assert published.status_code == 200, published.text
    assert published.json()["version"] == 1

    chat_session = await client.post(
        f"/api/chat-agents/{chat_agent_id}/sessions",
        headers=headers,
        json={
            "chat_agent_id": chat_agent_id,
            "contact_id": contact_id,
            "channel": "web",
            "dynamic_variables": {"locale": "en"},
        },
    )
    assert chat_session.status_code == 201, chat_session.text
    session_id = chat_session.json()["id"]
    assert chat_session.json()["chat_agent_version"] == 1

    message = await client.post(
        f"/api/chat-sessions/{session_id}/messages",
        headers=headers,
        json={
            "content": "Please remember that I prefer Bengali responses.",
            "memory_updates": {"preferred_language": "Bengali"},
        },
    )
    assert message.status_code == 201, message.text
    turn = message.json()
    assert turn["user_message"]["content"].startswith("Please remember")
    assert turn["assistant_message"]["content"]
    assert "preferred_language" in turn["memory_keys_saved"]
    assert turn["assistant_message"]["metadata"]["chat_agent_version"] == 1

    messages = await client.get(
        f"/api/chat-sessions/{session_id}/messages", headers=headers
    )
    assert messages.status_code == 200, messages.text
    persisted_messages = messages.json()
    assert [item["role"] for item in persisted_messages] == ["assistant", "user", "assistant"]
    assert persisted_messages[0]["content"] == "Hello, how can I help?"

    channel = await client.post(
        "/api/channels",
        headers=headers,
        json={"channel_type": "sms", "provider": "twilio", "config": {}, "is_active": True},
    )
    assert channel.status_code == 201, channel.text
    channel_id = channel.json()["id"]
    assert channel.json()["health_status"] == "unknown"

    health = await client.post(
        f"/api/channels/{channel_id}/health-check", headers=headers
    )
    assert health.status_code == 501
    assert health.json()["detail"]["code"] == "channel_health_check_not_implemented"

    send = await client.post(
        "/api/channels/send",
        headers=headers,
        json={
            "channel_type": "sms",
            "to": "+14155550145",
            "message": "Prompt 8 test; no SMS should be delivered.",
        },
    )
    assert send.status_code == 501
    assert send.json()["detail"]["code"] == "channel_send_not_implemented"
    assert "no message or call was created" in send.json()["detail"]["message"]
