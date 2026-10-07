"""Transactional Apply Service for Conductor (Prompt 4).

Enforces the core apply guarantees:
1. Reloads authoritative Agent / AgentVersion state inside the transaction.
2. Detects stale base versions (if another user published vN+1 while proposal was based on vN)
   and transitions proposal to ``STALE`` with HTTP 409 Conflict instead of overwriting.
3. Requires explicit human approval (``APPROVED`` or ``PARTIALLY_APPROVED`` with >=1 approved change).
4. Reapplies ONLY ``approved`` changes onto the authoritative base snapshot; ``rejected`` and
   ``pending``/undone changes are strictly excluded.
5. Revalidates the final candidate snapshot before creating the new immutable ``AgentVersion``.
6. Creates a new immutable ``AgentVersion`` (vN+1) without mutating any prior ``AgentVersion``
   and without auto-deploying to live production (``production_published=False``, ``ready_to_publish=True``).
7. Enforces idempotency on ``(proposal_id, idempotency_key, approved_change_hash)`` so retries
   return the already-created ``AgentVersion`` rather than creating duplicate versions.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.db.models import (
    AgentVersion as AgentVersionRow,
    ChatAgent,
    ChatAgentVersion,
    ConductorApproval,
    ConductorApprovalActionEnum,
    ConductorApprovalStateEnum,
    ConductorChange,
    ConductorProposal,
    ConductorProposalStatusEnum,
)
from app.domain.conductor_diff import apply_operations_to_snapshot, canonical_config_hash
from app.domain.conductor_models import (
    ConductorApplyProposalRequest,
    ConductorApplyResultResponse,
    enforce_proposal_transition,
)
from app.governance.audit import record_conductor_audit
from app.services import agent_service
from app.services.conductor_approval_service import compute_approved_change_hash
from app.services.conductor_context_service import resolve_authoritative_agent_baseline
from app.services.conductor_proposal_service import (
    build_proposal_response,
    get_proposal_row,
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


async def _load_existing_resulting_version_dict(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    proposal: ConductorProposal,
) -> dict[str, Any]:
    if proposal.agent_kind == "voice" and proposal.resulting_agent_version_id is not None:
        v_row = (
            await session.execute(
                select(AgentVersionRow).where(
                    AgentVersionRow.id == proposal.resulting_agent_version_id,
                    AgentVersionRow.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if v_row is not None:
            return {
                "id": str(v_row.id),
                "agent_id": str(v_row.agent_id),
                "version_number": int(v_row.version_number),
                "config_hash": str(v_row.config_hash),
                "config_snapshot": dict(v_row.config_snapshot or {}),
                "notes": str(v_row.changelog or ""),
                "published_by": str(v_row.published_by_user_id or ""),
                "published_at": v_row.published_at.isoformat() if v_row.published_at else None,
                "production_published": False,
                "ready_to_publish": True,
            }
    if proposal.agent_kind == "chat" and proposal.resulting_agent_version_id is not None:
        cv_row = (
            await session.execute(
                select(ChatAgentVersion).where(
                    ChatAgentVersion.id == proposal.resulting_agent_version_id,
                    ChatAgentVersion.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if cv_row is not None:
            return {
                "id": str(cv_row.id),
                "agent_id": str(cv_row.chat_agent_id),
                "version_number": int(cv_row.version_number),
                "config_hash": str(cv_row.config_hash),
                "config_snapshot": dict(cv_row.config_snapshot or {}),
                "notes": str(cv_row.notes or ""),
                "published_by": str(cv_row.published_by or ""),
                "published_at": cv_row.published_at.isoformat() if cv_row.published_at else None,
                "production_published": False,
                "ready_to_publish": True,
            }
    return {
        "id": str(proposal.resulting_agent_version_id or ""),
        "agent_id": proposal.agent_id,
        "version_number": int(proposal.resulting_version_number or 0),
        "config_hash": proposal.final_candidate_hash,
        "config_snapshot": dict(proposal.candidate_config_snapshot or {}),
        "production_published": False,
        "ready_to_publish": True,
    }


async def apply_approved_proposal(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    payload: ConductorApplyProposalRequest,
    actor_user_id: uuid.UUID | None,
) -> ConductorApplyResultResponse:
    """Transactionally apply approved changes and create a new immutable AgentVersion."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    p_id = _ensure_uuid(proposal_id, "proposal_id")
    proposal = await get_proposal_row(session, t_id, p_id)

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

    current_approved_hash = compute_approved_change_hash(changes)

    # 1. Idempotent replay check if already APPLIED
    if proposal.status == ConductorProposalStatusEnum.APPLIED.value:
        if (
            proposal.resulting_agent_version_id is not None
            and (
                payload.idempotency_key is None
                or payload.idempotency_key == proposal.apply_idempotency_key
            )
            and (
                proposal.approved_change_hash is None
                or proposal.approved_change_hash == current_approved_hash
            )
        ):
            applied_ids = [
                str(c.id)
                for c in changes
                if c.approval_state == ConductorApprovalStateEnum.APPROVED.value
            ]
            skipped_ids = [
                str(c.id)
                for c in changes
                if c.approval_state != ConductorApprovalStateEnum.APPROVED.value
            ]
            ver_dict = await _load_existing_resulting_version_dict(
                session, t_id, proposal
            )
            prop_resp = await build_proposal_response(session, proposal)
            return ConductorApplyResultResponse(
                proposal=prop_resp,
                resulting_version=ver_dict,
                applied_change_ids=applied_ids,
                skipped_change_ids=skipped_ids,
                base_version_number=proposal.base_version_number,
                resulting_version_number=int(
                    proposal.resulting_version_number or ver_dict.get("version_number") or 0
                ),
                base_config_hash=proposal.base_config_hash,
                resulting_config_hash=proposal.final_candidate_hash,
                production_published=False,
                ready_to_publish=True,
                idempotent_replay=True,
            )
        raise ConflictError(
            f"Proposal {proposal.id} has already been applied with a different idempotency key or change set."
        )

    if proposal.status == ConductorProposalStatusEnum.STALE.value:
        raise ConflictError(
            proposal.error_message
            or "Proposal is STALE because the underlying AgentVersion changed."
        )

    # 2. Verify authoritative Agent state has not moved ahead of base_version_number
    latest_baseline = await resolve_authoritative_agent_baseline(
        session,
        t_id,
        proposal.agent_id,
        agent_kind=proposal.agent_kind,
        base_version_number=None,
    )
    current_head_version = int(latest_baseline.get("base_version_number") or 0)
    current_head_hash = str(latest_baseline.get("base_config_hash") or "")

    stale_detected = False
    if (
        payload.expected_base_version_number is not None
        and int(payload.expected_base_version_number) != current_head_version
    ):
        stale_detected = True
    if (
        payload.expected_base_config_hash is not None
        and payload.expected_base_config_hash != current_head_hash
    ):
        stale_detected = True
    if current_head_version != int(proposal.base_version_number):
        stale_detected = True
    if (
        proposal.base_config_hash
        and current_head_hash
        and current_head_hash != proposal.base_config_hash
    ):
        stale_detected = True

    if stale_detected:
        proposal.status = ConductorProposalStatusEnum.STALE.value
        proposal.error_code = "STALE_BASE_VERSION"
        proposal.error_message = (
            f"Stale proposal conflict: proposal targets base v{proposal.base_version_number} "
            f"({proposal.base_config_hash[:12]}), but authoritative agent is now at "
            f"v{current_head_version} ({current_head_hash[:12]})."
        )
        proposal.updated_at = _now()
        await record_conductor_audit(
            session,
            tenant_id=t_id,
            actor_user_id=actor_user_id,
            event="conductor.proposal.stale",
            session_id=proposal.session_id,
            proposal_id=proposal.id,
            agent_id=proposal.agent_id,
            correlation_id=proposal.correlation_id,
            detail={
                "base_version_number": proposal.base_version_number,
                "current_version_number": current_head_version,
            },
        )
        await session.flush()
        raise ConflictError(proposal.error_message)

    # 3. Verify explicit human approval state
    if proposal.status not in {
        ConductorProposalStatusEnum.APPROVED.value,
        ConductorProposalStatusEnum.PARTIALLY_APPROVED.value,
    }:
        raise BadRequestError(
            f"Proposal {proposal.id} is in status '{proposal.status}' and cannot be applied before human approval."
        )

    approved_changes = [
        c
        for c in changes
        if c.approval_state == ConductorApprovalStateEnum.APPROVED.value
    ]
    if not approved_changes:
        raise BadRequestError(
            "Cannot apply proposal: zero changes are currently in 'approved' state."
        )

    # 4. Revalidate ONLY the approved subset against the authoritative base snapshot
    authoritative_base_snapshot = copy.deepcopy(
        latest_baseline.get("config_snapshot") or proposal.base_config_snapshot or {}
    )
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
    val_report = await validate_candidate_configuration(
        session,
        tenant_id=t_id,
        agent_name=str(latest_baseline.get("name") or "Agent"),
        base_snapshot=authoritative_base_snapshot,
        changes=change_dicts,
        only_approved=True,
    )
    if not val_report["valid"]:
        proposal.validation_status = "invalid"
        proposal.validation_report = val_report
        proposal.status = ConductorProposalStatusEnum.FAILED.value
        proposal.error_code = "VALIDATION_FAILED_ON_APPLY"
        proposal.error_message = "; ".join(
            f"{e['field']}: {e['message']}" for e in val_report["errors"]
        )
        proposal.updated_at = _now()
        await session.flush()
        raise BadRequestError(
            f"Candidate configuration failed validation on apply: {proposal.error_message}"
        )

    # 5. Transition to APPLYING -> create immutable AgentVersion -> APPLIED
    enforce_proposal_transition(proposal.status, ConductorProposalStatusEnum.APPLYING)
    proposal.status = ConductorProposalStatusEnum.APPLYING.value

    approved_ops = [
        {
            "sequence": c.sequence,
            "path": c.path,
            "operation": c.operation,
            "new_value": c.new_value,
        }
        for c in approved_changes
    ]
    final_snapshot = apply_operations_to_snapshot(
        authoritative_base_snapshot, approved_ops
    )
    final_hash = canonical_config_hash(final_snapshot)
    now = _now()
    notes_text = (
        payload.version_notes.strip()
        or f"Conductor apply from proposal {proposal.id}: {proposal.summary[:180]}"
    )

    if proposal.agent_kind == "voice":
        agent_row = await agent_service.get_agent_row(
            session, t_id, proposal.agent_id, for_update=True
        )
        if agent_row is None:
            raise NotFoundError(f"Voice Agent {proposal.agent_id} not found.")

        async with agent_service._get_publish_lock(f"{t_id}:{agent_row.id}"):
            max_stmt = select(
                func.coalesce(func.max(AgentVersionRow.version_number), 0)
            ).where(AgentVersionRow.agent_id == agent_row.id)
            next_v = int((await session.execute(max_stmt)).scalar_one() or 0) + 1

            new_ver_row = AgentVersionRow(
                id=uuid.uuid4(),
                tenant_id=t_id,
                agent_id=agent_row.id,
                version_number=next_v,
                version_label=f"v{next_v}",
                status="ready_to_publish",
                config_snapshot=copy.deepcopy(final_snapshot),
                config_hash=final_hash,
                changelog=notes_text[:500],
                published_environment_id=agent_row.environment_id,
                published_environment="staging",
                source_version_id=proposal.base_agent_version_id,
                is_rollback=False,
                is_active=False,
                published_by_user_id=actor_user_id,
                published_at=now,
                created_at=now,
                meta={
                    "created_by_conductor": True,
                    "proposal_id": str(proposal.id),
                    "ready_to_publish": True,
                    "production_published": False,
                },
            )
            session.add(new_ver_row)
            await session.flush()

            # Update Agent draft + version pointer while keeping prior versions untouched
            agent_row.current_draft_config = copy.deepcopy(final_snapshot)
            agent_row.draft_etag = f'W/"{final_hash[:24]}"'
            agent_row.published_version_id = new_ver_row.id
            agent_row.published_version_number = next_v
            agent_row.lock_version = int(agent_row.lock_version or 1) + 1
            agent_row.updated_at = now
            await session.flush()

            resulting_version_id = new_ver_row.id
            resulting_version_number = next_v
            resulting_version_dict = {
                "id": str(new_ver_row.id),
                "agent_id": str(agent_row.id),
                "version_number": next_v,
                "config_hash": final_hash,
                "config_snapshot": copy.deepcopy(final_snapshot),
                "notes": notes_text,
                "published_by": str(actor_user_id or "conductor"),
                "published_at": now.isoformat(),
                "production_published": False,
                "ready_to_publish": True,
            }
    else:
        chat_row = (
            await session.execute(
                select(ChatAgent).where(
                    ChatAgent.id == uuid.UUID(str(proposal.agent_id)),
                    ChatAgent.tenant_id == t_id,
                )
            )
        ).scalar_one_or_none()
        if chat_row is None:
            raise NotFoundError(f"Chat Agent {proposal.agent_id} not found.")

        max_stmt = select(
            func.coalesce(func.max(ChatAgentVersion.version_number), 0)
        ).where(ChatAgentVersion.chat_agent_id == chat_row.id)
        next_v = int((await session.execute(max_stmt)).scalar_one() or 0) + 1

        new_cver = ChatAgentVersion(
            id=uuid.uuid4(),
            tenant_id=t_id,
            chat_agent_id=chat_row.id,
            version_number=next_v,
            status="candidate",
            config_snapshot=copy.deepcopy(final_snapshot),
            config_hash=final_hash,
            notes=notes_text,
            published_by=actor_user_id,
            published_at=now,
            created_at=now,
        )
        session.add(new_cver)
        await session.flush()

        chat_row.draft_config = copy.deepcopy(final_snapshot)
        chat_row.draft_etag = f'W/"{final_hash[:24]}"'
        chat_row.active_version_id = new_cver.id
        chat_row.active_version_number = next_v
        chat_row.lock_version = int(chat_row.lock_version or 1) + 1
        chat_row.updated_at = now
        await session.flush()

        resulting_version_id = new_cver.id
        resulting_version_number = next_v
        resulting_version_dict = {
            "id": str(new_cver.id),
            "agent_id": str(chat_row.id),
            "version_number": next_v,
            "config_hash": final_hash,
            "config_snapshot": copy.deepcopy(final_snapshot),
            "notes": notes_text,
            "published_by": str(actor_user_id or ""),
            "published_at": now.isoformat(),
            "production_published": False,
            "ready_to_publish": True,
        }

    # Stamp applied_at on approved changes only
    applied_ids: list[str] = []
    skipped_ids: list[str] = []
    for chg in changes:
        if chg.approval_state == ConductorApprovalStateEnum.APPROVED.value:
            chg.applied_at = now
            chg.updated_at = now
            applied_ids.append(str(chg.id))
        else:
            skipped_ids.append(str(chg.id))

    enforce_proposal_transition(proposal.status, ConductorProposalStatusEnum.APPLIED)
    proposal.status = ConductorProposalStatusEnum.APPLIED.value
    proposal.candidate_config_snapshot = copy.deepcopy(final_snapshot)
    proposal.final_candidate_hash = final_hash
    proposal.resulting_agent_version_id = resulting_version_id
    proposal.resulting_version_number = resulting_version_number
    proposal.apply_idempotency_key = payload.idempotency_key
    proposal.approved_change_hash = current_approved_hash
    proposal.applied_at = now
    proposal.updated_at = now

    session.add(
        ConductorApproval(
            id=uuid.uuid4(),
            proposal_id=proposal.id,
            change_id=None,
            tenant_id=t_id,
            actor_user_id=actor_user_id,
            action=ConductorApprovalActionEnum.APPLY_PROPOSAL.value,
            reason=notes_text,
            previous_state="APPROVED",
            new_state=ConductorProposalStatusEnum.APPLIED.value,
            created_at=now,
        )
    )
    await session.flush()

    await record_conductor_audit(
        session,
        tenant_id=t_id,
        actor_user_id=actor_user_id,
        event="conductor.proposal.applied",
        session_id=proposal.session_id,
        proposal_id=proposal.id,
        agent_id=proposal.agent_id,
        environment_id=proposal.environment_id,
        correlation_id=proposal.correlation_id,
        detail={
            "base_version_number": proposal.base_version_number,
            "resulting_version_number": resulting_version_number,
            "resulting_agent_version_id": str(resulting_version_id),
            "base_config_hash": proposal.base_config_hash,
            "final_candidate_hash": final_hash,
            "applied_change_count": len(applied_ids),
            "skipped_change_count": len(skipped_ids),
            "production_published": False,
        },
    )

    prop_resp = await build_proposal_response(session, proposal)
    return ConductorApplyResultResponse(
        proposal=prop_resp,
        resulting_version=resulting_version_dict,
        applied_change_ids=applied_ids,
        skipped_change_ids=skipped_ids,
        base_version_number=proposal.base_version_number,
        resulting_version_number=resulting_version_number,
        base_config_hash=proposal.base_config_hash,
        resulting_config_hash=final_hash,
        production_published=False,
        ready_to_publish=True,
        idempotent_replay=False,
    )
