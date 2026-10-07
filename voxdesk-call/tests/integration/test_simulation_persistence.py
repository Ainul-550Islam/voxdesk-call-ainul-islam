"""Integration tests for Prompt 3: Version-Pinned Simulation Persistence, Real Intent Classification (No Fake Pass), Web Call Sessions, and Honest Carrier/Provider Failure States."""

from __future__ import annotations

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.jwt import create_access_token
from app.db.models import User, UserRole
from app.db.session import get_session
from app.main import app
from app.services import agent_service
from tests.conftest import make_tenant


async def _make_user_and_headers(session, tenant, role: UserRole = UserRole.ADMIN) -> dict[str, str]:
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=f"sim-admin-{uuid.uuid4().hex[:8]}@example.com",
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
async def test_version_pinning_immutability_and_no_fake_simulation_pass(engine) -> None:
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    try:
        async with maker() as setup_s:
            tenant = await make_tenant(setup_s, name="Simulation Pinning Tenant")
            headers = await _make_user_and_headers(setup_s, tenant)

            # Create Voice Agent and publish v1
            agent_row = await agent_service.create_agent_from_builder_async(
                setup_s,
                tenant.id,
                name="Pinned Support Agent",
                initial_config={
                    "name": "Pinned Support Agent",
                    "system_prompt": "V1_SIGNATURE_PROMPT: Handle billing inquiries.",
                    "greeting": "Hello from v1!",
                    "tools": ["book_appointment"],
                },
            )
            v1_domain = await agent_service.publish_async(
                setup_s, tenant, agent_id=str(agent_row.id), actor="sim-admin"
            )
            assert v1_domain.version == 1

            # Update draft and publish v2
            await agent_service.save_builder_draft_async(
                setup_s,
                tenant.id,
                agent_row.id,
                {
                    "name": "Pinned Support Agent",
                    "system_prompt": "V2_SIGNATURE_PROMPT: Handle technical escalations.",
                    "greeting": "Hello from v2!",
                    "tools": ["book_appointment", "transfer_call"],
                },
            )
            v2_domain = await agent_service.publish_async(
                setup_s, tenant, agent_id=str(agent_row.id), actor="sim-admin"
            )
            assert v2_domain.version == 2
            agent_id = str(agent_row.id)
            await setup_s.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Run LLM Playground pinned to v1 even though v2 is now active!
            run_v1 = await client.post(
                "/api/v1/testing/playground/run",
                headers=headers,
                json={
                    "agent_id": agent_id,
                    "agent_kind": "voice",
                    "agent_version_number": 1,
                    "user_message": "Tell me about your service.",
                    "evaluation_rules": [
                        {
                            "name": "Uses V1 Prompt Signature",
                            "rule_type": "contains",
                            "config": {"substring": "V1_SIGNATURE_PROMPT"},
                        },
                        {
                            "name": "Does Not Use V2 Prompt Signature",
                            "rule_type": "not_contains",
                            "config": {"substring": "V2_SIGNATURE_PROMPT"},
                        },
                    ],
                },
            )
            assert run_v1.status_code == 200, run_v1.text
            body_v1 = run_v1.json()
            assert body_v1["agent_version_number"] == 1
            assert body_v1["status"] == "passed"
            assert "V1_SIGNATURE_PROMPT" in body_v1["pinned_config_snapshot"]["system_prompt"]

            # 2. Verify /api/simulations/{id}/run no longer fakes actual_intent = expected_intent
            fake_check_create = await client.post(
                "/api/simulations",
                headers=headers,
                json={
                    "name": "Mismatch Intent Scenario",
                    "scenario": {
                        "steps": [
                            {
                                "speaker": "user",
                                "text": "Hello, good morning!",
                                "expect_intent": "transfer_to_human",
                            }
                        ]
                    },
                },
            )
            assert fake_check_create.status_code == 201
            sim_mismatch_id = fake_check_create.json()["id"]

            mismatch_run = await client.post(
                f"/api/simulations/{sim_mismatch_id}/run",
                headers=headers,
            )
            assert mismatch_run.status_code == 200
            mismatch_body = mismatch_run.json()
            # Must fail because actual_intent is 'greeting', NOT 'transfer_to_human'!
            assert mismatch_body["status"] == "failed"
            assert mismatch_body["result"]["passed"] is False
            step_check = mismatch_body["evidence"]["checks"][0]
            assert step_check["expected_intent"] == "transfer_to_human"
            assert step_check["actual_intent"] == "greeting"
            assert step_check["passed"] is False

            # 3. Honest error when live LLM provider is required without credentials
            no_mock_res = await client.post(
                "/api/v1/testing/playground/run",
                headers=headers,
                json={
                    "agent_id": agent_id,
                    "agent_kind": "voice",
                    "agent_version_number": 2,
                    "user_message": "Hello",
                    "allow_mock_fallback": False,
                },
            )
            assert no_mock_res.status_code == 200
            no_mock_body = no_mock_res.json()
            assert no_mock_body["status"] == "error"
            assert no_mock_body["error_code"] == "PROVIDER_NOT_CONFIGURED"

            # 4. Honest error / not_run when live telephony carrier is required
            phone_res = await client.post(
                "/api/v1/testing/phone-calls/run",
                headers=headers,
                json={
                    "agent_id": agent_id,
                    "agent_version_number": 2,
                    "to_number": "+14155550123",
                    "require_live_carrier": True,
                },
            )
            assert phone_res.status_code == 201, phone_res.text
            phone_body = phone_res.json()
            assert phone_body["status"] == "error"
            assert phone_body["error_code"] == "TELEPHONY_PROVIDER_UNAVAILABLE"

            # 5. Interactive Web Call session lifecycle (start -> utterance -> interrupt -> complete)
            web_start = await client.post(
                "/api/v1/testing/web-calls/sessions",
                headers=headers,
                json={
                    "agent_id": agent_id,
                    "agent_version_number": 2,
                    "evaluation_rules": [
                        {
                            "name": "Transfer Occurred On Escalation",
                            "rule_type": "transfer_occurred",
                            "config": {"expected": True},
                        }
                    ],
                },
            )
            assert web_start.status_code == 201, web_start.text
            web_run_id = web_start.json()["id"]

            web_utt = await client.post(
                f"/api/v1/testing/web-calls/sessions/{web_run_id}/events",
                headers=headers,
                json={
                    "event": "user_utterance",
                    "text": "Please transfer me to a human representative immediately.",
                },
            )
            assert web_utt.status_code == 200
            assert web_utt.json()["final_output"]["transferred"] is True

            web_int = await client.post(
                f"/api/v1/testing/web-calls/sessions/{web_run_id}/events",
                headers=headers,
                json={"event": "interrupt"},
            )
            assert web_int.status_code == 200
            assert web_int.json()["final_output"]["webrtc_state"] == "INTERRUPTED"

            web_done = await client.post(
                f"/api/v1/testing/web-calls/sessions/{web_run_id}/events",
                headers=headers,
                json={"event": "complete"},
            )
            assert web_done.status_code == 200
            done_body = web_done.json()
            assert done_body["status"] == "passed"
            assert done_body["scorecard_summary"]["status"] == "PASSED"
    finally:
        app.dependency_overrides.clear()
