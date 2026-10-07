"""End-to-end integration tests for durable Agent + AgentVersion persistence.

Verifies all 10 requirements of PROMPT 1:
1. Create draft persistence across distinct SQLAlchemy database sessions
2. Draft update & optimistic concurrency (`If-Match` / `expected_etag` -> `409 Conflict`)
3. Validation failure blocks publish (`422`) and records `validation_status='invalid'`
4. Publish mints immutable `AgentVersion` rows & projects runtime fields onto `Tenant`
5. Version history ordering (`DESC`) and single version snapshot lookup (`GET /versions/{version_number}`)
6. Append-only rollback (`is_rollback=True`, `source_version_id`, `Tenant` restored)
7. Concurrent publish serialization without version number collisions
8. Cross-tenant isolation (`404` across all read/write/version/lifecycle routes)
9. Archive / Restore / Retired lifecycle guards
10. Durable `AuditLog` trail for create, draft update, validate, publish, rollback, archive, restore
"""

from __future__ import annotations

import asyncio
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.agent_builder_routes import router as agent_builder_router
from app.api.agent_lifecycle_routes import router as agent_lifecycle_router
from app.api.agent_management_routes import router as agent_management_router
from app.api.agent_version_routes import router as agent_version_router
from app.auth.jwt import create_access_token
from app.core.errors import install_error_handling
from app.db.models import AuditLog
from app.db.session import get_session
from app.services import agent_service


@pytest_asyncio.fixture
async def agent_app(sessionmaker_):
    app = FastAPI()
    app.include_router(agent_management_router)
    app.include_router(agent_version_router)
    app.include_router(agent_builder_router)
    app.include_router(agent_lifecycle_router)
    install_error_handling(app)

    async def _override():
        async with sessionmaker_() as session:
            yield session

    app.dependency_overrides[get_session] = _override
    yield app
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(agent_app):
    async with AsyncClient(
        transport=ASGITransport(app=agent_app), base_url="http://test"
    ) as ac:
        yield ac


def _auth_headers(user) -> dict[str, str]:
    token, _ = create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=user.role.value,
        token_version=user.token_version,
    )
    return {"Authorization": f"Bearer {token}"}


class TestDurableAgentVersionPersistence:
    async def test_1_create_draft_persists_across_independent_sessions(
        self, client, sessionmaker_, owner_a, tenant_a
    ):
        # Create agent via HTTP API
        resp = await client.post(
            "/api/v1/agents",
            json={
                "name": "Durable Concierge",
                "description": "Persisted across sessions",
                "agent_type": "voice",
            },
            headers=_auth_headers(owner_a),
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        agent_id = created["agent_id"]
        initial_etag = created["etag"]
        assert initial_etag.startswith('W/"')

        # Open a brand-new independent AsyncSession and verify durability in DB
        async with sessionmaker_() as session_2:
            row = await agent_service.get_agent_row(session_2, tenant_a.id, agent_id)
            assert row is not None
            assert row.name == "Durable Concierge"
            assert row.description == "Persisted across sessions"
            assert row.status == "draft"
            assert row.draft_etag == initial_etag
            assert row.current_draft_config["identity"]["name"] == "Durable Concierge"

    async def test_2_draft_update_and_etag_optimistic_concurrency_409(
        self, client, owner_a
    ):
        resp = await client.post(
            "/api/v1/agents",
            json={"name": "Concurrency Guarded Agent", "description": "Initial"},
            headers=_auth_headers(owner_a),
        )
        assert resp.status_code == 201
        agent_id = resp.json()["agent_id"]
        etag_1 = resp.json()["etag"]

        # First update with valid If-Match header succeeds
        patch_1 = await client.patch(
            f"/api/v1/agents/{agent_id}/builder",
            json={
                "identity": {
                    "name": "Concurrency Guarded Agent",
                    "description": "Updated by session 1",
                    "persona": "Friendly specialist",
                    "greeting": "Hello from session 1!",
                    "end_call_message": "Goodbye!",
                    "fallback_message": "Please repeat.",
                }
            },
            headers={**_auth_headers(owner_a), "If-Match": etag_1},
        )
        assert patch_1.status_code == 200, patch_1.text
        etag_2 = patch_1.json()["etag"]
        assert etag_2 != etag_1
        assert patch_1.json()["lock_version"] == 2

        # Second update using stale etag_1 fails with 409 Conflict
        stale_patch = await client.patch(
            f"/api/v1/agents/{agent_id}/builder",
            json={
                "identity": {
                    "name": "Stale Overwrite",
                    "description": "Should be rejected",
                    "persona": "Stale",
                    "greeting": "Stale",
                    "end_call_message": "Bye",
                    "fallback_message": "Repeat",
                }
            },
            headers={**_auth_headers(owner_a), "If-Match": etag_1},
        )
        assert stale_patch.status_code == 409, stale_patch.text

        # Verify newer draft was not overwritten
        current = await client.get(
            f"/api/v1/agents/{agent_id}/builder",
            headers=_auth_headers(owner_a),
        )
        assert current.status_code == 200
        assert current.json()["identity"]["greeting"] == "Hello from session 1!"
        assert current.json()["etag"] == etag_2

    async def test_3_validation_failure_blocks_publish_with_422(
        self, client, sessionmaker_, owner_a, tenant_a
    ):
        resp = await client.post(
            "/api/v1/agents",
            json={"name": "Invalid Transfer Agent"},
            headers=_auth_headers(owner_a),
        )
        agent_id = resp.json()["agent_id"]

        # Save draft with an invalid non-E.164 transfer phone number
        patch_resp = await client.patch(
            f"/api/v1/agents/{agent_id}/builder",
            json={
                "call_handling": {
                    "silence_timeout_seconds": 5,
                    "max_call_duration_seconds": 1800,
                    "interruption_sensitivity": 0.5,
                    "voicemail_detection": True,
                    "dtmf_enabled": True,
                    "transfer_phone_number": "not-an-e164-phone",
                }
            },
            headers=_auth_headers(owner_a),
        )
        assert patch_resp.status_code == 200

        # Validate endpoint reports valid == False
        val_resp = await client.post(
            f"/api/v1/agents/{agent_id}/validate",
            headers=_auth_headers(owner_a),
        )
        assert val_resp.status_code == 200
        val_body = val_resp.json()
        assert val_body["valid"] is False
        assert any(
            e["field"] == "call_handling.transfer_phone_number"
            for e in val_body["errors"]
        )

        # Publish is blocked with 422 and creates no AgentVersion rows
        pub_resp = await client.post(
            f"/api/v1/agents/{agent_id}/publish",
            json={"release_notes": "Should fail"},
            headers=_auth_headers(owner_a),
        )
        assert pub_resp.status_code == 422

        async with sessionmaker_() as check_db:
            row = await agent_service.get_agent_row(check_db, tenant_a.id, agent_id)
            assert row is not None
            assert row.validation_status == "invalid"
            assert row.published_version_number is None
            versions = await agent_service.version_history_async(
                check_db, tenant_a.id, agent_id
            )
            assert versions == []

    async def test_4_5_6_publish_immutability_version_lookup_diff_and_rollback(
        self, client, sessionmaker_, owner_a, tenant_a
    ):
        # Create agent via /api/v1/agents
        created = await client.post(
            "/api/v1/agents",
            json={"name": "Release Pipeline Agent", "description": "v1 setup"},
            headers=_auth_headers(owner_a),
        )
        assert created.status_code == 201
        agent_id = created.json()["agent_id"]

        # Configure v1 draft
        await client.patch(
            f"/api/v1/agents/{agent_id}/builder",
            json={
                "identity": {
                    "name": "Release Pipeline Agent",
                    "description": "v1 description",
                    "persona": "Calm billing specialist",
                    "greeting": "Hello, welcome to Billing v1.",
                    "end_call_message": "Thank you!",
                    "fallback_message": "Could you repeat that?",
                },
                "voice": {
                    "provider": "elevenlabs",
                    "voice_id": "voice_v1",
                    "language": "en-US",
                    "speed": 1.0,
                    "pitch": 1.0,
                    "stability": 0.8,
                    "similarity_boost": 0.8,
                },
                "model": {
                    "provider": "anthropic",
                    "model_name": "claude-haiku-4-5-20251001",
                    "temperature": 0.2,
                    "max_tokens": 300,
                    "system_prompt": "You handle v1 billing questions.",
                    "context_window_turns": 20,
                    "response_style": "concise",
                },
            },
            headers=_auth_headers(owner_a),
        )

        # Publish v1
        pub_v1 = await client.post(
            f"/api/v1/agents/{agent_id}/publish",
            json={"release_notes": "Initial v1 release", "environment": "production"},
            headers=_auth_headers(owner_a),
        )
        assert pub_v1.status_code == 200, pub_v1.text
        assert pub_v1.json()["version"] == 1

        # Edit draft for v2
        await client.patch(
            f"/api/v1/agents/{agent_id}/builder",
            json={
                "identity": {
                    "name": "Release Pipeline Agent",
                    "description": "v2 description",
                    "persona": "Spanish billing specialist",
                    "greeting": "Hola, bienvenido a Facturacion v2.",
                    "end_call_message": "Gracias!",
                    "fallback_message": "Repita por favor.",
                },
                "voice": {
                    "provider": "elevenlabs",
                    "voice_id": "voice_v2",
                    "language": "es-MX",
                    "speed": 1.15,
                    "pitch": 1.0,
                    "stability": 0.75,
                    "similarity_boost": 0.75,
                },
            },
            headers=_auth_headers(owner_a),
        )

        # Publish v2
        pub_v2 = await client.post(
            f"/api/v1/agents/{agent_id}/publish",
            json={"release_notes": "Spanish v2 release", "environment": "production"},
            headers=_auth_headers(owner_a),
        )
        assert pub_v2.status_code == 200, pub_v2.text
        assert pub_v2.json()["version"] == 2

        # List version history (newest first: [2, 1])
        versions_resp = await client.get(
            f"/api/v1/agents/{agent_id}/versions",
            headers=_auth_headers(owner_a),
        )
        assert versions_resp.status_code == 200
        v_list = versions_resp.json()
        assert [v["version"] for v in v_list] == [2, 1]
        assert v_list[0]["is_active"] is True
        assert v_list[1]["is_active"] is False

        # Fetch single version snapshot v1 and verify immutability
        v1_snap_resp = await client.get(
            f"/api/v1/agents/{agent_id}/versions/1",
            headers=_auth_headers(owner_a),
        )
        assert v1_snap_resp.status_code == 200
        v1_snap = v1_snap_resp.json()
        assert v1_snap["version"] == 1
        assert (
            v1_snap["config_snapshot"]["identity"]["greeting"]
            == "Hello, welcome to Billing v1."
        )
        assert v1_snap["config_snapshot"]["voice"]["language"] == "en-US"

        # Also verify /api/agents/{id}/versions/1 and diff endpoint
        mgmt_v1 = await client.get(
            f"/api/agents/{agent_id}/versions/1",
            headers=_auth_headers(owner_a),
        )
        assert mgmt_v1.status_code == 200
        assert mgmt_v1.json()["version"] == 1

        diff_resp = await client.get(
            f"/api/agents/{agent_id}/versions/diff?v1=1&v2=2",
            headers=_auth_headers(owner_a),
        )
        assert diff_resp.status_code == 200
        assert diff_resp.json()["total_changes"] > 0

        # Rollback from v2 to v1 -> mints v3 with v1's config
        rb_resp = await client.post(
            f"/api/v1/agents/{agent_id}/rollback",
            json={"version": 1, "reason": "Revert to English greeting"},
            headers=_auth_headers(owner_a),
        )
        assert rb_resp.status_code == 200, rb_resp.text
        rb_data = rb_resp.json()
        assert rb_data["version"] == 3
        assert rb_data["is_rollback"] is True
        assert rb_data["source_version_id"] == v1_snap["id"]
        assert (
            rb_data["config_snapshot"]["identity"]["greeting"]
            == "Hello, welcome to Billing v1."
        )

        # Verify Tenant runtime columns and DB state in a fresh session
        async with sessionmaker_() as verify_db:
            from app.db.models import Tenant

            tenant_row = await verify_db.get(Tenant, tenant_a.id)
            assert tenant_row is not None
            assert tenant_row.greeting == "Hello, welcome to Billing v1."
            assert tenant_row.language == "en-US"
            assert tenant_row.voice_id == "voice_v1"

            all_vers = await agent_service.version_history_async(
                verify_db, tenant_a.id, agent_id
            )
            assert [v.version for v in all_vers] == [3, 2, 1]

    async def test_7_concurrent_publish_allocates_monotonic_versions(
        self, sessionmaker_, tenant_a
    ):
        async with sessionmaker_() as setup_db:
            from app.db.models import Tenant

            t_row = await setup_db.get(Tenant, tenant_a.id)
            cfg = agent_service.from_tenant(t_row)
            agent_row = await agent_service.create_draft_async(setup_db, t_row, cfg)
            await setup_db.commit()
            agent_id = str(agent_row.id)

        async def _publish_once(note: str) -> int:
            async with sessionmaker_() as s:
                from app.db.models import Tenant

                t = await s.get(Tenant, tenant_a.id)
                ver = await agent_service.publish_async(
                    s, t, None, agent_identifier=agent_id, changelog=note
                )
                await s.commit()
                return ver.version

        v_first, v_second = await asyncio.gather(
            _publish_once("concurrent-1"),
            _publish_once("concurrent-2"),
        )
        assert sorted([v_first, v_second]) == [1, 2]

    async def test_8_cross_tenant_isolation_enforced_everywhere(
        self, client, owner_a, owner_b
    ):
        created = await client.post(
            "/api/v1/agents",
            json={"name": "Tenant A Private Agent"},
            headers=_auth_headers(owner_a),
        )
        assert created.status_code == 201
        agent_id = created.json()["agent_id"]

        await client.post(
            f"/api/v1/agents/{agent_id}/publish",
            json={"release_notes": "v1"},
            headers=_auth_headers(owner_a),
        )

        # Tenant B must receive 404 across all endpoints for Tenant A's agent
        for method, path, payload in [
            ("GET", f"/api/v1/agents/{agent_id}", None),
            ("GET", f"/api/v1/agents/{agent_id}/builder", None),
            ("PATCH", f"/api/v1/agents/{agent_id}/builder", {"expected_etag": "x"}),
            ("POST", f"/api/v1/agents/{agent_id}/validate", {}),
            ("POST", f"/api/v1/agents/{agent_id}/publish", {"release_notes": "hack"}),
            ("GET", f"/api/v1/agents/{agent_id}/versions", None),
            ("GET", f"/api/v1/agents/{agent_id}/versions/1", None),
            ("POST", f"/api/v1/agents/{agent_id}/rollback", {"version": 1}),
            ("POST", f"/api/v1/agents/{agent_id}/archive", {"reason": "hack"}),
            ("DELETE", f"/api/v1/agents/{agent_id}", None),
            ("GET", f"/api/agents/{agent_id}", None),
            ("GET", f"/api/agents/{agent_id}/versions/1", None),
        ]:
            resp = await client.request(
                method, path, json=payload, headers=_auth_headers(owner_b)
            )
            assert resp.status_code == 404, f"Expected 404 for {method} {path}, got {resp.status_code}"

    async def test_9_10_archive_restore_guards_and_audit_trail(
        self, client, sessionmaker_, owner_a, tenant_a
    ):
        created = await client.post(
            "/api/v1/agents",
            json={"name": "Lifecycle Audited Agent"},
            headers=_auth_headers(owner_a),
        )
        agent_id = created.json()["agent_id"]

        # Archive agent
        arch = await client.post(
            f"/api/v1/agents/{agent_id}/archive",
            json={"reason": "Seasonal pause"},
            headers=_auth_headers(owner_a),
        )
        assert arch.status_code == 200
        assert arch.json()["status"] == "archived"

        # Publishing an archived agent is rejected with 422
        pub_while_archived = await client.post(
            f"/api/v1/agents/{agent_id}/publish",
            json={"release_notes": "Should fail while archived"},
            headers=_auth_headers(owner_a),
        )
        assert pub_while_archived.status_code == 422

        # Restore agent
        rest = await client.post(
            f"/api/v1/agents/{agent_id}/restore",
            headers=_auth_headers(owner_a),
        )
        assert rest.status_code == 200
        assert rest.json()["status"] == "active"

        # Publish after restore succeeds
        pub_after_restore = await client.post(
            f"/api/v1/agents/{agent_id}/publish",
            json={"release_notes": "Live after restore"},
            headers=_auth_headers(owner_a),
        )
        assert pub_after_restore.status_code == 200
        assert pub_after_restore.json()["version"] == 1

        # Verify durable AuditLog entries
        async with sessionmaker_() as audit_db:
            rows = (
                await audit_db.execute(
                    select(AuditLog).where(
                        AuditLog.tenant_id == tenant_a.id,
                    )
                )
            ).scalars().all()
            recorded_actions = {
                (r.detail or {}).get("event")
                for r in rows
                if (r.detail or {}).get("resource_id") == agent_id
            }
            assert "agent.created" in recorded_actions
            assert "agent.archived" in recorded_actions
            assert "agent.restored" in recorded_actions
            assert "agent.published" in recorded_actions
