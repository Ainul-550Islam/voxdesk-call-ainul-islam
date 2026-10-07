"""Integration tests for Prompt 2: Durable Chat Agent + Messages + Contacts + Contact Memory."""

from __future__ import annotations

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.jwt import create_access_token
from app.db.models import AuditLog, User, UserRole
from app.db.retell_models import (
    ChatAgent as ChatAgent,
    ChatAgentVersion as ChatAgentVersion,
    ChatMessage,
    ChatSession,
    Contact,
    ContactMemoryEntry,
)
from app.db.session import get_session
from app.main import app
from tests.conftest import make_tenant


async def _make_user_and_headers(session, tenant, role: UserRole = UserRole.ADMIN) -> dict[str, str]:
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=f"chat-admin-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed",
        role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    token, _ = create_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        role=role.value,
        token_version=user.token_version,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_contact_and_contact_memory_lifecycle_and_credential_refusal(engine) -> None:
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    try:
        async with maker() as setup_s:
            tenant = await make_tenant(setup_s, name="Contact CRM Tenant")
            headers = await _make_user_and_headers(setup_s, tenant)
            await setup_s.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Create contact with non-normalized phone -> normalized to E.164
            c_res = await client.post(
                "/api/contacts",
                headers=headers,
                json={
                    "phone": "415-555-0188",
                    "name": "Nusrat Jahan",
                    "email": "nusrat@example.com",
                    "company": "Dhaka Cloud Ltd",
                    "custom_fields": {"segment": "enterprise"},
                    "source": "crm",
                },
            )
            assert c_res.status_code == 201, c_res.text
            c_body = c_res.json()
            contact_id = c_body["id"]
            assert c_body["phone"] == "+14155550188"
            assert c_body["lifecycle"] == "active"

            # 2. Save durable contact memory entries
            m1_res = await client.put(
                f"/api/contacts/{contact_id}/memory/preferred_language",
                headers=headers,
                json={
                    "value": "Bengali",
                    "value_type": "string",
                    "source": "manual",
                    "confidence": 0.99,
                    "importance": 90,
                },
            )
            assert m1_res.status_code == 200, m1_res.text
            assert m1_res.json()["key"] == "preferred_language"
            assert m1_res.json()["value"] == "Bengali"

            # 3. Credential-shaped memory key is refused with 422
            bad_mem = await client.put(
                f"/api/contacts/{contact_id}/memory/openai_api_key",
                headers=headers,
                json={"value": "sk-secret", "value_type": "string", "source": "manual"},
            )
            assert bad_mem.status_code == 422

            # 4. Archive and restore contact
            arch_res = await client.post(
                f"/api/contacts/{contact_id}/archive", headers=headers
            )
            assert arch_res.status_code == 200
            assert arch_res.json()["lifecycle"] == "archived"

            rest_res = await client.post(
                f"/api/contacts/{contact_id}/restore", headers=headers
            )
            assert rest_res.status_code == 200
            assert rest_res.json()["lifecycle"] == "active"

        # 5. Fresh DB session proves persistence
        async with maker() as check_s:
            db_contact = await check_s.get(Contact, uuid.UUID(contact_id))
            assert db_contact is not None
            assert db_contact.phone == "+14155550188"
            mem_rows = (
                await check_s.execute(
                    select(ContactMemoryEntry).where(
                        ContactMemoryEntry.contact_id == uuid.UUID(contact_id)
                    )
                )
            ).scalars().all()
            assert len(mem_rows) == 1
            assert mem_rows[0].key == "preferred_language"
            assert mem_rows[0].value == "Bengali"
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_chat_agent_etag_conflict_publish_versions_and_rollback(engine) -> None:
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    try:
        async with maker() as setup_s:
            tenant = await make_tenant(setup_s, name="Chat Agent Lifecycle Tenant")
            headers = await _make_user_and_headers(setup_s, tenant)
            await setup_s.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Create chat agent
            create_res = await client.post(
                "/api/chat-agents",
                headers=headers,
                json={
                    "name": "Enterprise Billing Chat",
                    "description": "Billing assistant",
                    "draft_config": {
                        "system_prompt": "You are Billing Chat v1.",
                        "first_message": "Hello! Ask me about billing.",
                        "model": "gpt-4o-mini",
                        "temperature": 0.2,
                    },
                },
            )
            assert create_res.status_code == 201, create_res.text
            agent_id = create_res.json()["id"]
            etag_1 = create_res.json()["etag"]
            assert etag_1.startswith('W/"')

            # Update with matching If-Match succeeds
            patch_ok = await client.patch(
                f"/api/chat-agents/{agent_id}",
                headers={**headers, "If-Match": etag_1},
                json={
                    "draft_config": {
                        "system_prompt": "You are Billing Chat v1 refined.",
                        "first_message": "Hello! Ask me about billing.",
                        "model": "gpt-4o-mini",
                        "temperature": 0.2,
                    }
                },
            )
            assert patch_ok.status_code == 200, patch_ok.text
            etag_2 = patch_ok.json()["etag"]
            assert etag_2 != etag_1

            # Stale If-Match returns 409 Conflict
            patch_conflict = await client.patch(
                f"/api/chat-agents/{agent_id}",
                headers={**headers, "If-Match": etag_1},
                json={"description": "Stale overwrite attempt"},
            )
            assert patch_conflict.status_code == 409

            # Validate draft
            val_res = await client.post(
                f"/api/chat-agents/{agent_id}/validate", headers=headers
            )
            assert val_res.status_code == 200
            assert val_res.json()["valid"] is True

            # Publish v1
            pub1 = await client.post(
                f"/api/chat-agents/{agent_id}/publish",
                headers=headers,
                json={"change_summary": "Initial v1"},
            )
            assert pub1.status_code == 200
            assert pub1.json()["version"] == 1

            # Modify draft & publish v2
            await client.patch(
                f"/api/chat-agents/{agent_id}",
                headers=headers,
                json={
                    "draft_config": {
                        "system_prompt": "You are Billing Chat v2.",
                        "first_message": "Welcome to Billing v2!",
                        "model": "gpt-4o",
                        "temperature": 0.5,
                    }
                },
            )
            pub2 = await client.post(
                f"/api/chat-agents/{agent_id}/publish",
                headers=headers,
                json={"change_summary": "Second v2"},
            )
            assert pub2.status_code == 200
            assert pub2.json()["version"] == 2

            # Inspect v1 snapshot — must remain immutable
            v1_res = await client.get(
                f"/api/chat-agents/{agent_id}/versions/1", headers=headers
            )
            assert v1_res.status_code == 200
            assert (
                v1_res.json()["config"]["system_prompt"]
                == "You are Billing Chat v1 refined."
            )

            # Rollback to v1 -> mints v3
            rb_res = await client.post(
                f"/api/chat-agents/{agent_id}/rollback",
                headers=headers,
                json={"target_version": 1, "reason": "Restore v1 prompt"},
            )
            assert rb_res.status_code == 200
            assert rb_res.json()["version"] == 3
            assert (
                rb_res.json()["config"]["system_prompt"]
                == "You are Billing Chat v1 refined."
            )

            # Version history contains [3, 2, 1]
            hist_res = await client.get(
                f"/api/chat-agents/{agent_id}/versions", headers=headers
            )
            assert hist_res.status_code == 200
            assert [v["version"] for v in hist_res.json()] == [3, 2, 1]
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_durable_chat_session_messages_and_cross_tenant_isolation(engine) -> None:
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    try:
        async with maker() as setup_s:
            tenant_a = await make_tenant(setup_s, name="Chat Tenant A")
            tenant_b = await make_tenant(setup_s, name="Chat Tenant B")
            headers_a = await _make_user_and_headers(setup_s, tenant_a)
            headers_b = await _make_user_and_headers(setup_s, tenant_b)
            await setup_s.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            agent_res = await client.post(
                "/api/chat-agents",
                headers=headers_a,
                json={
                    "name": "Tenant A Concierge",
                    "description": "Concierge",
                    "draft_config": {
                        "system_prompt": "Always greet the customer warmly.",
                        "first_message": "Hi there! How can I assist?",
                        "model": "gpt-4o-mini",
                    },
                },
            )
            agent_id = agent_res.json()["id"]
            await client.post(
                f"/api/chat-agents/{agent_id}/publish",
                headers=headers_a,
                json={"change_summary": "v1"},
            )

            # Start durable chat session linked to a contact phone
            sess_res = await client.post(
                f"/api/chat-agents/{agent_id}/sessions",
                headers=headers_a,
                json={
                    "contact_phone": "+14155550166",
                    "contact_name": "Karim Ahmed",
                    "channel": "web",
                    "dynamic_variables": {"plan": "pro"},
                },
            )
            assert sess_res.status_code == 201, sess_res.text
            session_id = sess_res.json()["id"]
            contact_id = sess_res.json()["contact_id"]
            assert sess_res.json()["message_count"] == 1

            # Send message with memory update
            turn_res = await client.post(
                f"/api/chat-sessions/{session_id}/messages",
                headers=headers_a,
                json={
                    "content": "I need help with my invoice",
                    "memory_updates": {"account_tier": "VIP"},
                },
            )
            assert turn_res.status_code == 201, turn_res.text
            turn_body = turn_res.json()
            assert turn_body["user_message"]["sequence"] == 2
            assert turn_body["assistant_message"]["sequence"] == 3
            assert "account_tier" in turn_body["memory_keys_saved"]
            assert "VIP" in turn_body["assistant_message"]["content"]

            # List messages
            msgs_res = await client.get(
                f"/api/chat-sessions/{session_id}/messages", headers=headers_a
            )
            assert msgs_res.status_code == 200
            assert [m["sequence"] for m in msgs_res.json()] == [1, 2, 3]

            # Cross-tenant isolation: Tenant B gets 404 on all Tenant A resources
            assert (
                await client.get(f"/api/chat-agents/{agent_id}", headers=headers_b)
            ).status_code == 404
            assert (
                await client.get(f"/api/chat-sessions/{session_id}", headers=headers_b)
            ).status_code == 404
            assert (
                await client.get(
                    f"/api/chat-sessions/{session_id}/messages", headers=headers_b
                )
            ).status_code == 404
            assert (
                await client.get(f"/api/contacts/{contact_id}", headers=headers_b)
            ).status_code == 404
            assert (
                await client.get(
                    f"/api/contacts/{contact_id}/memory", headers=headers_b
                )
            ).status_code == 404

            # End session
            end_res = await client.post(
                f"/api/chat-sessions/{session_id}/end",
                headers=headers_a,
                json={"status": "completed"},
            )
            assert end_res.status_code == 200
            assert end_res.json()["status"] == "completed"

        # Verify DB persistence and AuditLog entries in a fresh session
        async with maker() as verify_s:
            db_sess = await verify_s.get(ChatSession, uuid.UUID(session_id))
            assert db_sess is not None
            assert db_sess.status == "completed"
            assert db_sess.message_count == 3

            db_msgs = (
                await verify_s.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == uuid.UUID(session_id))
                    .order_by(ChatMessage.sequence.asc())
                )
            ).scalars().all()
            assert len(db_msgs) == 3

            audits = (
                await verify_s.execute(
                    select(AuditLog).where(AuditLog.tenant_id == tenant_a.id)
                )
            ).scalars().all()
            events = {a.detail.get("event") for a in audits if isinstance(a.detail, dict)}
            assert "chat_agent.created" in events
            assert "chat_agent.published" in events
            assert "chat_session.created" in events
            assert "chat_session.ended" in events
            assert "contact.created" in events
            assert "contact_memory.created" in events
    finally:
        app.dependency_overrides.pop(get_session, None)
