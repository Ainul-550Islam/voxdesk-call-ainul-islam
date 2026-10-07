"""Human Approval, Rejection, and Undo Service for Conductor (Prompt 4).

Enforces the invariant:
- Individual changes can be accepted, rejected, or undone (`pending`).
- Bulk approve (`safe_only=True` or all) and bulk reject/undo update individual
  `ConductorChange` rows and record immutable `ConductorApproval` audit entries.
- A proposal transitions between `READY_FOR_REVIEW`, `PARTIALLY_APPROVED`,
  `APPROVED`, and `REJECTED` based on its constituent changes.
- Already-applied proposals (`APPLIED`) and stale proposals (`STALE`) cannot have
  their approval state mutated.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any as Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.db.models import (
    ConductorApproval,
    ConductorApprovalActionEnum,
    ConductorApprovalStateEnum,
    ConductorChange,
    ConductorProposal,
    ConductorProposalStatusEnum,
)
from app.domain.conductor_models import enforce_proposal_transition
from app.services.conductor_proposal_service import get_proposal_row


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_uuid(val: uuid.UUID | str, name: str = "id") -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError) as exc:
        raise BadRequestError(f"Invalid UUID for {name}: {val!r}") from exc


def compute_approved_change_hash(changes: list[ConductorChange]) -> str:
    """Compute a deterministic hash over all currently approved changes in sequence order."""
    approved = [
        {
            "id": str(c.id),
            "sequence": c.sequence,
            "path": c.path,
            "operation": c.operation,
            "new_value": c.new_value,
        }
        for c in sorted(changes, key=lambda x: x.sequence)
        if c.approval_state == ConductorApprovalStateEnum.APPROVED.value
    ]
    serialized = json.dumps(approved, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _recompute_proposal_approval_status(
    proposal: ConductorProposal,
    changes: list[ConductorChange],
) -> None:
    """Update ``proposal.status`` and ``proposal.approved_change_hash`` from its changes."""
    if not changes:
        return

    states = [c.approval_state for c in changes]
    total = len(states)
    approved_count = sum(
        1 for s in states if s == ConductorApprovalStateEnum.APPROVED.value
    )
    rejected_count = sum(
        1 for s in states if s == ConductorApprovalStateEnum.REJECTED.value
    )

    if approved_count == total:
        target = ConductorProposalStatusEnum.APPROVED.value
    elif rejected_count == total:
        target = ConductorProposalStatusEnum.REJECTED.value
    elif approved_count > 0:
        target = ConductorProposalStatusEnum.PARTIALLY_APPROVED.value
    else:
        target = ConductorProposalStatusEnum.READY_FOR_REVIEW.value

    enforce_proposal_transition(proposal.status, target)
    proposal.status = target
    proposal.approved_change_hash = (
        compute_approved_change_hash(changes) if approved_count > 0 else None
    )
    proposal.updated_at = _now()


def _ensure_reviewable(proposal: ConductorProposal) -> None:
    if proposal.status == ConductorProposalStatusEnum.APPLIED.value:
        raise ConflictError("Cannot modify approvals on an already APPLIED proposal.")
    if proposal.status == ConductorProposalStatusEnum.STALE.value:
        raise ConflictError(
            "Proposal is STALE because the underlying AgentVersion changed. Rebase or create a new proposal."
        )


async def _load_proposal_changes(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    proposal_id: uuid.UUID,
) -> list[ConductorChange]:
    return list(
        (
            await session.execute(
                select(ConductorChange)
                .where(
                    ConductorChange.tenant_id == tenant_id,
                    ConductorChange.proposal_id == proposal_id,
                )
                .order_by(ConductorChange.sequence.asc())
            )
        )
        .scalars()
        .all()
    )


async def review_single_change(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    change_id: uuid.UUID | str,
    new_approval_state: ConductorApprovalStateEnum | str,
    action: ConductorApprovalActionEnum | str,
    actor_user_id: uuid.UUID | None,
    reason: str = "",
) -> ConductorProposal:
    """Approve, reject, or undo a single ``ConductorChange`` and update proposal status."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    p_id = _ensure_uuid(proposal_id, "proposal_id")
    c_id = _ensure_uuid(change_id, "change_id")

    proposal = await get_proposal_row(session, t_id, p_id)
    _ensure_reviewable(proposal)

    changes = await _load_proposal_changes(session, t_id, p_id)
    target_change = next((c for c in changes if c.id == c_id), None)
    if target_change is None:
        raise NotFoundError(
            f"ConductorChange {change_id} not found on proposal {proposal_id}."
        )

    state_str = (
        new_approval_state.value
        if hasattr(new_approval_state, "value")
        else str(new_approval_state).lower()
    )
    action_str = (
        action.value if hasattr(action, "value") else str(action).lower()
    )

    prev_state = target_change.approval_state
    target_change.approval_state = state_str
    target_change.updated_at = _now()

    session.add(
        ConductorApproval(
            id=uuid.uuid4(),
            proposal_id=proposal.id,
            change_id=target_change.id,
            tenant_id=t_id,
            actor_user_id=actor_user_id,
            action=action_str,
            reason=str(reason or "").strip(),
            previous_state=prev_state,
            new_state=state_str,
            created_at=_now(),
        )
    )

    _recompute_proposal_approval_status(proposal, changes)
    await session.flush()
    return proposal


async def approve_proposal_changes(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    actor_user_id: uuid.UUID | None,
    reason: str = "",
    safe_only: bool = False,
) -> ConductorProposal:
    """Approve all pending changes (or only low-risk changes when ``safe_only=True``)."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    p_id = _ensure_uuid(proposal_id, "proposal_id")
    proposal = await get_proposal_row(session, t_id, p_id)
    _ensure_reviewable(proposal)

    changes = await _load_proposal_changes(session, t_id, p_id)
    prev_prop_state = proposal.status
    now = _now()

    for chg in changes:
        if safe_only and str(chg.risk_level).lower() != "low":
            continue
        if chg.approval_state == ConductorApprovalStateEnum.REJECTED.value and safe_only:
            continue
        chg.approval_state = ConductorApprovalStateEnum.APPROVED.value
        chg.updated_at = now

    _recompute_proposal_approval_status(proposal, changes)
    session.add(
        ConductorApproval(
            id=uuid.uuid4(),
            proposal_id=proposal.id,
            change_id=None,
            tenant_id=t_id,
            actor_user_id=actor_user_id,
            action=ConductorApprovalActionEnum.APPROVE_PROPOSAL.value,
            reason=str(reason or ("Approved safe changes" if safe_only else "Approved all changes")).strip(),
            previous_state=prev_prop_state,
            new_state=proposal.status,
            created_at=now,
        )
    )
    await session.flush()
    return proposal


async def reject_proposal_changes(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    actor_user_id: uuid.UUID | None,
    reason: str = "",
) -> ConductorProposal:
    """Reject all pending/approved changes on a proposal and move proposal to ``REJECTED``."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    p_id = _ensure_uuid(proposal_id, "proposal_id")
    proposal = await get_proposal_row(session, t_id, p_id)
    _ensure_reviewable(proposal)

    changes = await _load_proposal_changes(session, t_id, p_id)
    prev_prop_state = proposal.status
    now = _now()

    for chg in changes:
        chg.approval_state = ConductorApprovalStateEnum.REJECTED.value
        chg.updated_at = now

    _recompute_proposal_approval_status(proposal, changes)
    session.add(
        ConductorApproval(
            id=uuid.uuid4(),
            proposal_id=proposal.id,
            change_id=None,
            tenant_id=t_id,
            actor_user_id=actor_user_id,
            action=ConductorApprovalActionEnum.REJECT_PROPOSAL.value,
            reason=str(reason or "Rejected proposal changes").strip(),
            previous_state=prev_prop_state,
            new_state=proposal.status,
            created_at=now,
        )
    )
    await session.flush()
    return proposal


async def undo_proposal_approvals(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    actor_user_id: uuid.UUID | None,
    reason: str = "",
) -> ConductorProposal:
    """Reset all approved changes back to ``pending`` before final apply."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    p_id = _ensure_uuid(proposal_id, "proposal_id")
    proposal = await get_proposal_row(session, t_id, p_id)
    _ensure_reviewable(proposal)

    changes = await _load_proposal_changes(session, t_id, p_id)
    prev_prop_state = proposal.status
    now = _now()

    for chg in changes:
        if chg.approval_state == ConductorApprovalStateEnum.APPROVED.value:
            chg.approval_state = ConductorApprovalStateEnum.PENDING.value
            chg.updated_at = now

    _recompute_proposal_approval_status(proposal, changes)
    session.add(
        ConductorApproval(
            id=uuid.uuid4(),
            proposal_id=proposal.id,
            change_id=None,
            tenant_id=t_id,
            actor_user_id=actor_user_id,
            action=ConductorApprovalActionEnum.UNDO_PROPOSAL.value,
            reason=str(reason or "Undid pending approvals").strip(),
            previous_state=prev_prop_state,
            new_state=proposal.status,
            created_at=now,
        )
    )
    await session.flush()
    return proposal
