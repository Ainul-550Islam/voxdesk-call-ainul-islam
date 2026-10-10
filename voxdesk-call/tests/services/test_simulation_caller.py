"""Tests for LLM-played SimulatedCaller, criteria judging, and KPI mock exclusion (Part 6 / Gate G7)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Agent,
    AgentLifecycleStatus,
    AgentVersion,
    AgentVersionStatus,
    TestRun,
)
from app.services import simulation_service
from app.services.simulation_caller import SimulatedCaller
from tests.conftest import make_tenant


async def _create_published_voice_agent(
    db: AsyncSession, tenant_id: uuid.UUID
) -> tuple[Agent, AgentVersion]:
    now = datetime.now(timezone.utc)
    agent_id = uuid.uuid4()
    snapshot = {
        "name": "Receptionist Alpha",
        "system_prompt": "You are a dental office receptionist. Help callers schedule or reschedule appointments.",
        "greeting": "Hello! Thank you for calling Bright Smile Dental. How can I help you today?",
        "llm_provider": "openai",
        "llm_model": "gpt-4o-mini",
        "temperature": 0.2,
    }
    agent = Agent(
        id=agent_id,
        tenant_id=tenant_id,
        external_key=f"agent-{agent_id.hex[:8]}",
        name="Receptionist Alpha",
        status=AgentLifecycleStatus.PUBLISHED,
        current_draft_config=snapshot,
        published_version_number=1,
        created_at=now,
        updated_at=now,
    )
    db.add(agent)
    await db.flush()

    ver = AgentVersion(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent.id,
        version_number=1,
        status=AgentVersionStatus.PUBLISHED,
        config_snapshot=snapshot,
        config_hash="hash-v1",
        changelog="Initial publish",
        created_at=now,
    )
    db.add(ver)
    agent.published_version_id = ver.id
    await db.flush()
    return agent, ver


@pytest.mark.asyncio
async def test_simulated_caller_deterministic_seed(db: AsyncSession):
    """Two SimulatedCaller instances with the same seed, persona, goal, and variables produce identical turns."""
    tenant = await make_tenant(db, name="Sim Seed Tenant")

    caller_a = SimulatedCaller(
        persona="Busy parent needing a Tuesday afternoon cleaning",
        goal="Schedule a dental cleaning for Tuesday at 2pm",
        variables={"patient_name": "Alex Rivera", "preferred_day": "Tuesday"},
        interruption_style="polite",
        seed=1337,
        max_turns=3,
    )
    caller_b = SimulatedCaller(
        persona="Busy parent needing a Tuesday afternoon cleaning",
        goal="Schedule a dental cleaning for Tuesday at 2pm",
        variables={"patient_name": "Alex Rivera", "preferred_day": "Tuesday"},
        interruption_style="polite",
        seed=1337,
        max_turns=3,
    )
    caller_diff_seed = SimulatedCaller(
        persona="Busy parent needing a Tuesday afternoon cleaning",
        goal="Schedule a dental cleaning for Tuesday at 2pm",
        variables={"patient_name": "Alex Rivera", "preferred_day": "Tuesday"},
        interruption_style="impatient",
        seed=9999,
        max_turns=3,
    )

    turn_a0 = await caller_a.generate_next_turn(
        db,
        tenant=tenant,
        turn_index=0,
        agent_message="Hello! How can I help you?",
        allow_mock_fallback=True,
    )
    turn_b0 = await caller_b.generate_next_turn(
        db,
        tenant=tenant,
        turn_index=0,
        agent_message="Hello! How can I help you?",
        allow_mock_fallback=True,
    )
    turn_diff0 = await caller_diff_seed.generate_next_turn(
        db,
        tenant=tenant,
        turn_index=0,
        agent_message="Hello! How can I help you?",
        allow_mock_fallback=True,
    )

    assert turn_a0["content"] == turn_b0["content"]
    assert turn_a0["seed"] == 1337
    assert turn_diff0["content"].startswith("[Interrupting]")
    assert turn_a0["content"] != turn_diff0["content"]


@pytest.mark.asyncio
async def test_simulated_caller_run_judges_goal_and_criteria(db: AsyncSession):
    """execute_simulated_caller_run runs against a published AgentVersion and records judge verdict & rationale."""
    tenant = await make_tenant(db, name="Sim Judge Tenant")
    agent, _ = await _create_published_voice_agent(db, tenant.id)

    run_resp = await simulation_service.execute_simulated_caller_run(
        db,
        tenant.id,
        agent_id=str(agent.id),
        agent_kind="voice",
        agent_version_number=1,
        persona="Polite patient looking to book Tuesday afternoon",
        goal="Book a dental cleaning for Tuesday afternoon",
        variables={"patient_name": "Jordan Lee"},
        interruption_style="none",
        seed=42,
        max_turns=3,
        success_criteria=[
            "Agent responds to the caller request",
            "Conversation stays relevant to scheduling",
        ],
        allow_mock_fallback=True,
    )

    assert len(run_resp.transcript_snapshot) >= 3
    assert run_resp.agent_version_number == 1
    assert run_resp.is_mock_provider is True
    assert len(run_resp.evaluation_results) >= 2
    assert "judge_verdict" in run_resp.final_output
    verdict = run_resp.final_output["judge_verdict"]
    assert isinstance(verdict["rationale"], str)
    assert len(verdict["rationale"]) > 0


@pytest.mark.asyncio
async def test_mock_runs_excluded_from_simulation_kpis(db: AsyncSession):
    """compute_simulation_kpis excludes is_mock_provider=True runs from pass_rate and kpi_eligible_runs."""
    tenant = await make_tenant(db, name="Sim KPI Tenant")
    agent, ver = await _create_published_voice_agent(db, tenant.id)

    # 1. Execute a mocked run (is_mock_provider=True) -> must be excluded from KPIs
    mock_run = await simulation_service.execute_simulated_caller_run(
        db,
        tenant.id,
        agent_id=str(agent.id),
        agent_version_number=1,
        persona="Test caller",
        goal="Ask office hours",
        seed=10,
        max_turns=2,
        success_criteria=["Respond politely"],
        allow_mock_fallback=True,
    )
    assert mock_run.is_mock_provider is True

    # 2. Persist two non-mocked (live) TestRun rows: 1 passed, 1 failed
    now = datetime.now(timezone.utc)
    live_pass = TestRun(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        suite_id=None,
        test_case_id=mock_run.test_case_id,
        agent_id=str(agent.id),
        agent_kind="voice",
        agent_version_id=ver.id,
        agent_version_number=1,
        agent_config_hash="hash-v1",
        status="passed",
        is_mock_provider=False,
        provider="openai",
        model="gpt-4o-mini",
        transcript_snapshot=[{"turn_index": 0, "role": "user", "content": "hi"}],
        scorecard_summary={"overall_score": 1.0, "status": "PASSED"},
        duration_ms=250,
        started_at=now,
        completed_at=now,
        created_at=now,
    )
    live_fail = TestRun(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        suite_id=None,
        test_case_id=mock_run.test_case_id,
        agent_id=str(agent.id),
        agent_kind="voice",
        agent_version_id=ver.id,
        agent_version_number=1,
        agent_config_hash="hash-v1",
        status="failed",
        is_mock_provider=False,
        provider="openai",
        model="gpt-4o-mini",
        transcript_snapshot=[{"turn_index": 0, "role": "user", "content": "hi"}],
        scorecard_summary={"overall_score": 0.0, "status": "FAILED_ASSERTION"},
        duration_ms=300,
        started_at=now,
        completed_at=now,
        created_at=now,
    )
    db.add_all([live_pass, live_fail])
    await db.flush()

    kpis = await simulation_service.compute_simulation_kpis(
        db, tenant.id, agent_id=str(agent.id)
    )
    assert kpis["total_runs"] == 3
    assert kpis["mock_runs_excluded"] == 1
    assert kpis["kpi_eligible_runs"] == 2
    assert kpis["passed_count"] == 1
    assert kpis["failed_count"] == 1
    assert kpis["pass_rate"] == 50.0
