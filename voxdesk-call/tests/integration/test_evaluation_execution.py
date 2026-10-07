"""Integration tests for Prompt 3: Deterministic & LLM-Judge Evaluation Engine, Rerun Without Call Re-Execution, and Zero-Assertion NO_ASSERTIONS Scorecard."""

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
        email=f"eval-admin-{uuid.uuid4().hex[:8]}@example.com",
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
async def test_zero_assertions_scorecard_and_rerun_evaluation_without_call_rerun(engine) -> None:
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    try:
        async with maker() as setup_s:
            tenant_a = await make_tenant(setup_s, name="Eval Tenant A")
            tenant_b = await make_tenant(setup_s, name="Eval Tenant B")
            headers_a = await _make_user_and_headers(setup_s, tenant_a)
            headers_b = await _make_user_and_headers(setup_s, tenant_b)

            # Create and publish v1 of a Voice Agent for Tenant A
            agent_row = await agent_service.create_agent_from_builder_async(
                setup_s,
                tenant_a.id,
                name="Clinic Booking Voice Agent",
                initial_config={
                    "name": "Clinic Booking Voice Agent",
                    "system_prompt": "You are the clinic scheduling assistant for {{customer_name}}.",
                    "greeting": "Welcome to Dhaka Clinic!",
                    "tools": ["book_appointment"],
                    "llm_provider": "openai",
                    "llm_model": "gpt-4o-mini",
                },
            )
            await agent_service.publish_async(
                setup_s,
                tenant_a,
                agent_id=str(agent_row.id),
                actor="eval-admin",
            )
            agent_id = str(agent_row.id)
            await setup_s.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Create a TestSuite with 0 rules
            s_res = await client.post(
                "/api/v1/testing/suites",
                headers=headers_a,
                json={
                    "name": "Booking QA Suite",
                    "description": "Validates appointment booking and confirmation codes",
                    "pass_policy": "all_passed",
                },
            )
            assert s_res.status_code == 201, s_res.text
            suite_id = s_res.json()["id"]

            # 2. Run a multi-turn simulation linked to suite_id with 0 enabled rules
            sim_res = await client.post(
                "/api/v1/testing/simulations/run",
                headers=headers_a,
                json={
                    "agent_id": agent_id,
                    "agent_kind": "voice",
                    "agent_version_number": 1,
                    "suite_id": suite_id,
                    "mode": "simulation",
                    "input_messages": [
                        "Hello, I need to book an appointment tomorrow at 10:00 AM",
                        "Thank you, goodbye",
                    ],
                    "dynamic_variables": {
                        "customer_name": "Nadia Islam",
                        "preferred_time": "tomorrow at 10:00 AM",
                    },
                    "evaluation_rules": [],
                },
            )
            assert sim_res.status_code == 200, sim_res.text
            run_body = sim_res.json()
            run_id = run_body["id"]
            original_transcript = run_body["transcript_snapshot"]

            # Zero enabled rules -> NO_ASSERTIONS and overall_score is None
            assert run_body["scorecard_summary"]["status"] == "NO_ASSERTIONS"
            assert run_body["scorecard_summary"]["overall_score"] is None
            assert len(run_body["evaluation_results"]) == 0

            # Verify QA scorecard endpoint also reports NO_ASSERTIONS
            qa_res = await client.get(
                f"/api/qa/test-runs/{run_id}/scorecard",
                headers=headers_a,
            )
            assert qa_res.status_code == 200, qa_res.text
            assert qa_res.json()["scorecard"]["status"] == "NO_ASSERTIONS"
            assert qa_res.json()["scorecard"]["overall_score"] is None

            # 3. Add deterministic + LLM judge rules to the suite
            rules_to_create = [
                {
                    "suite_id": suite_id,
                    "name": "Contains Confirmation Code",
                    "rule_type": "contains",
                    "config": {"substring": "APT-2026"},
                },
                {
                    "suite_id": suite_id,
                    "name": "Does Not Leak Secret",
                    "rule_type": "not_contains",
                    "config": {"substring": "STACK_TRACE_ERROR"},
                },
                {
                    "suite_id": suite_id,
                    "name": "Regex Code Format",
                    "rule_type": "regex",
                    "config": {"pattern": r"APT-\d{4}"},
                },
                {
                    "suite_id": suite_id,
                    "name": "JSONPath Booking Status",
                    "rule_type": "json_path_equals",
                    "config": {"path": "$.variables.booking_status", "expected": "confirmed"},
                },
                {
                    "suite_id": suite_id,
                    "name": "JSONPath Customer Name Exists",
                    "rule_type": "json_path_exists",
                    "config": {"path": "$.variables.customer_name"},
                },
                {
                    "suite_id": suite_id,
                    "name": "Booking Tool Called",
                    "rule_type": "tool_called",
                    "config": {"tool_name": "book_appointment"},
                },
                {
                    "suite_id": suite_id,
                    "name": "Cancel Tool Not Called",
                    "rule_type": "tool_not_called",
                    "config": {"tool_name": "cancel_appointment"},
                },
                {
                    "suite_id": suite_id,
                    "name": "No Transfer Occurred",
                    "rule_type": "transfer_occurred",
                    "config": {"expected": False},
                },
                {
                    "suite_id": suite_id,
                    "name": "Runtime Variable Confirmation Code",
                    "rule_type": "variable_equals",
                    "config": {"variable_name": "confirmation_code", "expected": "APT-2026"},
                },
                {
                    "suite_id": suite_id,
                    "name": "Turn Count Max 10",
                    "rule_type": "turn_count_max",
                    "config": {"threshold": 10},
                },
                {
                    "suite_id": suite_id,
                    "name": "Turn Count Min 2",
                    "rule_type": "turn_count_min",
                    "config": {"threshold": 2},
                },
                {
                    "suite_id": suite_id,
                    "name": "Latency Within 5000ms",
                    "rule_type": "latency_ms_max",
                    "config": {"max_ms": 5000},
                },
                {
                    "suite_id": suite_id,
                    "name": "Final State Completed",
                    "rule_type": "final_state_equals",
                    "config": {"expected_state": "completed"},
                },
                {
                    "suite_id": suite_id,
                    "name": "LLM Judge Politeness Rubric",
                    "rule_type": "llm_judge",
                    "config": {
                        "rubric": "Agent politely confirms appointment and says goodbye",
                        "pass_threshold": 0.7,
                    },
                },
            ]
            for r_payload in rules_to_create:
                cr = await client.post(
                    "/api/v1/evaluations/rules",
                    headers=headers_a,
                    json=r_payload,
                )
                assert cr.status_code == 201, cr.text

            # 4. Rerun evaluation on the existing TestRun WITHOUT rerunning the call
            rerun_res = await client.post(
                f"/api/v1/evaluations/runs/{run_id}/rerun",
                headers=headers_a,
                json={"allow_mock_judge": True},
            )
            assert rerun_res.status_code == 200, rerun_res.text
            rerun_body = rerun_res.json()

            # Transcript snapshot is 100% unchanged!
            assert rerun_body["transcript_snapshot"] == original_transcript
            assert rerun_body["status"] == "passed"
            assert rerun_body["scorecard_summary"]["status"] == "PASSED"
            assert rerun_body["scorecard_summary"]["overall_score"] == 100.0
            assert len(rerun_body["evaluation_results"]) == 14

            # 5. Add a failing rule and rerun evaluation -> FAILED_ASSERTION
            fail_rule_res = await client.post(
                "/api/v1/evaluations/rules",
                headers=headers_a,
                json={
                    "suite_id": suite_id,
                    "name": "Must Mention Secret Discount Code",
                    "rule_type": "contains",
                    "config": {"substring": "VIP-9999-DISCOUNT"},
                },
            )
            assert fail_rule_res.status_code == 201

            rerun_fail = await client.post(
                f"/api/v1/evaluations/runs/{run_id}/rerun",
                headers=headers_a,
                json={"allow_mock_judge": True},
            )
            assert rerun_fail.status_code == 200
            rerun_fail_body = rerun_fail.json()
            assert rerun_fail_body["transcript_snapshot"] == original_transcript
            assert rerun_fail_body["status"] == "failed"
            assert rerun_fail_body["scorecard_summary"]["status"] == "FAILED_ASSERTION"
            assert rerun_fail_body["scorecard_summary"]["failed_assertion_count"] == 1

            # 6. Cross-tenant isolation check
            cross_qa = await client.get(
                f"/api/qa/test-runs/{run_id}/scorecard",
                headers=headers_b,
            )
            assert cross_qa.status_code == 404
    finally:
        app.dependency_overrides.clear()
