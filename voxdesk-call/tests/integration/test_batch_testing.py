"""Integration tests for Prompt 3: Batch Test Suite Execution, Honest Status Aggregation, and Agent Version QA Comparison."""

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
        email=f"batch-admin-{uuid.uuid4().hex[:8]}@example.com",
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
async def test_batch_suite_execution_and_version_qa_comparison(engine) -> None:
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    try:
        async with maker() as setup_s:
            tenant = await make_tenant(setup_s, name="Batch QA Tenant")
            headers = await _make_user_and_headers(setup_s, tenant)

            agent_row = await agent_service.create_agent_from_builder_async(
                setup_s,
                tenant.id,
                name="Batch Booking Agent",
                initial_config={
                    "name": "Batch Booking Agent",
                    "system_prompt": "You book appointments and provide confirmation code APT-2026.",
                    "greeting": "Welcome to Batch Booking!",
                    "tools": ["book_appointment"],
                },
            )
            await agent_service.publish_async(
                setup_s, tenant, agent_id=str(agent_row.id), actor="batch-admin"
            )
            await agent_service.save_builder_draft_async(
                setup_s,
                tenant.id,
                agent_row.id,
                {
                    "name": "Batch Booking Agent",
                    "system_prompt": "You book appointments with enhanced v2 confirmation.",
                    "greeting": "Welcome to Batch Booking v2!",
                    "tools": ["book_appointment", "transfer_call"],
                },
            )
            await agent_service.publish_async(
                setup_s, tenant, agent_id=str(agent_row.id), actor="batch-admin"
            )
            agent_id = str(agent_row.id)
            await setup_s.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Create TestSuite
            suite_res = await client.post(
                "/api/v1/testing/suites",
                headers=headers,
                json={
                    "name": "Nightly Regression Suite",
                    "description": "Runs booking + negative assertion cases",
                    "pass_policy": "all_passed",
                    "min_pass_score": 100.0,
                },
            )
            assert suite_res.status_code == 201, suite_res.text
            suite_id = suite_res.json()["id"]

            # 2. Create Passing TestCase pinned to v1
            case1_res = await client.post(
                "/api/v1/testing/cases",
                headers=headers,
                json={
                    "suite_id": suite_id,
                    "agent_id": agent_id,
                    "agent_kind": "voice",
                    "agent_version_number": 1,
                    "name": "Passing Booking Case",
                    "mode": "simulation",
                    "input_messages": [
                        "Hello, please book an appointment tomorrow at 10:00 AM"
                    ],
                    "dynamic_variables": {"customer_name": "Tanvir Ahmed"},
                    "expected_rules": [
                        {
                            "name": "Booking Confirmation Code",
                            "rule_type": "contains",
                            "config": {"substring": "APT-2026"},
                        },
                        {
                            "name": "Tool Called",
                            "rule_type": "tool_called",
                            "config": {"tool_name": "book_appointment"},
                        },
                    ],
                },
            )
            assert case1_res.status_code == 201, case1_res.text

            # 3. Create Failing TestCase pinned to v1
            case2_res = await client.post(
                "/api/v1/testing/cases",
                headers=headers,
                json={
                    "suite_id": suite_id,
                    "agent_id": agent_id,
                    "agent_kind": "voice",
                    "agent_version_number": 1,
                    "name": "Failing Assertion Case",
                    "mode": "simulation",
                    "input_messages": ["What are your pricing plans?"],
                    "expected_rules": [
                        {
                            "name": "Impossible Substring",
                            "rule_type": "contains",
                            "config": {"substring": "IMPOSSIBLE_TOKEN_XYZ_999"},
                        }
                    ],
                },
            )
            assert case2_res.status_code == 201, case2_res.text

            # 4. Run Batch Suite -> overall_status MUST be 'failed' (1 passed, 1 failed)
            batch_res = await client.post(
                f"/api/v1/testing/suites/{suite_id}/run-batch",
                headers=headers,
                json={},
            )
            assert batch_res.status_code == 200, batch_res.text
            batch_body = batch_res.json()
            assert batch_body["total_cases"] == 2
            assert batch_body["passed_count"] == 1
            assert batch_body["failed_count"] == 1
            assert batch_body["overall_status"] == "failed"
            assert batch_body["average_score"] == 50.0
            assert len(batch_body["runs"]) == 2

            # 5. Run Case 1 with version override = 2 and compare v1 vs v2 QA
            case1_id = case1_res.json()["id"]
            v2_run = await client.post(
                f"/api/v1/testing/cases/{case1_id}/run",
                headers=headers,
                json={"agent_version_override": 2},
            )
            assert v2_run.status_code == 200
            assert v2_run.json()["agent_version_number"] == 2
            assert v2_run.json()["status"] == "passed"

            cmp_res = await client.get(
                f"/api/qa/agents/{agent_id}/compare-versions?version_a=1&version_b=2",
                headers=headers,
            )
            assert cmp_res.status_code == 200, cmp_res.text
            cmp_body = cmp_res.json()
            assert cmp_body["version_a"]["total_runs"] == 2
            assert cmp_body["version_a"]["average_score"] == 50.0
            assert cmp_body["version_b"]["total_runs"] == 1
            assert cmp_body["version_b"]["average_score"] == 100.0
            assert cmp_body["score_delta"] == 50.0
    finally:
        app.dependency_overrides.clear()
