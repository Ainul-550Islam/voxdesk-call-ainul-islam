"""Tests for converting production calls into PII-redacted regression tests and Conductor real-evidence enforcement (Part 6 / Gate G7)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.db.models import (
    Agent,
    AgentLifecycleStatus,
    AgentVersion,
    AgentVersionStatus,
    Call,
    CallDirection,
    CallStatus,
    ConductorChange,
    ConductorEvidenceSourceEnum,
    ConductorProposal,
    ConductorSession,
    EvaluationRule,
    Speaker,
    TestCase,
    Turn,
)
from app.services import conductor_evidence_service
from app.services.regression_from_calls import create_regression_test_from_call
from tests.conftest import make_tenant


async def _seed_call_with_pii_and_tools(
    db: AsyncSession, tenant_id: uuid.UUID
) -> tuple[Agent, AgentVersion, Call]:
    now = datetime.now(timezone.utc)
    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        external_key=f"agent-{uuid.uuid4().hex[:8]}",
        name="Dental Booking Agent",
        status=AgentLifecycleStatus.PUBLISHED,
        current_draft_config={
            "name": "Dental Booking Agent",
            "system_prompt": "Schedule dental appointments politely.",
            "llm_provider": "openai",
            "llm_model": "gpt-4o-mini",
        },
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
        config_snapshot=dict(agent.current_draft_config),
        config_hash="hash-v1",
        changelog="v1",
        created_at=now,
    )
    db.add(ver)
    agent.published_version_id = ver.id
    await db.flush()

    call = Call(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        agent_id=agent.id,
        agent_version_id=ver.id,
        call_sid=f"CA{uuid.uuid4().hex[:28]}",
        direction=CallDirection.INBOUND,
        from_number="+14155550199",
        to_number="+14155550100",
        status=CallStatus.COMPLETED,
        duration_seconds=95.0,
        intent="book_appointment",
        booked=True,
        escalated=False,
        summary="Caller Jane Doe (jane.doe@example.com, SSN 123-45-6789) booked a Tuesday cleaning.",
        transfer_context={
            "tool_calls": [
                {
                    "tool_name": "book_appointment",
                    "arguments": {
                        "slot": "2026-10-13T14:00:00",
                        "email": "jane.doe@example.com",
                    },
                    "output": {"booked": True},
                }
            ]
        },
        started_at=now,
        ended_at=now,
    )
    db.add(call)
    await db.flush()

    t1 = Turn(
        id=uuid.uuid4(),
        call_id=call.id,
        speaker=Speaker.USER,
        text="Hi, my email is jane.doe@example.com and my SSN is 123-45-6789. Can I book Tuesday at 2pm?",
        created_at=now,
    )
    t2 = Turn(
        id=uuid.uuid4(),
        call_id=call.id,
        speaker=Speaker.ASSISTANT,
        text="I have booked your dental cleaning for Tuesday at 2:00 PM. We will send confirmation to jane.doe@example.com.",
        created_at=now,
    )
    t3 = Turn(
        id=uuid.uuid4(),
        call_id=call.id,
        speaker=Speaker.USER,
        text="Great, my callback number is 415-555-0199. Thank you!",
        created_at=now,
    )
    t4 = Turn(
        id=uuid.uuid4(),
        call_id=call.id,
        speaker=Speaker.ASSISTANT,
        text="You're all set for Tuesday at 2:00 PM. Have a wonderful day!",
        created_at=now,
    )
    db.add_all([t1, t2, t3, t4])
    await db.flush()
    return agent, ver, call


@pytest.mark.asyncio
async def test_create_regression_test_from_real_call_redacts_pii_and_marks_needs_review(
    db: AsyncSession,
):
    """Real call -> TestCase + EvaluationRule rows with PII redacted and needs_review=True."""
    tenant = await make_tenant(db, name="Regression Call Tenant")
    other_tenant = await make_tenant(db, name="Other Tenant")
    _, _, call = await _seed_call_with_pii_and_tools(db, tenant.id)

    case_row = await create_regression_test_from_call(
        db,
        tenant.id,
        call.id,
        redact_pii=True,
    )

    assert isinstance(case_row, TestCase)
    meta = dict(case_row.metadata_json or {})
    assert meta.get("needs_review") is True
    assert meta.get("pii_redacted") is True
    assert meta.get("redaction_count", 0) >= 3
    assert meta.get("source_call_id") == str(call.id)

    # Verify PII was scrubbed from persisted TestCase turns and metadata
    serialized_turns = str(case_row.input_messages)
    assert "jane.doe@example.com" not in serialized_turns
    assert "123-45-6789" not in serialized_turns
    assert "415-555-0199" not in serialized_turns
    assert "[EMAIL]" in serialized_turns
    assert "[SSN]" in serialized_turns
    assert "[PHONE]" in serialized_turns

    # Verify every drafted EvaluationRule is marked needs_review=True
    rules = list(
        (
            await db.execute(
                select(EvaluationRule).where(
                    EvaluationRule.test_case_id == case_row.id,
                    EvaluationRule.tenant_id == tenant.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rules) >= 2
    for rule in rules:
        assert rule.config.get("needs_review") is True

    # Cross-tenant call lookup must fail closed with NotFoundError
    with pytest.raises(NotFoundError):
        await create_regression_test_from_call(
            db,
            other_tenant.id,
            call.id,
        )


@pytest.mark.asyncio
async def test_conductor_rejects_fabricated_call_evidence_and_applies_real_evidence_to_draft(
    db: AsyncSession,
):
    """Conductor rejects fabricated call IDs / turn IDs / quotes and applies verified proposals to AgentVersion drafts."""
    tenant = await make_tenant(db, name="Conductor Evidence Tenant")
    agent, ver, call = await _seed_call_with_pii_and_tools(db, tenant.id)

    now = datetime.now(timezone.utc)
    cond_session = ConductorSession(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        agent_id=str(agent.id),
        agent_kind="voice",
        starting_agent_version_id=ver.id,
        starting_version_number=1,
        request_text="Confirm callback number before closing",
        created_at=now,
        updated_at=now,
    )
    db.add(cond_session)
    await db.flush()

    proposal = ConductorProposal(
        id=uuid.uuid4(),
        session_id=cond_session.id,
        tenant_id=tenant.id,
        agent_id=str(agent.id),
        agent_kind="voice",
        base_agent_version_id=ver.id,
        base_version_number=1,
        base_config_hash="hash-v1",
        base_config_snapshot=dict(ver.config_snapshot),
        request_text="Confirm callback number before closing",
        summary="Update prompt based on real call transcript",
        rationale="Observed caller providing callback number at end of call",
        status="APPROVED",
        created_at=now,
        updated_at=now,
    )
    db.add(proposal)
    change = ConductorChange(
        id=uuid.uuid4(),
        proposal_id=proposal.id,
        tenant_id=tenant.id,
        sequence=0,
        section="prompt",
        path="system_prompt",
        operation="replace",
        old_value="Schedule dental appointments politely.",
        new_value="Schedule dental appointments politely and confirm the callback phone number.",
        reason="Anchored in real call",
        risk_level="low",
        approval_state="approved",
        created_at=now,
    )
    db.add(change)
    await db.flush()

    # 1. Fabricated Call UUID that does not exist in DB -> rejected!
    with pytest.raises(BadRequestError, match="Fabricated call evidence rejected"):
        await conductor_evidence_service.record_proposal_evidence(
            db,
            tenant_id=tenant.id,
            proposal_id=proposal.id,
            source_type=ConductorEvidenceSourceEnum.CALL,
            source_id=str(uuid.uuid4()),
            evidence_summary="Fabricated non-existent call",
        )

    # 2. Real Call ID but fabricated transcript quote that never occurred -> rejected!
    with pytest.raises(BadRequestError, match="Fabricated call evidence rejected"):
        await conductor_evidence_service.record_proposal_evidence(
            db,
            tenant_id=tenant.id,
            proposal_id=proposal.id,
            source_type=ConductorEvidenceSourceEnum.CALL,
            source_id=str(call.id),
            evidence_summary="Fabricated quote on real call",
            evidence_payload={
                "cited_quote": "I want to cancel my mortgage account immediately",
            },
        )

    # 3. Attempting to apply proposal before any real evidence is attached -> rejected!
    with pytest.raises(BadRequestError, match="must cite at least one verified real Call"):
        await conductor_evidence_service.apply_evidence_backed_proposal_to_draft(
            db,
            tenant_id=tenant.id,
            proposal_id=proposal.id,
        )

    # 4. Attach real Call evidence with an authentic quote from the call's persisted turns
    ev = await conductor_evidence_service.record_proposal_evidence(
        db,
        tenant_id=tenant.id,
        proposal_id=proposal.id,
        source_type=ConductorEvidenceSourceEnum.CALL,
        source_id=str(call.id),
        evidence_summary="Caller provided callback number on turn 3",
        evidence_payload={
            "cited_quote": "callback number is 415-555-0199",
        },
    )
    assert ev.source_id == str(call.id)

    # 5. Apply to AgentVersion draft -> creates a new DRAFT AgentVersion row
    applied = await conductor_evidence_service.apply_evidence_backed_proposal_to_draft(
        db,
        tenant_id=tenant.id,
        proposal_id=proposal.id,
    )
    assert applied["status"] == "draft"
    assert applied["version_number"] == 2
    assert applied["verified_evidence_count"] == 1
    assert "confirm the callback phone number" in applied["config_snapshot"]["system_prompt"]
