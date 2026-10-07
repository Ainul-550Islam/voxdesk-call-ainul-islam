"""Integration tests for Prompt 4: Conductor AI Control Plane — Propose, Diff, Validate, Simulate, Review (Accept/Reject/Undo), and Safe Transactional Apply creating a new immutable AgentVersion."""

from __future__ import annotations

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.jwt import create_access_token
from app.db.models import (
    AgentVersion,
    ConductorApproval,
    ConductorChange,
    ConductorEvidence,
    ConductorProposal,
    ConductorSession,
    TestCase,
    TestRun,
    User,
    UserRole,
)
from app.db.session import get_session
from app.main import app
from app.services import agent_service
from tests.conftest import make_tenant


async def _make_user_and_headers(
    session, tenant, role: UserRole = UserRole.ADMIN
) -> dict[str, str]:
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=f"conductor-{role.value}-{uuid.uuid4().hex[:8]}@example.com",
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
async def test_conductor_full_workflow_partial_approve_simulate_and_immutable_apply(
    engine,
) -> None:
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    try:
        async with maker() as setup_s:
            tenant = await make_tenant(setup_s, name="Conductor Workflow Tenant")
            headers = await _make_user_and_headers(setup_s, tenant)

            agent_row = await agent_service.create_agent_from_builder_async(
                setup_s,
                tenant.id,
                name="Clinic Reception Agent",
                initial_config={
                    "name": "Clinic Reception Agent",
                    "system_prompt": "You are a polite receptionist for Dhaka Clinic.",
                    "greeting": "Hello! Thank you for calling Dhaka Clinic.",
                    "voice_speed": 1.0,
                    "temperature": 0.4,
                    "tools": ["book_appointment"],
                },
            )
            v1_domain = await agent_service.publish_async(
                setup_s, tenant, agent_id=str(agent_row.id), actor="conductor-admin"
            )
            assert v1_domain.version == 1
            agent_id = str(agent_row.id)
            await setup_s.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Create Conductor Session
            sess_res = await client.post(
                "/api/v1/conductor/sessions",
                headers=headers,
                json={
                    "agent_id": agent_id,
                    "agent_kind": "voice",
                    "base_version_number": 1,
                    "origin_surface": "agent_builder",
                    "request_text": "Review clinic agent and improve greeting and speed.",
                },
            )
            assert sess_res.status_code == 201, sess_res.text
            sess_data = sess_res.json()
            session_id = sess_data["id"]
            assert sess_data["starting_version_number"] == 1
            assert sess_data["status"] == "ACTIVE"

            # 2. Submit Conductor Request with multi-field operations
            prop_res = await client.post(
                "/api/v1/conductor/proposals",
                headers=headers,
                json={
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "base_version_number": 1,
                    "request_text": (
                        'Change greeting to "Welcome to Dhaka Specialty Clinic! How may I help?", '
                        "slow down voice speed to 0.92, and lower temperature to 0.2."
                    ),
                    "auto_validate": True,
                    "auto_simulate": False,
                },
            )
            assert prop_res.status_code == 201, prop_res.text
            proposal = prop_res.json()
            proposal_id = proposal["id"]
            assert proposal["base_version_number"] == 1
            assert proposal["validation_status"] == "valid"
            assert proposal["status"] == "READY_FOR_REVIEW"
            assert proposal["production_published"] is False
            assert len(proposal["changes"]) >= 3

            # Verify no AgentVersion v2 was auto-created merely by proposing!
            versions_res = await client.get(
                f"/api/agents/{agent_id}/versions", headers=headers
            )
            assert versions_res.status_code == 200
            versions_list = versions_res.json()
            assert len(versions_list) == 1

            # 3. Inspect Deterministic Diff
            diff_res = await client.get(
                f"/api/v1/conductor/proposals/{proposal_id}/diff",
                headers=headers,
            )
            assert diff_res.status_code == 200, diff_res.text
            diff_data = diff_res.json()
            assert diff_data["base_version_number"] == 1
            assert diff_data["total_changes"] == len(proposal["changes"])
            assert diff_data["base_config_hash"] != diff_data["candidate_config_hash"]
            assert len(diff_data["diff_hash"]) == 64

            # Re-fetch diff to prove deterministic hashing
            diff_res_2 = await client.get(
                f"/api/v1/conductor/proposals/{proposal_id}/diff",
                headers=headers,
            )
            assert diff_res_2.json()["diff_hash"] == diff_data["diff_hash"]

            # 4. Run Candidate Simulation + Reproduction TestCase creation
            sim_res = await client.post(
                f"/api/v1/conductor/proposals/{proposal_id}/simulate",
                headers=headers,
                json={
                    "input_messages": [
                        "Hello, I would like to book an appointment with Dr. Rahman."
                    ],
                    "persist_reproduction_test_case": True,
                    "test_case_name": "Conductor Clinic Greeting Verification",
                },
            )
            assert sim_res.status_code == 200, sim_res.text
            sim_prop = sim_res.json()
            assert sim_prop["simulation_status"] in ("passed", "failed")
            assert sim_prop["simulation_summary"]["test_run_id"]
            assert sim_prop["simulation_summary"]["test_case_id"]

            # 5. Granular Human Review: Approve greeting + voice_speed, Reject temperature, test Undo
            changes_by_path = {c["path"]: c for c in sim_prop["changes"]}
            assert "greeting" in changes_by_path
            assert "voice_speed" in changes_by_path
            assert "temperature" in changes_by_path

            greeting_chg_id = changes_by_path["greeting"]["id"]
            speed_chg_id = changes_by_path["voice_speed"]["id"]
            temp_chg_id = changes_by_path["temperature"]["id"]

            # Approve greeting
            app_g = await client.post(
                f"/api/v1/conductor/proposals/{proposal_id}/changes/{greeting_chg_id}/approve",
                headers=headers,
                json={"reason": "Approved clearer clinic greeting"},
            )
            assert app_g.status_code == 200
            assert app_g.json()["status"] == "PARTIALLY_APPROVED"

            # Approve voice_speed then undo then re-approve
            app_s = await client.post(
                f"/api/v1/conductor/proposals/{proposal_id}/changes/{speed_chg_id}/approve",
                headers=headers,
                json={"reason": "Approved slower speaking rate"},
            )
            assert app_s.status_code == 200

            undo_s = await client.post(
                f"/api/v1/conductor/proposals/{proposal_id}/changes/{speed_chg_id}/undo",
                headers=headers,
                json={"reason": "Double checking speed value"},
            )
            assert undo_s.status_code == 200
            undone_speed = next(
                c for c in undo_s.json()["changes"] if c["id"] == speed_chg_id
            )
            assert undone_speed["approval_state"] == "pending"

            reapp_s = await client.post(
                f"/api/v1/conductor/proposals/{proposal_id}/changes/{speed_chg_id}/approve",
                headers=headers,
                json={"reason": "Re-approved 0.92 speed"},
            )
            assert reapp_s.status_code == 200

            # Reject temperature change (and any remaining changes)
            for chg in reapp_s.json()["changes"]:
                if chg["id"] not in (greeting_chg_id, speed_chg_id):
                    rej_r = await client.post(
                        f"/api/v1/conductor/proposals/{proposal_id}/changes/{chg['id']}/reject",
                        headers=headers,
                        json={"reason": "Keep existing temperature"},
                    )
                    assert rej_r.status_code == 200

            # 6. Apply Approved Subset -> Creates NEW Immutable AgentVersion v2
            apply_res = await client.post(
                f"/api/v1/conductor/proposals/{proposal_id}/apply",
                headers=headers,
                json={
                    "idempotency_key": "apply-idem-key-001",
                    "expected_base_version_number": 1,
                    "version_notes": "Applied Conductor greeting and voice speed improvements",
                },
            )
            assert apply_res.status_code == 200, apply_res.text
            apply_data = apply_res.json()
            assert apply_data["base_version_number"] == 1
            assert apply_data["resulting_version_number"] == 2
            assert apply_data["production_published"] is False
            assert apply_data["ready_to_publish"] is True
            assert apply_data["idempotent_replay"] is False
            assert set(apply_data["applied_change_ids"]) == {
                greeting_chg_id,
                speed_chg_id,
            }
            assert temp_chg_id in apply_data["skipped_change_ids"]

            new_v2_snap = apply_data["resulting_version"]["config_snapshot"]
            assert (
                new_v2_snap["greeting"]
                == "Welcome to Dhaka Specialty Clinic! How may I help?"
            )
            assert float(new_v2_snap["voice_speed"]) == 0.92
            # Rejected temperature change MUST NOT be applied!
            assert float(new_v2_snap["temperature"]) == 0.4

            # 7. Idempotent Replay with same idempotency_key does NOT create v3
            replay_res = await client.post(
                f"/api/v1/conductor/proposals/{proposal_id}/apply",
                headers=headers,
                json={
                    "idempotency_key": "apply-idem-key-001",
                    "expected_base_version_number": 1,
                },
            )
            assert replay_res.status_code == 200
            replay_data = replay_res.json()
            assert replay_data["idempotent_replay"] is True
            assert replay_data["resulting_version_number"] == 2

            # 8. Verify Base AgentVersion v1 is untouched & immutable
            v1_check = await client.get(
                f"/api/agents/{agent_id}/versions/1", headers=headers
            )
            assert v1_check.status_code == 200
            v1_snap = v1_check.json()["config_snapshot"]
            assert v1_snap["greeting"] == "Hello! Thank you for calling Dhaka Clinic."
            assert float(v1_snap["voice_speed"]) == 1.0

        # 9. Verify Database Durability Across Fresh DB Session (Simulated Restart)
        async with maker() as verify_s:
            sess_row = await verify_s.get(ConductorSession, uuid.UUID(session_id))
            prop_row = await verify_s.get(ConductorProposal, uuid.UUID(proposal_id))
            assert sess_row is not None
            assert prop_row is not None
            assert prop_row.status == "APPLIED"
            assert prop_row.resulting_version_number == 2

            chg_rows = list(
                (
                    await verify_s.execute(
                        select(ConductorChange).where(
                            ConductorChange.proposal_id == prop_row.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(chg_rows) >= 3

            app_rows = list(
                (
                    await verify_s.execute(
                        select(ConductorApproval).where(
                            ConductorApproval.proposal_id == prop_row.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(app_rows) >= 4

            ev_rows = list(
                (
                    await verify_s.execute(
                        select(ConductorEvidence).where(
                            ConductorEvidence.proposal_id == prop_row.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(ev_rows) >= 2

            tc_row = await verify_s.get(
                TestCase,
                uuid.UUID(sim_prop["simulation_summary"]["test_case_id"]),
            )
            tr_row = await verify_s.get(
                TestRun,
                uuid.UUID(sim_prop["simulation_summary"]["test_run_id"]),
            )
            assert tc_row is not None
            assert tr_row is not None

            v_rows = list(
                (
                    await verify_s.execute(
                        select(AgentVersion).where(
                            AgentVersion.agent_id == uuid.UUID(agent_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(v_rows) == 2
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_conductor_stale_base_version_conflict_blocks_overwrite(engine) -> None:
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_session():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    try:
        async with maker() as setup_s:
            tenant = await make_tenant(setup_s, name="Conductor Stale Conflict Tenant")
            headers = await _make_user_and_headers(setup_s, tenant)

            agent_row = await agent_service.create_agent_from_builder_async(
                setup_s,
                tenant.id,
                name="Conflict Guard Agent",
                initial_config={
                    "name": "Conflict Guard Agent",
                    "system_prompt": "Initial v1 prompt.",
                    "greeting": "Initial v1 greeting.",
                },
            )
            await agent_service.publish_async(
                setup_s, tenant, agent_id=str(agent_row.id), actor="admin"
            )
            agent_id = str(agent_row.id)
            await setup_s.commit()

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Create two concurrent proposals both pinned to base_version_number = 1
            prop_a_res = await client.post(
                "/api/v1/conductor/proposals",
                headers=headers,
                json={
                    "agent_id": agent_id,
                    "base_version_number": 1,
                    "request_text": 'Change greeting to "Greeting from Proposal A"',
                },
            )
            assert prop_a_res.status_code == 201
            prop_a_id = prop_a_res.json()["id"]

            prop_b_res = await client.post(
                "/api/v1/conductor/proposals",
                headers=headers,
                json={
                    "agent_id": agent_id,
                    "base_version_number": 1,
                    "request_text": 'Change greeting to "Greeting from Proposal B"',
                },
            )
            assert prop_b_res.status_code == 201
            prop_b_id = prop_b_res.json()["id"]

            # Approve both proposals
            await client.post(
                f"/api/v1/conductor/proposals/{prop_a_id}/approve",
                headers=headers,
                json={"reason": "Approve A"},
            )
            await client.post(
                f"/api/v1/conductor/proposals/{prop_b_id}/approve",
                headers=headers,
                json={"reason": "Approve B"},
            )

            # Apply Proposal A -> succeeds and advances agent to v2
            apply_a = await client.post(
                f"/api/v1/conductor/proposals/{prop_a_id}/apply",
                headers=headers,
                json={"expected_base_version_number": 1},
            )
            assert apply_a.status_code == 200
            assert apply_a.json()["resulting_version_number"] == 2

            # Apply Proposal B (still pinned to v1) -> MUST fail with HTTP 409 STALE conflict!
            apply_b = await client.post(
                f"/api/v1/conductor/proposals/{prop_b_id}/apply",
                headers=headers,
                json={"expected_base_version_number": 1},
            )
            assert apply_b.status_code == 409, apply_b.text

            # Verify Proposal B is now marked STALE
            prop_b_after = await client.get(
                f"/api/v1/conductor/proposals/{prop_b_id}",
                headers=headers,
            )
            assert prop_b_after.status_code == 200
            assert prop_b_after.json()["status"] == "STALE"
    finally:
        app.dependency_overrides.clear()
