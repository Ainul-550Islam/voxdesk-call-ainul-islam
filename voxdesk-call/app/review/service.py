"""Transactional review workflow service.

This module is the only writer for review lifecycle state.  It deliberately
keeps authorization at the route/dependency boundary and policy decisions in
the existing governance PDP, while owning row locking, stale-state rejection,
audit/evidence append, and execution review-state synchronization.
"""
from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.governance.context import GovernanceScope
from app.governance.enums import DecisionType
from app.governance.evidence import append_event
from app.governance.models import GovernancePolicy
from app.governance.policy import evaluate_policy
from app.governance.audit import record_governance_audit
from app.governance.hashing import sha256_hex
from app.db.models import Environment, User
from app.specialized_agents.executor import SpecializedExecutionRecord
from app.specialized_agents.enums import ReviewState
from app.tenancy.isolation import BoundaryDenied, Forbidden, LifecycleDenied, ValidationFailed
from . import repository
from .enums import ReviewAssignmentStatus, ReviewCaseStatus, ReviewDecisionType, ReviewPriority, TERMINAL_CASE_STATES
from .models import ReviewAssignment, ReviewCase, ReviewDecision


_ALLOWED_CASE_TYPES = frozenset({"legal", "translation", "insight", "forecast", "anomaly", "compliance", "deployment", "specialized_execution"})
_ALLOWED_AGENT_TYPES = frozenset({"legal", "translation", "insight", "forecast", "forecasting", "anomaly", "compliance", "deployment", "intake_flow", "virtual_paralegal", "billing_guard", "billing_ops", "ocg_compliance", "qms_compliance", "healthcare", "manufacturing", "retail", "metrics_insights", "specialized_execution"})
_SECRET_VALUE = re.compile(
    r"(?:bearer\s+[A-Za-z0-9._~+/-]{8,}|\bsk-[A-Za-z0-9_-]{16,}|"
    r"\b(?:api[_-]?key|token|secret)\s*[:=]\s*[A-Za-z0-9._~+/-]{8,})",
    re.IGNORECASE,
)


def _safe_text(value: str) -> str:
    return _SECRET_VALUE.sub("[redacted]", value)[:10_000]


def _safe_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_values(item) for item in value]
    if isinstance(value, str):
        return _safe_text(value)
    return value


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _validate_scope(case: ReviewCase, scope: GovernanceScope) -> None:
    if case.tenant_id != scope.tenant_id or case.organization_id != scope.organization_id:
        raise BoundaryDenied()
    if scope.environment_id is not None and case.environment_id != scope.environment_id:
        raise BoundaryDenied()


async def _validate_reviewer(session: AsyncSession, scope: GovernanceScope, reviewer_id: uuid.UUID) -> User:
    reviewer = await session.scalar(select(User).where(User.id == reviewer_id, User.tenant_id == scope.tenant_id))
    if reviewer is None or not reviewer.is_active:
        raise BoundaryDenied()
    if scope.environment_id is not None:
        environment = await session.get(Environment, scope.environment_id)
        if environment is None or environment.tenant_id != scope.tenant_id:
            raise BoundaryDenied()
    return reviewer


async def create_case(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    actor_user_id: uuid.UUID | None,
    case_type: str,
    agent_type: str | None = None,
    priority: str = ReviewPriority.NORMAL.value,
    reason: str = "Human review required by governed execution",
    execution_id: uuid.UUID | None = None,
    subject_type: str = "specialized_execution",
    subject_id: str | None = None,
    requested_controls: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ReviewCase:
    if scope.environment_id is None:
        raise ValidationFailed("review cases require an authorized environment scope")
    if case_type not in _ALLOWED_CASE_TYPES:
        raise ValidationFailed("unsupported review case type")
    if (agent_type or case_type) not in _ALLOWED_AGENT_TYPES:
        raise ValidationFailed("unsupported review agent type")
    if priority not in {item.value for item in ReviewPriority}:
        raise ValidationFailed("unsupported review priority")
    if not reason or len(reason) > 2_000:
        raise ValidationFailed("review reason is required and must be <= 2000 characters")
    if actor_user_id is not None:
        await _validate_reviewer(session, scope, actor_user_id)
    if execution_id is not None:
        execution = await session.scalar(select(SpecializedExecutionRecord).where(
            SpecializedExecutionRecord.id == execution_id,
            SpecializedExecutionRecord.tenant_id == scope.tenant_id,
            SpecializedExecutionRecord.organization_id == scope.organization_id,
            SpecializedExecutionRecord.environment_id == scope.environment_id,
        ))
        if execution is None:
            raise BoundaryDenied()
        subject_id = subject_id or str(execution_id)
        existing = await session.scalar(select(ReviewCase).where(
            ReviewCase.execution_id == execution_id,
            ReviewCase.tenant_id == scope.tenant_id,
            ReviewCase.organization_id == scope.organization_id,
            ReviewCase.environment_id == scope.environment_id,
            ReviewCase.case_type == case_type,
            ReviewCase.status.not_in(tuple(TERMINAL_CASE_STATES)),
        ).order_by(ReviewCase.created_at.desc()).limit(1))
        if existing is not None:
            return existing
    if not subject_id:
        raise ValidationFailed("review case requires a subject_id or execution_id")
    safe_metadata = _safe_values(dict(metadata or {}))
    safe_subject_id = _safe_text(subject_id)
    safe_reason = _safe_text(reason)
    # Review metadata is references and control state, never raw provider data.
    try:
        from app.jobs.types import ensure_payload_safe
        ensure_payload_safe(safe_metadata)
    except Exception as exc:
        raise ValidationFailed("review metadata is not safe to persist") from exc
    requested_controls = list(requested_controls or [])
    row = ReviewCase(
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        environment_id=scope.environment_id,
        execution_id=execution_id,
        case_type=case_type,
        agent_type=agent_type or case_type,
        subject_type=subject_type,
        subject_id=safe_subject_id,
        status=ReviewCaseStatus.PENDING.value,
        priority=priority,
        reason=safe_reason,
        requested_controls=requested_controls,
        requested_by=actor_user_id,
        metadata_json={**safe_metadata, "requester_id": str(actor_user_id) if actor_user_id else None},
    )
    session.add(row)
    await session.flush()
    await record_governance_audit(
        session, scope, event="review_case_created", actor_user_id=actor_user_id,
        detail={"review_case_id": str(row.id), "case_type": case_type, "subject_id": subject_id},
    )
    await append_event(
        session, scope, event_type="review_case_created",
        payload={"review_case_id": str(row.id), "case_type": case_type, "subject_id": subject_id},
        actor_user_id=actor_user_id, correlation_id=str(row.id), subject_type="review_case", subject_id=str(row.id),
    )
    return row


async def assign_case(
    session: AsyncSession, scope: GovernanceScope, *, case_id: uuid.UUID,
    reviewer_id: uuid.UUID, assigned_by: uuid.UUID, expires_at: dt.datetime | None = None,
) -> tuple[ReviewCase, ReviewAssignment]:
    case = await repository.get_case(session, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, case_id=case_id, lock=True)
    if case is None:
        raise BoundaryDenied()
    _validate_scope(case, scope)
    if case.status not in {ReviewCaseStatus.PENDING.value, ReviewCaseStatus.ASSIGNED.value, ReviewCaseStatus.IN_REVIEW.value, ReviewCaseStatus.CHANGES_REQUESTED.value}:
        raise LifecycleDenied("review case state cannot be assigned")
    await _validate_reviewer(session, scope, reviewer_id)
    await _validate_reviewer(session, scope, assigned_by)
    active = await repository.get_assignment(session, case=case, lock=True)
    latest = await session.scalar(select(ReviewAssignment).where(ReviewAssignment.case_id == case.id, ReviewAssignment.tenant_id == case.tenant_id, ReviewAssignment.organization_id == case.organization_id, ReviewAssignment.environment_id == case.environment_id).order_by(ReviewAssignment.assignment_version.desc()).limit(1).with_for_update())
    if active is not None and active.reviewer_id == reviewer_id:
        return case, active
    if active is not None:
        active.status = ReviewAssignmentStatus.CANCELLED.value
        active.completed_at = _now()
    version = (latest.assignment_version + 1) if latest else 1
    assignment = ReviewAssignment(
        case_id=case.id, tenant_id=case.tenant_id, organization_id=case.organization_id,
        environment_id=case.environment_id, reviewer_id=reviewer_id, assigned_by=assigned_by,
        status=ReviewAssignmentStatus.ACTIVE.value, assignment_version=version, expires_at=expires_at,
    )
    session.add(assignment)
    case.status = ReviewCaseStatus.ASSIGNED.value
    case.version += 1
    await session.flush()
    await record_governance_audit(session, scope, event="review_case_assigned", actor_user_id=assigned_by, detail={"review_case_id": str(case.id), "reviewer_id": str(reviewer_id), "assignment_id": str(assignment.id)})
    await append_event(session, scope, event_type="review_case_assigned", payload={"review_case_id": str(case.id), "assignment_id": str(assignment.id), "reviewer_id": str(reviewer_id)}, actor_user_id=assigned_by, correlation_id=str(case.id), subject_type="review_case", subject_id=str(case.id))
    return case, assignment


async def start_case(session: AsyncSession, scope: GovernanceScope, *, case_id: uuid.UUID, reviewer_id: uuid.UUID) -> ReviewCase:
    case = await repository.get_case(session, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, case_id=case_id, lock=True)
    if case is None:
        raise BoundaryDenied()
    await _validate_reviewer(session, scope, reviewer_id)
    assignment = await repository.get_assignment(session, case=case, reviewer_id=reviewer_id, lock=True)
    if assignment is None:
        raise Forbidden("Only the active assigned reviewer may start a case")
    if case.status != ReviewCaseStatus.ASSIGNED.value:
        raise LifecycleDenied("case is not awaiting review start")
    if assignment.expires_at is not None and assignment.expires_at <= _now():
        assignment.status = ReviewAssignmentStatus.EXPIRED.value
        case.status = ReviewCaseStatus.EXPIRED.value
        case.completed_at = _now()
        case.version += 1
        await session.flush()
        await _record_lifecycle(session, scope, case, event_type="review_case_expired", actor_user_id=reviewer_id, extra={"assignment_id": str(assignment.id)})
        return case
    case.status = ReviewCaseStatus.IN_REVIEW.value
    case.version += 1
    await session.flush()
    await record_governance_audit(session, scope, event="review_case_started", actor_user_id=reviewer_id, detail={"review_case_id": str(case.id), "assignment_id": str(assignment.id)})
    await append_event(session, scope, event_type="review_case_started", payload={"review_case_id": str(case.id), "assignment_id": str(assignment.id)}, actor_user_id=reviewer_id, correlation_id=str(case.id), subject_type="review_case", subject_id=str(case.id))
    return case


async def _decision_policy(session: AsyncSession, scope: GovernanceScope, case: ReviewCase, reviewer_id: uuid.UUID, decision: str):
    requester_id = (case.metadata_json or {}).get("requester_id")
    self_review = bool(requester_id and str(reviewer_id) == str(requester_id))
    policy_decision = await evaluate_policy(
        session, scope, policy_type="review_decision",
        context={"review_case_id": str(case.id), "case_type": case.case_type, "decision": decision, "self_review": self_review, "required_human_approval": True},
        principal_id=reviewer_id, correlation_id=str(case.id),
    )
    if policy_decision.decision != DecisionType.ALLOW.value:
        raise Forbidden(f"review decision denied by governance policy: {policy_decision.reason_code}")
    if self_review:
        policy = await session.get(GovernancePolicy, policy_decision.policy_id) if policy_decision.policy_id else None
        if policy is None or not bool((policy.rules or {}).get("allow_self_review", False)):
            raise Forbidden("governance policy forbids self-approval")
    return policy_decision


async def decide_case(
    session: AsyncSession, scope: GovernanceScope, *, case_id: uuid.UUID, reviewer_id: uuid.UUID,
    decision: str, rationale: str, evidence: dict[str, Any] | None = None,
) -> tuple[ReviewCase, ReviewDecision]:
    if decision not in {item.value for item in ReviewDecisionType}:
        raise ValidationFailed("unsupported review decision")
    if not rationale.strip():
        raise ValidationFailed("review rationale is required")
    case = await repository.get_case(session, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, case_id=case_id, lock=True)
    if case is None:
        raise BoundaryDenied()
    if case.status != ReviewCaseStatus.IN_REVIEW.value:
        raise LifecycleDenied("stale or invalid review state; case must be in_review")
    await _validate_reviewer(session, scope, reviewer_id)
    assignment = await repository.get_assignment(session, case=case, reviewer_id=reviewer_id, lock=True)
    if assignment is None:
        raise Forbidden("only the active assigned reviewer may decide")
    if assignment.expires_at is not None and assignment.expires_at <= _now():
        raise LifecycleDenied("review assignment has expired; expire the case before deciding")
    try:
        from app.jobs.types import ensure_payload_safe
        ensure_payload_safe(evidence or {})
    except Exception as exc:
        raise ValidationFailed("review evidence is not safe to persist") from exc
    policy_decision = await _decision_policy(session, scope, case, reviewer_id, decision)
    next_status = {
        ReviewDecisionType.APPROVE.value: ReviewCaseStatus.APPROVED.value,
        ReviewDecisionType.REJECT.value: ReviewCaseStatus.REJECTED.value,
        ReviewDecisionType.REQUEST_CHANGES.value: ReviewCaseStatus.CHANGES_REQUESTED.value,
    }[decision]
    previous = await session.scalar(select(ReviewDecision).where(ReviewDecision.case_id == case.id).order_by(ReviewDecision.decision_version.desc()).limit(1))
    decision_row = ReviewDecision(
        case_id=case.id, assignment_id=assignment.id, tenant_id=case.tenant_id,
        organization_id=case.organization_id, environment_id=case.environment_id,
        reviewer_id=reviewer_id, decision=decision, rationale=_safe_text(rationale),
        evidence=_safe_values(dict(evidence or {})), policy_decision_id=policy_decision.id,
        decision_version=(previous.decision_version + 1) if previous else 1,
    )
    session.add(decision_row)
    case.status = next_status
    case.version += 1
    assignment.status = ReviewAssignmentStatus.COMPLETED.value
    assignment.completed_at = _now()
    if next_status in TERMINAL_CASE_STATES:
        case.completed_at = _now()
    await session.flush()
    event_name = "review_case_" + {"approve": "approved", "reject": "rejected", "request_changes": "changes_requested"}[decision]
    evidence_event = await append_event(
        session, scope, event_type=event_name,
        payload={"review_case_id": str(case.id), "decision_id": str(decision_row.id), "decision": decision, "rationale_fingerprint": sha256_hex(rationale), "evidence_keys": sorted((evidence or {}).keys())},
        actor_user_id=reviewer_id, correlation_id=str(case.id), subject_type="review_case", subject_id=str(case.id),
    )
    await session.flush()
    decision_row.evidence_event_id = evidence_event.id
    await record_governance_audit(session, scope, event=event_name, actor_user_id=reviewer_id, detail={"review_case_id": str(case.id), "decision_id": str(decision_row.id), "decision": decision, "evidence_event_id": str(evidence_event.id)})
    if case.execution_id is not None and case.case_type == "legal":
        from app.legal.persistence import LegalOutcomeRecord, LegalReviewRecord
        legal_review = await session.get(LegalReviewRecord, case.execution_id)
        if legal_review is not None:
            legal_review.review_state = {"approve": "approved", "reject": "rejected", "request_changes": "required"}[decision]
            legal_review.status = {"approve": "human_reviewed", "reject": "rejected", "request_changes": "changes_requested"}[decision]
            if decision in {"approve", "reject"}:
                legal_review.review_required = False
                legal_review.completed_at = _now()
        session.add(LegalOutcomeRecord(
            execution_id=case.execution_id, review_case_id=case.id,
            tenant_id=case.tenant_id, organization_id=case.organization_id,
            environment_id=case.environment_id, outcome_version=decision_row.decision_version,
            outcome={"approve": "human_reviewed", "reject": "rejected", "request_changes": "changes_requested"}[decision],
            rationale_fingerprint=sha256_hex(rationale), evidence_event_id=evidence_event.id,
            metadata_json={"decision_id": str(decision_row.id), "legal_validity": "not_assessed"},
        ))
    if case.execution_id is not None and case.case_type == "translation":
        from app.translation.persistence import TranslationJobRecord, TranslationProgressRecord
        translation_job = await session.get(TranslationJobRecord, case.execution_id)
        progress = await session.get(TranslationProgressRecord, case.execution_id)
        if translation_job is not None:
            translation_job.status = {"approve": "completed", "reject": "failed", "request_changes": "review_required"}[decision]
            translation_job.review_state = {"approve": "approved", "reject": "rejected", "request_changes": "required"}[decision]
            translation_job.completed_at = _now() if decision == "approve" else None
        if progress is not None:
            progress.state = {"approve": "completed", "reject": "failed", "request_changes": "review_required"}[decision]
            progress.review_state = {"approve": "approved", "reject": "rejected", "request_changes": "required"}[decision]
    if case.case_type == "compliance" and case.subject_type == "compliance_finding":
        from app.compliance.models import ComplianceFinding
        finding = await session.scalar(select(ComplianceFinding).where(
            ComplianceFinding.id == uuid.UUID(case.subject_id),
            ComplianceFinding.tenant_id == case.tenant_id,
            ComplianceFinding.organization_id == case.organization_id,
            ComplianceFinding.environment_id == case.environment_id,
        ).with_for_update())
        if finding is not None:
            finding.review_required = decision == ReviewDecisionType.REQUEST_CHANGES.value
            finding.result = {**(finding.result or {}), "human_review_outcome": {"approve": "reviewed", "reject": "rejected", "request_changes": "changes_requested"}[decision], "review_decision_id": str(decision_row.id), "review_evidence_event_id": str(evidence_event.id)}
            await session.flush()
    if case.case_type == "deployment" and case.subject_type == "deployment_revision":
        from app.deployment.models import DeploymentRevision
        revision = await session.scalar(select(DeploymentRevision).where(
            DeploymentRevision.id == uuid.UUID(case.subject_id),
            DeploymentRevision.tenant_id == case.tenant_id,
            DeploymentRevision.organization_id == case.organization_id,
            DeploymentRevision.environment_id == case.environment_id,
        ).with_for_update())
        if revision is not None:
            if decision == ReviewDecisionType.APPROVE.value:
                if revision.state != "validated":
                    raise LifecycleDenied("deployment revision is not awaiting approval")
                revision.state = "approved"
                revision.policy_decision_id = policy_decision.id
                revision.evidence_root = evidence_event.event_hash
            elif decision == ReviewDecisionType.REJECT.value:
                revision.state = "failed"
                revision.evidence_root = evidence_event.event_hash
            await session.flush()
    if case.execution_id is not None:
        execution = await session.scalar(select(SpecializedExecutionRecord).where(
            SpecializedExecutionRecord.id == case.execution_id,
            SpecializedExecutionRecord.tenant_id == case.tenant_id,
            SpecializedExecutionRecord.organization_id == case.organization_id,
            SpecializedExecutionRecord.environment_id == case.environment_id,
        ).with_for_update())
        if execution is not None:
            execution.review_state = {ReviewDecisionType.APPROVE.value: ReviewState.APPROVED.value, ReviewDecisionType.REJECT.value: ReviewState.REJECTED.value, ReviewDecisionType.REQUEST_CHANGES.value: ReviewState.REQUIRED.value}[decision]
            if decision == ReviewDecisionType.APPROVE.value and execution.status == "review_required":
                execution.status = "succeeded"
            elif decision == ReviewDecisionType.REJECT.value and execution.status == "review_required":
                execution.status = "failed"
            await session.flush()
    return case, decision_row


async def _record_lifecycle(session: AsyncSession, scope: GovernanceScope, case: ReviewCase, *, event_type: str, actor_user_id: uuid.UUID | None, extra: dict[str, Any] | None = None) -> None:
    payload = {"review_case_id": str(case.id), "status": case.status, "case_version": case.version, **(extra or {})}
    await record_governance_audit(session, scope, event=event_type, actor_user_id=actor_user_id, detail=payload)
    await append_event(session, scope, event_type=event_type, payload=payload, actor_user_id=actor_user_id, correlation_id=str(case.id), subject_type="review_case", subject_id=str(case.id))


async def cancel_case(session: AsyncSession, scope: GovernanceScope, *, case_id: uuid.UUID, actor_user_id: uuid.UUID, reason: str) -> ReviewCase:
    if not reason.strip():
        raise ValidationFailed("cancellation reason is required")
    case = await repository.get_case(session, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, case_id=case_id, lock=True)
    if case is None:
        raise BoundaryDenied()
    if case.status not in {ReviewCaseStatus.PENDING.value, ReviewCaseStatus.ASSIGNED.value, ReviewCaseStatus.IN_REVIEW.value, ReviewCaseStatus.CHANGES_REQUESTED.value}:
        raise LifecycleDenied("review case state cannot be cancelled")
    assignment = await repository.get_assignment(session, case=case, lock=True)
    if assignment is not None:
        assignment.status = ReviewAssignmentStatus.CANCELLED.value
        assignment.completed_at = _now()
    case.status = ReviewCaseStatus.CANCELLED.value
    case.completed_at = _now()
    case.version += 1
    await session.flush()
    await _record_lifecycle(session, scope, case, event_type="review_case_cancelled", actor_user_id=actor_user_id, extra={"reason_fingerprint": sha256_hex(reason)})
    return case


async def expire_case(session: AsyncSession, scope: GovernanceScope, *, case_id: uuid.UUID, actor_user_id: uuid.UUID | None = None) -> ReviewCase:
    case = await repository.get_case(session, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, case_id=case_id, lock=True)
    if case is None:
        raise BoundaryDenied()
    if case.status in TERMINAL_CASE_STATES:
        raise LifecycleDenied("terminal review cases cannot expire")
    assignment = await repository.get_assignment(session, case=case, lock=True)
    now = _now()
    if assignment is not None and (assignment.expires_at is None or assignment.expires_at > now):
        raise LifecycleDenied("review assignment has not expired")
    if assignment is None and case.status != ReviewCaseStatus.PENDING.value:
        raise LifecycleDenied("case has no expirable active assignment")
    if assignment is not None:
        assignment.status = ReviewAssignmentStatus.EXPIRED.value
        assignment.completed_at = now
    case.status = ReviewCaseStatus.EXPIRED.value
    case.completed_at = now
    case.version += 1
    await session.flush()
    await _record_lifecycle(session, scope, case, event_type="review_case_expired", actor_user_id=actor_user_id)
    return case


async def request_changes(session: AsyncSession, scope: GovernanceScope, *, case_id: uuid.UUID, reviewer_id: uuid.UUID, rationale: str, evidence: dict[str, Any] | None = None) -> tuple[ReviewCase, ReviewDecision]:
    """Explicit endpoint synonym; decision and rationale remain durable."""
    return await decide_case(session, scope, case_id=case_id, reviewer_id=reviewer_id, decision=ReviewDecisionType.REQUEST_CHANGES.value, rationale=rationale, evidence=evidence)
