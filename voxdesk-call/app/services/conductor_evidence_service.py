"""Conductor Evidence Service (Prompt 4 & Part 6 / Gate G7).

Persists immutable ``ConductorEvidence`` rows linking a ``ConductorProposal`` to
its authoritative source artifacts (AgentVersion, Call, TestRun, EvaluationResult,
QA Scorecard, Tool Registry, Knowledge Base, Workflow, or Validation Report).
Enforces that cited call/test evidence references REAL persisted rows in the
caller's tenant (rejecting fabricated call IDs, turn IDs, or transcript quotes)
and supports applying evidence-backed proposals to ``AgentVersion`` drafts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import scrub
from app.core.errors import BadRequestError
from app.db.models import (
    Agent,
    AgentVersion as AgentVersionRow,
    Call,
    ChatAgent,
    ChatAgentVersion,
    ConductorChange,
    ConductorEvidence,
    ConductorEvidenceSourceEnum,
    ConductorProposal,
    EvaluationResult,
    TestCase,
    TestRun,
    Turn,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_uuid(val: uuid.UUID | str, name: str = "id") -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError) as exc:
        raise BadRequestError(f"Invalid UUID for {name}: {val!r}") from exc


def _try_uuid(val: Any) -> uuid.UUID | None:
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val).strip())
    except (ValueError, TypeError, AttributeError):
        return None


async def validate_and_resolve_evidence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_type: ConductorEvidenceSourceEnum | str,
    source_id: str,
    payload: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Verify that ``source_id`` and any cited transcript/turn facts exist in ``tenant_id``."""
    src_val = (
        source_type.value
        if hasattr(source_type, "value")
        else str(source_type).split(".")[-1].lower()
    )
    enriched = dict(payload or {})

    if src_val in {"call", "call_transcript", "call_event"}:
        call_uuid = _try_uuid(source_id)
        if call_uuid is None:
            raise BadRequestError(
                f"Fabricated call evidence rejected: Call ID {source_id!r} is not a valid UUID."
            )
        call_row = (
            await session.execute(
                select(Call).where(Call.id == call_uuid, Call.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
        if call_row is None:
            raise BadRequestError(
                f"Fabricated call evidence rejected: Call '{source_id}' does not exist in this tenant."
            )

        turns = list(
            (
                await session.execute(
                    select(Turn).where(Turn.call_id == call_row.id)
                )
            )
            .scalars()
            .all()
        )
        turn_id_raw = enriched.get("turn_id")
        if turn_id_raw:
            turn_uuid = _try_uuid(turn_id_raw)
            if turn_uuid is None or not any(t.id == turn_uuid for t in turns):
                raise BadRequestError(
                    f"Fabricated call evidence rejected: turn_id '{turn_id_raw}' does not exist on Call '{call_row.id}'."
                )

        cited_quote = str(
            enriched.get("cited_quote")
            or enriched.get("transcript_excerpt")
            or ""
        ).strip()
        if cited_quote:
            haystack = " ".join(
                [*(t.text or "" for t in turns), str(call_row.summary or "")]
            ).lower()
            if cited_quote.lower() not in haystack:
                raise BadRequestError(
                    f"Fabricated call evidence rejected: cited excerpt {cited_quote!r} "
                    f"does not appear in persisted transcript or summary of Call '{call_row.id}'."
                )

        enriched.setdefault("call_sid", call_row.call_sid)
        enriched.setdefault(
            "status",
            call_row.status.value
            if hasattr(call_row.status, "value")
            else str(call_row.status),
        )
        enriched.setdefault("intent", call_row.intent)
        enriched.setdefault("verified_turn_count", len(turns))
        return str(call_row.id), enriched

    if src_val in {"test_run", "qa_scorecard"}:
        run_uuid = _try_uuid(source_id)
        if run_uuid is None:
            raise BadRequestError(
                f"Fabricated test_run evidence rejected: TestRun ID {source_id!r} is not a valid UUID."
            )
        run_row = (
            await session.execute(
                select(TestRun).where(
                    TestRun.id == run_uuid, TestRun.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()
        if run_row is None:
            raise BadRequestError(
                f"Fabricated test_run evidence rejected: TestRun '{source_id}' does not exist in this tenant."
            )
        enriched.setdefault("status", run_row.status)
        enriched.setdefault("agent_version_number", run_row.agent_version_number)
        return str(run_row.id), enriched

    if src_val == "evaluation_result":
        ev_uuid = _try_uuid(source_id)
        if ev_uuid is None:
            raise BadRequestError(
                f"Fabricated evaluation_result evidence rejected: {source_id!r} is not a valid UUID."
            )
        ev_row = (
            await session.execute(
                select(EvaluationResult).where(
                    EvaluationResult.id == ev_uuid,
                    EvaluationResult.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if ev_row is None:
            raise BadRequestError(
                f"Fabricated evaluation_result evidence rejected: EvaluationResult '{source_id}' does not exist in this tenant."
            )
        return str(ev_row.id), enriched

    if src_val == "test_case":
        tc_uuid = _try_uuid(source_id)
        if tc_uuid is not None:
            tc_row = (
                await session.execute(
                    select(TestCase).where(
                        TestCase.id == tc_uuid, TestCase.tenant_id == tenant_id
                    )
                )
            ).scalar_one_or_none()
            if tc_row is None:
                raise BadRequestError(
                    f"Fabricated test_case evidence rejected: TestCase '{source_id}' does not exist in this tenant."
                )
            return str(tc_row.id), enriched

    if src_val == "agent_version":
        ver_uuid = _try_uuid(source_id)
        if ver_uuid is not None:
            v_row = (
                await session.execute(
                    select(AgentVersionRow).where(
                        AgentVersionRow.id == ver_uuid,
                        AgentVersionRow.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if v_row is not None:
                return str(v_row.id), enriched
            cv_row = (
                await session.execute(
                    select(ChatAgentVersion).where(
                        ChatAgentVersion.id == ver_uuid,
                        ChatAgentVersion.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if cv_row is not None:
                return str(cv_row.id), enriched
            raise BadRequestError(
                f"Fabricated agent_version evidence rejected: AgentVersion '{source_id}' does not exist in this tenant."
            )

    return str(source_id)[:120], enriched


async def record_proposal_evidence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    source_type: ConductorEvidenceSourceEnum | str,
    source_id: str,
    evidence_summary: str,
    evidence_payload: dict[str, Any] | None = None,
) -> ConductorEvidence:
    """Validate against real tenant artifacts and persist a credential-scrubbed ``ConductorEvidence`` row."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    p_id = _ensure_uuid(proposal_id, "proposal_id")
    src_val = (
        source_type.value
        if hasattr(source_type, "value")
        else str(source_type).split(".")[-1].lower()
    )

    resolved_id, verified_payload = await validate_and_resolve_evidence(
        session,
        tenant_id=t_id,
        source_type=src_val,
        source_id=source_id,
        payload=evidence_payload,
    )
    scrubbed_payload = scrub(dict(verified_payload or {}))
    row = ConductorEvidence(
        id=uuid.uuid4(),
        proposal_id=p_id,
        tenant_id=t_id,
        source_type=src_val,
        source_id=str(resolved_id)[:120],
        evidence_summary=str(evidence_summary or "").strip(),
        evidence_payload=scrubbed_payload if isinstance(scrubbed_payload, dict) else {},
        created_at=_now(),
    )
    session.add(row)
    await session.flush()
    return row


async def list_proposal_evidence(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
) -> list[ConductorEvidence]:
    """Return all persisted ``ConductorEvidence`` rows for ``proposal_id`` in creation order."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    p_id = _ensure_uuid(proposal_id, "proposal_id")
    stmt = (
        select(ConductorEvidence)
        .where(
            ConductorEvidence.tenant_id == t_id,
            ConductorEvidence.proposal_id == p_id,
        )
        .order_by(ConductorEvidence.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def apply_evidence_backed_proposal_to_draft(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    actor_user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Verify a ConductorProposal cites REAL call/test evidence and apply it to an AgentVersion draft."""
    from app.domain.conductor_diff import apply_operations_to_snapshot, canonical_config_hash

    t_id = _ensure_uuid(tenant_id, "tenant_id")
    p_id = _ensure_uuid(proposal_id, "proposal_id")

    proposal = (
        await session.execute(
            select(ConductorProposal).where(
                ConductorProposal.id == p_id,
                ConductorProposal.tenant_id == t_id,
            )
        )
    ).scalar_one_or_none()
    if proposal is None:
        raise BadRequestError(f"ConductorProposal '{proposal_id}' not found for tenant.")

    evidence_rows = await list_proposal_evidence(
        session, tenant_id=t_id, proposal_id=proposal.id
    )
    real_call_or_test_evidence = [
        ev
        for ev in evidence_rows
        if ev.source_type
        in {
            ConductorEvidenceSourceEnum.CALL.value,
            "call_transcript",
            "call_event",
            ConductorEvidenceSourceEnum.TEST_RUN.value,
            ConductorEvidenceSourceEnum.EVALUATION_RESULT.value,
            ConductorEvidenceSourceEnum.QA_SCORECARD.value,
        }
    ]
    if not real_call_or_test_evidence:
        raise BadRequestError(
            "Conductor proposal must cite at least one verified real Call or TestRun evidence record before applying to an AgentVersion draft."
        )

    for ev in real_call_or_test_evidence:
        await validate_and_resolve_evidence(
            session,
            tenant_id=t_id,
            source_type=ev.source_type,
            source_id=ev.source_id,
            payload=dict(ev.evidence_payload or {}),
        )

    changes = list(
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
    ops = [
        {
            "path": c.path,
            "operation": c.operation,
            "old_value": c.old_value,
            "new_value": c.new_value,
        }
        for c in changes
        if c.approval_state != "rejected"
    ]
    base_snapshot = dict(proposal.base_config_snapshot or {})
    draft_snapshot = apply_operations_to_snapshot(base_snapshot, ops)
    draft_hash = canonical_config_hash(draft_snapshot)

    agent_uuid = _try_uuid(proposal.agent_id)
    if agent_uuid is None:
        raise BadRequestError(f"Invalid agent_id on proposal: {proposal.agent_id!r}")

    if proposal.agent_kind == "chat":
        chat_agent = (
            await session.execute(
                select(ChatAgent).where(
                    ChatAgent.id == agent_uuid,
                    ChatAgent.tenant_id == t_id,
                )
            )
        ).scalar_one_or_none()
        if chat_agent is None:
            raise BadRequestError(f"ChatAgent '{proposal.agent_id}' not found.")
        chat_agent.draft_config = draft_snapshot
        chat_agent.draft_version = int(chat_agent.draft_version or 0) + 1
        chat_agent.updated_at = _now()
        await session.flush()
        return {
            "proposal_id": str(proposal.id),
            "agent_id": str(chat_agent.id),
            "agent_kind": "chat",
            "draft_version": chat_agent.draft_version,
            "config_hash": draft_hash,
            "config_snapshot": draft_snapshot,
            "verified_evidence_count": len(real_call_or_test_evidence),
        }

    voice_agent = (
        await session.execute(
            select(Agent).where(
                Agent.id == agent_uuid,
                Agent.tenant_id == t_id,
            )
        )
    ).scalar_one_or_none()
    if voice_agent is None:
        raise BadRequestError(f"Agent '{proposal.agent_id}' not found.")

    existing_vers = list(
        (
            await session.execute(
                select(AgentVersionRow.version_number).where(
                    AgentVersionRow.tenant_id == t_id,
                    AgentVersionRow.agent_id == voice_agent.id,
                )
            )
        )
        .scalars()
        .all()
    )
    next_ver_num = (max(existing_vers) if existing_vers else 0) + 1
    now = _now()

    draft_ver_row = AgentVersionRow(
        id=uuid.uuid4(),
        tenant_id=t_id,
        agent_id=voice_agent.id,
        version_number=next_ver_num,
        status="draft",
        config_snapshot=draft_snapshot,
        config_hash=draft_hash,
        changelog=f"Conductor draft from proposal {proposal.id}",
        published_by_user_id=actor_user_id,
        created_at=now,
    )
    session.add(draft_ver_row)
    voice_agent.current_draft_config = draft_snapshot
    voice_agent.updated_at = now
    await session.flush()

    return {
        "proposal_id": str(proposal.id),
        "agent_id": str(voice_agent.id),
        "agent_kind": "voice",
        "agent_version_id": str(draft_ver_row.id),
        "version_number": next_ver_num,
        "status": "draft",
        "config_hash": draft_hash,
        "config_snapshot": draft_snapshot,
        "verified_evidence_count": len(real_call_or_test_evidence),
    }
