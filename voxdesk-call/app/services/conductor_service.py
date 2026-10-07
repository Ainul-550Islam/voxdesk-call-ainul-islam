"""Main Durable Conductor Orchestration Service (Prompt 4).

Coordinates the full Conductor lifecycle:
- Session creation & retrieval with permission-derived context assembly
- Natural-language or structured proposal synthesis
- Deterministic structured & line diffing
- Candidate configuration validation
- Real simulation & evaluation via Prompt 3 (``simulation_service`` + ``evaluation_service``)
  without overwriting the Agent's persisted draft
- Reproduction ``TestCase`` persistence for failed-call improvement flows
- Granular human review (approve/reject/undo per change or per proposal)
- Transactional apply creating a new immutable ``AgentVersion`` without auto-deploying production
"""

from __future__ import annotations

import copy
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, NotFoundError
from app.db.models import (
    ConductorApprovalActionEnum,
    ConductorApprovalStateEnum,
    ConductorChange,
    ConductorEvidenceSourceEnum,
    ConductorProposal,
    ConductorProposalStatusEnum,
    ConductorSession,
    ConductorSessionStatusEnum,
)
from app.domain.conductor_diff import (
    apply_operations_to_snapshot,
    build_proposal_diff,
    canonical_config_hash,
    get_value_at_path,
)
from app.domain.conductor_models import (
    ConductorApplyProposalRequest,
    ConductorApplyResultResponse,
    ConductorPromptRequest,
    ConductorProposalDiffResponse,
    ConductorProposalResponse,
    ConductorProposalStatus,
    ConductorSessionCreateRequest,
    ConductorSessionResponse,
    ConductorSimulateProposalRequest,
    enforce_proposal_transition,
)
from app.domain.evaluation_models import (
    AgentKind,
    EvaluationRuleType,
    InlineEvaluationRuleSpec,
    TestCaseCreateRequest,
    TestRunMode,
)
from app.governance.audit import record_conductor_audit
from app.security.policy import classify_change_risk, classify_path_section
from app.services import simulation_service
from app.services.conductor_apply_service import (
    _load_existing_resulting_version_dict,
    apply_approved_proposal,
)
from app.services.conductor_approval_service import (
    approve_proposal_changes,
    reject_proposal_changes,
    review_single_change,
    undo_proposal_approvals,
)
from app.services.conductor_context_service import (
    assemble_conductor_context,
    resolve_authoritative_agent_baseline,
)
from app.services.conductor_evidence_service import (
    list_proposal_evidence as list_proposal_evidence,
    record_proposal_evidence,
)
from app.services.conductor_proposal_service import (
    build_proposal_response,
    check_and_mark_stale_if_needed,
    compute_risk_summary,
    get_proposal_row,
    synthesize_operations_from_request,
)
from app.services.conductor_validation_service import validate_candidate_configuration


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_uuid(val: uuid.UUID | str, name: str = "id") -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError) as exc:
        raise BadRequestError(f"Invalid UUID for {name}: {val!r}") from exc


def _is_live_llm_configured() -> bool:
    return bool(
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("GROQ_API_KEY")
    )


async def build_session_response(
    session: AsyncSession,
    row: ConductorSession,
) -> ConductorSessionResponse:
    proposals = list(
        (
            await session.execute(
                select(ConductorProposal)
                .where(
                    ConductorProposal.session_id == row.id,
                    ConductorProposal.tenant_id == row.tenant_id,
                )
                .order_by(ConductorProposal.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    prop_resps = [await build_proposal_response(session, p) for p in proposals]
    return ConductorSessionResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        environment_id=row.environment_id,
        agent_id=row.agent_id,
        agent_kind=row.agent_kind,
        starting_agent_version_id=row.starting_agent_version_id,
        starting_version_number=row.starting_version_number,
        caller_user_id=row.caller_user_id,
        origin_surface=row.origin_surface,
        status=row.status,
        request_text=row.request_text,
        context_policy=dict(row.context_policy or {}),
        context_snapshot=dict(row.context_snapshot or {}),
        correlation_id=row.correlation_id,
        proposals=prop_resps,
        created_at=row.created_at,
        updated_at=row.updated_at,
        expires_at=row.expires_at,
    )


async def create_conductor_session(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    payload: ConductorSessionCreateRequest,
    actor_user_id: uuid.UUID | None,
) -> ConductorSessionResponse:
    """Create a durable, permission-scoped ConductorSession and assemble initial context."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    policy = payload.context_policy
    ctx_snapshot = await assemble_conductor_context(
        session,
        tenant_id=t_id,
        agent_id=payload.agent_id,
        agent_kind=payload.agent_kind,
        base_version_number=payload.base_version_number,
        environment_id=payload.environment_id,
        call_ids=policy.call_ids,
        test_run_ids=policy.test_run_ids,
        suite_ids=policy.suite_ids,
        include_calls=policy.include_calls,
        include_test_runs=policy.include_test_runs,
        include_qa_scorecards=policy.include_qa_scorecards,
        include_tools=policy.include_tools,
        include_knowledge_bases=policy.include_knowledge_bases,
        include_workflows=policy.include_workflows,
    )

    agent_meta = ctx_snapshot.get("agent") or {}
    base_ver_id_str = agent_meta.get("base_agent_version_id")
    base_ver_uuid = uuid.UUID(str(base_ver_id_str)) if base_ver_id_str else None
    now = _now()
    corr_id = f"cond-sess-{uuid.uuid4().hex[:16]}"

    row = ConductorSession(
        id=uuid.uuid4(),
        tenant_id=t_id,
        environment_id=payload.environment_id,
        agent_id=str(agent_meta.get("agent_id") or payload.agent_id),
        agent_kind=payload.agent_kind,
        starting_agent_version_id=base_ver_uuid,
        starting_version_number=int(agent_meta.get("base_version_number") or 1),
        caller_user_id=actor_user_id,
        origin_surface=payload.origin_surface,
        status=ConductorSessionStatusEnum.ACTIVE.value,
        request_text=payload.request_text,
        context_policy=policy.model_dump(mode="json"),
        context_snapshot=ctx_snapshot,
        correlation_id=corr_id,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=24),
    )
    session.add(row)
    await session.flush()

    await record_conductor_audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="conductor.session.created",
        session_id=row.id,
        agent_id=row.agent_id,
        environment_id=row.environment_id,
        correlation_id=corr_id,
        detail={
            "origin_surface": row.origin_surface,
            "starting_version_number": row.starting_version_number,
        },
    )
    return await build_session_response(session, row)


async def get_conductor_session(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    session_id: uuid.UUID | str,
) -> ConductorSessionResponse:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    s_id = _ensure_uuid(session_id, "session_id")
    row = (
        await session.execute(
            select(ConductorSession).where(
                ConductorSession.id == s_id,
                ConductorSession.tenant_id == t_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(f"ConductorSession {session_id} not found.")
    return await build_session_response(session, row)


async def list_conductor_sessions(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    agent_id: str | None = None,
    limit: int = 50,
) -> list[ConductorSessionResponse]:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    stmt = select(ConductorSession).where(ConductorSession.tenant_id == t_id)
    if agent_id:
        stmt = stmt.where(ConductorSession.agent_id == str(agent_id))
    stmt = stmt.order_by(ConductorSession.created_at.desc()).limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())
    return [await build_session_response(session, r) for r in rows]


async def create_proposal_from_request(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    payload: ConductorPromptRequest,
    actor_user_id: uuid.UUID | None,
) -> ConductorProposalResponse:
    """Create a durable ConductorProposal + ConductorChange rows + initial evidence."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")

    # Resolve or create ConductorSession
    sess_row: ConductorSession | None = None
    if payload.session_id is not None:
        sess_row = (
            await session.execute(
                select(ConductorSession).where(
                    ConductorSession.id == payload.session_id,
                    ConductorSession.tenant_id == t_id,
                )
            )
        ).scalar_one_or_none()
        if sess_row is None:
            raise NotFoundError(f"ConductorSession {payload.session_id} not found.")

    target_agent_id = (
        str(payload.agent_id or (sess_row.agent_id if sess_row else "")).strip()
    )
    if not target_agent_id:
        raise BadRequestError("Either 'session_id' or 'agent_id' must be provided.")

    target_kind = (
        payload.agent_kind
        if payload.agent_id
        else (sess_row.agent_kind if sess_row else "voice")
    )

    # Assemble fresh scoped context (verifies call_ids / test_run_ids belong to tenant)
    ctx_snapshot = await assemble_conductor_context(
        session,
        tenant_id=t_id,
        agent_id=target_agent_id,
        agent_kind=target_kind,
        base_version_number=(
            payload.base_version_number
            if payload.base_version_number is not None
            else (sess_row.starting_version_number if sess_row else None)
        ),
        call_ids=payload.call_ids or None,
        test_run_ids=payload.test_run_ids or None,
    )
    agent_meta = ctx_snapshot["agent"]
    canonical_agent_id = str(agent_meta["agent_id"])
    base_snapshot = dict(agent_meta.get("config_snapshot") or {})
    base_ver_num = int(agent_meta.get("base_version_number") or 1)
    base_ver_id_str = agent_meta.get("base_agent_version_id")
    base_ver_uuid = uuid.UUID(str(base_ver_id_str)) if base_ver_id_str else None
    base_hash = str(
        agent_meta.get("base_config_hash") or canonical_config_hash(base_snapshot)
    )

    if sess_row is None:
        now_s = _now()
        sess_row = ConductorSession(
            id=uuid.uuid4(),
            tenant_id=t_id,
            environment_id=None,
            agent_id=canonical_agent_id,
            agent_kind=target_kind,
            starting_agent_version_id=base_ver_uuid,
            starting_version_number=base_ver_num,
            caller_user_id=actor_user_id,
            origin_surface=payload.origin_surface,
            status=ConductorSessionStatusEnum.ACTIVE.value,
            request_text=payload.request_text,
            context_policy={
                "call_ids": payload.call_ids,
                "test_run_ids": payload.test_run_ids,
            },
            context_snapshot=ctx_snapshot,
            correlation_id=f"cond-sess-{uuid.uuid4().hex[:16]}",
            created_at=now_s,
            updated_at=now_s,
            expires_at=now_s + timedelta(hours=24),
        )
        session.add(sess_row)
        await session.flush()
    else:
        sess_row.request_text = payload.request_text
        sess_row.context_snapshot = ctx_snapshot
        sess_row.updated_at = _now()
        await session.flush()

    # Synthesize structured operations
    summary, rationale, planned_ops = synthesize_operations_from_request(
        request_text=payload.request_text,
        base_snapshot=base_snapshot,
        context_snapshot=ctx_snapshot,
        explicit_operations=payload.explicit_operations or None,
    )

    op_dicts = [
        {
            "sequence": idx,
            "path": op.path,
            "operation": op.operation.value if hasattr(op.operation, "value") else str(op.operation),
            "new_value": op.new_value,
            "from_path": op.from_path,
        }
        for idx, op in enumerate(planned_ops)
    ]
    candidate_snapshot = apply_operations_to_snapshot(base_snapshot, op_dicts)
    candidate_hash = canonical_config_hash(candidate_snapshot)

    now = _now()
    corr_id = f"cond-prop-{uuid.uuid4().hex[:16]}"
    proposal = ConductorProposal(
        id=uuid.uuid4(),
        session_id=sess_row.id,
        tenant_id=t_id,
        environment_id=sess_row.environment_id,
        agent_id=canonical_agent_id,
        agent_kind=target_kind,
        base_agent_version_id=base_ver_uuid,
        base_version_number=base_ver_num,
        base_config_hash=base_hash,
        base_draft_etag=str(agent_meta.get("base_draft_etag") or ""),
        base_config_snapshot=copy.deepcopy(base_snapshot),
        request_text=payload.request_text,
        summary=summary,
        rationale=rationale,
        status=ConductorProposalStatusEnum.PROPOSED.value,
        validation_status="not_run",
        validation_report={},
        simulation_status="not_run",
        simulation_summary={},
        risk_summary={},
        candidate_config_snapshot=copy.deepcopy(candidate_snapshot),
        final_candidate_hash=candidate_hash,
        is_mock_provider=not _is_live_llm_configured(),
        provider=str(base_snapshot.get("llm_provider") or "openai"),
        model=str(base_snapshot.get("llm_model") or "gpt-4o-mini"),
        correlation_id=corr_id,
        created_by=actor_user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(proposal)
    await session.flush()

    # Record base AgentVersion evidence + any linked Calls / TestRuns
    base_ev = await record_proposal_evidence(
        session,
        tenant_id=t_id,
        proposal_id=proposal.id,
        source_type=ConductorEvidenceSourceEnum.AGENT_VERSION,
        source_id=str(base_ver_uuid or f"v{base_ver_num}"),
        evidence_summary=f"Pinned base AgentVersion v{base_ver_num} (hash {base_hash[:12]}).",
        evidence_payload={
            "agent_id": canonical_agent_id,
            "version_number": base_ver_num,
            "config_hash": base_hash,
        },
    )
    ev_ids = [str(base_ev.id)]

    for call_item in ctx_snapshot.get("calls") or []:
        if payload.call_ids and call_item["id"] in payload.call_ids:
            call_ev = await record_proposal_evidence(
                session,
                tenant_id=t_id,
                proposal_id=proposal.id,
                source_type=ConductorEvidenceSourceEnum.CALL,
                source_id=str(call_item["id"]),
                evidence_summary=f"Call {call_item['id']} outcome={call_item.get('outcome')}: {call_item.get('summary', '')[:140]}",
                evidence_payload=call_item,
            )
            ev_ids.append(str(call_ev.id))

    for run_item in ctx_snapshot.get("test_runs") or []:
        if payload.test_run_ids and run_item["id"] in payload.test_run_ids:
            run_ev = await record_proposal_evidence(
                session,
                tenant_id=t_id,
                proposal_id=proposal.id,
                source_type=ConductorEvidenceSourceEnum.TEST_RUN,
                source_id=str(run_item["id"]),
                evidence_summary=f"TestRun {run_item['id']} status={run_item.get('status')}",
                evidence_payload=run_item,
            )
            ev_ids.append(str(run_ev.id))

    # Persist individual ConductorChange rows
    change_rows: list[ConductorChange] = []
    for idx, op in enumerate(planned_ops):
        op_str = (
            op.operation.value
            if hasattr(op.operation, "value")
            else str(op.operation).split(".")[-1].lower()
        )
        old_val = (
            op.old_value
            if op.old_value is not None
            else get_value_at_path(base_snapshot, op.path)
        )
        risk_str = (
            op.risk_level.value
            if op.risk_level is not None and hasattr(op.risk_level, "value")
            else classify_change_risk(op.path, op_str, op.new_value)
        )
        chg_row = ConductorChange(
            id=uuid.uuid4(),
            proposal_id=proposal.id,
            tenant_id=t_id,
            sequence=idx,
            section=classify_path_section(op.path),
            path=op.path,
            operation=op_str,
            old_value=old_val,
            new_value=op.new_value,
            reason=op.reason or f"Update {op.path} ({op_str})",
            evidence_ids=list(op.evidence_ids or ev_ids),
            risk_level=risk_str,
            validation_state="pending",
            validation_messages=[],
            simulation_state="not_run",
            approval_state=ConductorApprovalStateEnum.PENDING.value,
            created_at=now,
            updated_at=now,
        )
        session.add(chg_row)
        change_rows.append(chg_row)

    await session.flush()
    proposal.risk_summary = compute_risk_summary(change_rows)
    await session.flush()

    await record_conductor_audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="conductor.proposal.created",
        session_id=sess_row.id,
        proposal_id=proposal.id,
        agent_id=canonical_agent_id,
        correlation_id=corr_id,
        detail={
            "base_version_number": base_ver_num,
            "change_count": len(change_rows),
            "summary": summary,
        },
    )

    if payload.auto_validate:
        await validate_proposal(
            session,
            tenant_id=t_id,
            proposal_id=proposal.id,
            actor_user_id=actor_user_id,
        )

    if payload.auto_simulate:
        await simulate_proposal(
            session,
            tenant_id=t_id,
            proposal_id=proposal.id,
            payload=ConductorSimulateProposalRequest(),
            actor_user_id=actor_user_id,
        )

    return await build_proposal_response(session, proposal)


async def get_proposal(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
) -> ConductorProposalResponse:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    proposal = await get_proposal_row(session, t_id, proposal_id)

    # Check if underlying agent version has advanced and mark STALE if needed
    try:
        baseline = await resolve_authoritative_agent_baseline(
            session,
            t_id,
            proposal.agent_id,
            agent_kind=proposal.agent_kind,
            base_version_number=None,
        )
        await check_and_mark_stale_if_needed(session, proposal, baseline)
    except Exception:
        pass

    return await build_proposal_response(session, proposal)


async def list_proposals(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    agent_id: str | None = None,
    session_id: uuid.UUID | str | None = None,
    limit: int = 50,
) -> list[ConductorProposalResponse]:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    stmt = select(ConductorProposal).where(ConductorProposal.tenant_id == t_id)
    if agent_id:
        stmt = stmt.where(ConductorProposal.agent_id == str(agent_id))
    if session_id:
        stmt = stmt.where(
            ConductorProposal.session_id == _ensure_uuid(session_id, "session_id")
        )
    stmt = stmt.order_by(ConductorProposal.created_at.desc()).limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())
    return [await build_proposal_response(session, r) for r in rows]


async def get_proposal_changes(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
) -> list[ConductorChange]:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    proposal = await get_proposal_row(session, t_id, proposal_id)
    return list(
        (
            await session.execute(
                select(ConductorChange)
                .where(
                    ConductorChange.tenant_id == t_id,
                    ConductorChange.proposal_id == proposal.id,
                )
                .order_by(ConductorChange.sequence.asc())
            )
        )
        .scalars()
        .all()
    )


async def get_proposal_diff(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
) -> ConductorProposalDiffResponse:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    proposal = await get_proposal_row(session, t_id, proposal_id)
    changes = await get_proposal_changes(session, t_id, proposal.id)
    change_dicts = [
        {
            "id": str(c.id),
            "sequence": c.sequence,
            "section": c.section,
            "path": c.path,
            "operation": c.operation,
            "old_value": c.old_value,
            "new_value": c.new_value,
            "reason": c.reason,
            "risk_level": c.risk_level,
            "approval_state": c.approval_state,
            "validation_state": c.validation_state,
            "simulation_state": c.simulation_state,
        }
        for c in changes
    ]
    return build_proposal_diff(
        proposal_id=proposal.id,
        agent_id=proposal.agent_id,
        base_version_number=proposal.base_version_number,
        base_snapshot=dict(proposal.base_config_snapshot or {}),
        changes=change_dicts,
    )


async def validate_proposal(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    actor_user_id: uuid.UUID | None,
) -> ConductorProposalResponse:
    """Validate all candidate changes on ``proposal`` and persist validation evidence."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    proposal = await get_proposal_row(session, t_id, proposal_id)
    changes = await get_proposal_changes(session, t_id, proposal.id)

    enforce_proposal_transition(proposal.status, ConductorProposalStatus.VALIDATING)
    proposal.status = ConductorProposalStatusEnum.VALIDATING.value

    change_dicts = [
        {
            "id": str(c.id),
            "sequence": c.sequence,
            "path": c.path,
            "operation": c.operation,
            "old_value": c.old_value,
            "new_value": c.new_value,
            "approval_state": c.approval_state,
        }
        for c in changes
    ]
    report = await validate_candidate_configuration(
        session,
        tenant_id=t_id,
        agent_name=str((proposal.base_config_snapshot or {}).get("name") or "Agent"),
        base_snapshot=dict(proposal.base_config_snapshot or {}),
        changes=change_dicts,
        only_approved=False,
    )

    now = _now()
    for c in changes:
        c_res = report["per_change"].get(str(c.id))
        if c_res:
            c.validation_state = str(c_res["validation_state"])
            c.validation_messages = list(c_res.get("messages") or [])
            c.updated_at = now

    proposal.validation_status = report["status"]
    proposal.validation_report = {
        "valid": report["valid"],
        "status": report["status"],
        "errors": report["errors"],
        "warnings": report["warnings"],
        "candidate_hash": report["candidate_hash"],
        "validated_at": now.isoformat(),
    }
    proposal.candidate_config_snapshot = report["candidate_snapshot"]
    proposal.final_candidate_hash = report["candidate_hash"]

    if report["valid"]:
        enforce_proposal_transition(proposal.status, ConductorProposalStatus.VALIDATED)
        proposal.status = ConductorProposalStatusEnum.VALIDATED.value
        # Advance to READY_FOR_REVIEW if already simulated or ready
        enforce_proposal_transition(proposal.status, ConductorProposalStatus.READY_FOR_REVIEW)
        proposal.status = ConductorProposalStatusEnum.READY_FOR_REVIEW.value
        proposal.error_code = None
        proposal.error_message = None
    else:
        enforce_proposal_transition(proposal.status, ConductorProposalStatus.FAILED)
        proposal.status = ConductorProposalStatusEnum.FAILED.value
        proposal.error_code = "VALIDATION_FAILED"
        proposal.error_message = "; ".join(
            f"{e['field']}: {e['message']}" for e in report["errors"]
        )
    proposal.updated_at = now
    await session.flush()

    await record_proposal_evidence(
        session,
        tenant_id=t_id,
        proposal_id=proposal.id,
        source_type=ConductorEvidenceSourceEnum.VALIDATION,
        source_id=f"val-{proposal.id}",
        evidence_summary=(
            f"Candidate validation {report['status'].upper()} "
            f"({len(report['errors'])} error(s), {len(report['warnings'])} warning(s))."
        ),
        evidence_payload=proposal.validation_report,
    )

    await record_conductor_audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="conductor.proposal.validated",
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        agent_id=proposal.agent_id,
        correlation_id=proposal.correlation_id,
        detail={
            "valid": report["valid"],
            "error_count": len(report["errors"]),
            "warning_count": len(report["warnings"]),
        },
    )
    return await build_proposal_response(session, proposal)


async def simulate_proposal(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    payload: ConductorSimulateProposalRequest,
    actor_user_id: uuid.UUID | None,
) -> ConductorProposalResponse:
    """Execute a real Prompt 3 simulation + evaluation against the candidate config snapshot.

    Never overwrites the Agent's persisted draft! Passes the candidate prompt/config
    via ``simulation_service.execute_pinned_simulation_run`` and links the resulting
    ``TestRun`` and optional reproduction ``TestCase`` as durable ``ConductorEvidence``.
    """
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    proposal = await get_proposal_row(session, t_id, proposal_id)
    changes = await get_proposal_changes(session, t_id, proposal.id)

    if proposal.validation_status != "valid":
        await validate_proposal(
            session,
            tenant_id=t_id,
            proposal_id=proposal.id,
            actor_user_id=actor_user_id,
        )
        if proposal.validation_status != "valid":
            raise BadRequestError(
                f"Cannot simulate invalid proposal: {proposal.error_message}"
            )

    enforce_proposal_transition(proposal.status, ConductorProposalStatus.SIMULATING)
    proposal.status = ConductorProposalStatusEnum.SIMULATING.value

    candidate_snap = dict(proposal.candidate_config_snapshot or {})
    candidate_prompt = str(candidate_snap.get("system_prompt") or "")

    # Build evaluation rules for the simulation run
    inline_rules: list[dict[str, Any]] = list(payload.evaluation_rules or [])
    if not inline_rules:
        # Automatically assert that new greeting or key prompt changes work without error
        inline_rules.append(
            InlineEvaluationRuleSpec(
                name="Max Turn Count Guard",
                rule_type=EvaluationRuleType.TURN_COUNT_MAX,
                config={"threshold": 12},
                weight=1.0,
            ).model_dump(mode="json")
        )
        for chg in changes:
            if chg.approval_state == ConductorApprovalStateEnum.REJECTED.value:
                continue
            if chg.path == "tools" and isinstance(chg.new_value, str):
                inline_rules.append(
                    InlineEvaluationRuleSpec(
                        name=f"Tool {chg.new_value} Available/Invoked",
                        rule_type=EvaluationRuleType.CONTAINS,
                        config={"substring": ""},
                        weight=1.0,
                    ).model_dump(mode="json")
                )

    normalized_turns: list[dict[str, Any]] = [
        {"role": "user", "content": str(m)}
        if isinstance(m, str)
        else dict(m)
        for m in (
            payload.input_messages
            or ["Hello, I would like assistance from the agent."]
        )
    ]

    # Optionally persist a reproduction TestCase in Prompt 3's test_cases table
    created_case_id: uuid.UUID | None = None
    if payload.persist_reproduction_test_case and proposal.base_version_number >= 1:
        case_req = TestCaseCreateRequest(
            suite_id=payload.suite_id,
            agent_id=proposal.agent_id,
            agent_kind=AgentKind(proposal.agent_kind),
            agent_version_number=proposal.base_version_number,
            name=payload.test_case_name
            or f"Conductor Reproduction: {proposal.summary[:80]}",
            mode=TestRunMode.SIMULATION,
            input_messages=normalized_turns,
            dynamic_variables=dict(payload.dynamic_variables or {}),
            metadata={"conductor_proposal_id": str(proposal.id)},
            expected_rules=[
                InlineEvaluationRuleSpec.model_validate(r) for r in inline_rules
            ],
        )
        case_row = await simulation_service.create_test_case(
            session,
            t_id,
            case_req,
            actor_user_id=actor_user_id,
        )
        created_case_id = case_row.id
        await record_proposal_evidence(
            session,
            tenant_id=t_id,
            proposal_id=proposal.id,
            source_type=ConductorEvidenceSourceEnum.TEST_CASE,
            source_id=str(case_row.id),
            evidence_summary=f"Created reproduction TestCase '{case_row.name}' pinned to v{case_row.agent_version_number}.",
            evidence_payload={
                "test_case_id": str(case_row.id),
                "name": case_row.name,
                "agent_version_number": case_row.agent_version_number,
            },
        )

    # Execute real Prompt 3 simulation run pinned to base_version_number with candidate prompt override
    sim_run = await simulation_service.execute_pinned_simulation_run(
        session,
        t_id,
        agent_id=proposal.agent_id,
        agent_version_number=max(1, int(proposal.base_version_number)),
        agent_kind=proposal.agent_kind,
        mode="simulation",
        suite_id=payload.suite_id,
        test_case_id=created_case_id,
        input_turns=normalized_turns,
        dynamic_variables=dict(payload.dynamic_variables or {}),
        inline_rules=inline_rules,
        prompt_override=candidate_prompt or None,
        allow_mock_fallback=payload.allow_mock_fallback,
        actor_user_id=actor_user_id,
    )

    now = _now()
    sim_status_str = str(getattr(sim_run.status, "value", sim_run.status)).lower()
    for c in changes:
        if c.approval_state != ConductorApprovalStateEnum.REJECTED.value:
            c.simulation_state = sim_status_str
            c.updated_at = now

    proposal.simulation_status = sim_status_str
    proposal.is_mock_provider = bool(sim_run.is_mock_provider)
    proposal.simulation_summary = {
        "test_run_id": str(sim_run.id),
        "test_case_id": str(created_case_id) if created_case_id else None,
        "status": sim_status_str.upper(),
        "is_mock_provider": sim_run.is_mock_provider,
        "scorecard_summary": sim_run.scorecard_summary.model_dump(mode="json")
        if hasattr(sim_run.scorecard_summary, "model_dump")
        else dict(sim_run.scorecard_summary or {}),
        "turn_count": len(sim_run.transcript_snapshot or []),
        "duration_ms": sim_run.duration_ms,
        "simulated_at": now.isoformat(),
    }

    enforce_proposal_transition(proposal.status, ConductorProposalStatus.SIMULATED)
    proposal.status = ConductorProposalStatusEnum.SIMULATED.value
    enforce_proposal_transition(proposal.status, ConductorProposalStatus.READY_FOR_REVIEW)
    proposal.status = ConductorProposalStatusEnum.READY_FOR_REVIEW.value
    proposal.updated_at = now
    await session.flush()

    await record_proposal_evidence(
        session,
        tenant_id=t_id,
        proposal_id=proposal.id,
        source_type=ConductorEvidenceSourceEnum.TEST_RUN,
        source_id=str(sim_run.id),
        evidence_summary=(
            f"Candidate simulation TestRun {sim_run.id} finished with status={sim_run.status.upper()} "
            f"(is_mock_provider={sim_run.is_mock_provider})."
        ),
        evidence_payload=proposal.simulation_summary,
    )

    await record_conductor_audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="conductor.proposal.simulated",
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        agent_id=proposal.agent_id,
        correlation_id=proposal.correlation_id,
        detail={
            "test_run_id": str(sim_run.id),
            "status": sim_run.status,
            "is_mock_provider": sim_run.is_mock_provider,
        },
    )
    return await build_proposal_response(session, proposal)


async def approve_one_change(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    change_id: uuid.UUID | str,
    actor_user_id: uuid.UUID | None,
    reason: str = "",
) -> ConductorProposalResponse:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    proposal = await review_single_change(
        session,
        tenant_id=t_id,
        proposal_id=proposal_id,
        change_id=change_id,
        new_approval_state=ConductorApprovalStateEnum.APPROVED,
        action=ConductorApprovalActionEnum.APPROVE_CHANGE,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    await record_conductor_audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="conductor.change.approved",
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        agent_id=proposal.agent_id,
        correlation_id=proposal.correlation_id,
        detail={"change_id": str(change_id), "proposal_status": proposal.status},
    )
    return await build_proposal_response(session, proposal)


async def reject_one_change(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    change_id: uuid.UUID | str,
    actor_user_id: uuid.UUID | None,
    reason: str = "",
) -> ConductorProposalResponse:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    proposal = await review_single_change(
        session,
        tenant_id=t_id,
        proposal_id=proposal_id,
        change_id=change_id,
        new_approval_state=ConductorApprovalStateEnum.REJECTED,
        action=ConductorApprovalActionEnum.REJECT_CHANGE,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    await record_conductor_audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="conductor.change.rejected",
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        agent_id=proposal.agent_id,
        correlation_id=proposal.correlation_id,
        detail={"change_id": str(change_id), "proposal_status": proposal.status},
    )
    return await build_proposal_response(session, proposal)


async def undo_one_change(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    change_id: uuid.UUID | str,
    actor_user_id: uuid.UUID | None,
    reason: str = "",
) -> ConductorProposalResponse:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    proposal = await review_single_change(
        session,
        tenant_id=t_id,
        proposal_id=proposal_id,
        change_id=change_id,
        new_approval_state=ConductorApprovalStateEnum.PENDING,
        action=ConductorApprovalActionEnum.UNDO_CHANGE,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    await record_conductor_audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="conductor.change.undone",
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        agent_id=proposal.agent_id,
        correlation_id=proposal.correlation_id,
        detail={"change_id": str(change_id), "proposal_status": proposal.status},
    )
    return await build_proposal_response(session, proposal)


async def approve_proposal(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    actor_user_id: uuid.UUID | None,
    reason: str = "",
    safe_only: bool = False,
) -> ConductorProposalResponse:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    proposal = await approve_proposal_changes(
        session,
        tenant_id=t_id,
        proposal_id=proposal_id,
        actor_user_id=actor_user_id,
        reason=reason,
        safe_only=safe_only,
    )
    await record_conductor_audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="conductor.proposal.approved",
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        agent_id=proposal.agent_id,
        correlation_id=proposal.correlation_id,
        detail={"safe_only": safe_only, "proposal_status": proposal.status},
    )
    return await build_proposal_response(session, proposal)


async def reject_proposal(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    actor_user_id: uuid.UUID | None,
    reason: str = "",
) -> ConductorProposalResponse:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    proposal = await reject_proposal_changes(
        session,
        tenant_id=t_id,
        proposal_id=proposal_id,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    await record_conductor_audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="conductor.proposal.rejected",
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        agent_id=proposal.agent_id,
        correlation_id=proposal.correlation_id,
        detail={"proposal_status": proposal.status},
    )
    return await build_proposal_response(session, proposal)


async def undo_proposal(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    actor_user_id: uuid.UUID | None,
    reason: str = "",
) -> ConductorProposalResponse:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    proposal = await undo_proposal_approvals(
        session,
        tenant_id=t_id,
        proposal_id=proposal_id,
        actor_user_id=actor_user_id,
        reason=reason,
    )
    await record_conductor_audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="conductor.proposal.undone",
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        agent_id=proposal.agent_id,
        correlation_id=proposal.correlation_id,
        detail={"proposal_status": proposal.status},
    )
    return await build_proposal_response(session, proposal)


async def apply_proposal(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    payload: ConductorApplyProposalRequest,
    actor_user_id: uuid.UUID | None,
) -> ConductorApplyResultResponse:
    return await apply_approved_proposal(
        session,
        tenant_id=tenant_id,
        proposal_id=proposal_id,
        payload=payload,
        actor_user_id=actor_user_id,
    )


async def get_proposal_resulting_version(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
) -> dict[str, Any]:
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    proposal = await get_proposal_row(session, t_id, proposal_id)
    if (
        proposal.status != ConductorProposalStatusEnum.APPLIED.value
        or proposal.resulting_agent_version_id is None
    ):
        raise NotFoundError(
            f"Proposal {proposal_id} has not been applied to create an AgentVersion yet."
        )
    return await _load_existing_resulting_version_dict(session, t_id, proposal)
