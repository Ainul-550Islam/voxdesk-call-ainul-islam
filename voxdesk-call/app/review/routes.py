"""Authenticated review queue and decision APIs."""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.governance.context import resolve_scope
from app.tenancy.isolation import BoundaryDenied, HierarchyError, to_http
from app.review import repository, service
from app.review.enums import ReviewCaseStatus, ReviewPriority
from app.review.schemas import (
    ReviewAssignmentOut, ReviewAssignmentRequest, ReviewCaseCreate, ReviewCaseOut,
    ReviewDecisionOut, ReviewDecisionRequest, RequestChangesRequest, ReviewDetailOut,
)

router = APIRouter(prefix="/api/reviews", tags=["reviews"])


def _case_out(row) -> ReviewCaseOut:
    return ReviewCaseOut.model_validate(row, from_attributes=True)


async def _case_scope(session: AsyncSession, ctx: TenantContext, case_id: uuid.UUID):
    base = await resolve_scope(session, ctx)
    case = await repository.get_case(session, tenant_id=ctx.tenant_id, organization_id=base.organization_id, case_id=case_id)
    if case is None:
        raise to_http(BoundaryDenied())
    return await resolve_scope(session, ctx, case.environment_id), case


@router.post("", response_model=ReviewCaseOut, status_code=201)
async def create_review_case(
    payload: ReviewCaseCreate,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    scope = await resolve_scope(session, ctx, payload.environment_id)
    try:
        case = await service.create_case(
            session, scope, actor_user_id=ctx.user_id, case_type=payload.case_type,
            agent_type=payload.agent_type, priority=payload.priority.value, reason=payload.reason, execution_id=payload.execution_id,
            subject_type=payload.subject_type, subject_id=payload.subject_id,
            requested_controls=payload.requested_controls, metadata=payload.metadata,
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _case_out(case)


@router.get("", response_model=list[ReviewCaseOut])
async def list_review_cases(
    environment_id: uuid.UUID | None = Query(None),
    status: ReviewCaseStatus | None = Query(None),
    priority: ReviewPriority | None = Query(None),
    case_type: str | None = Query(None, max_length=64),
    agent_type: str | None = Query(None, max_length=64),
    assigned_to: uuid.UUID | None = Query(None),
    reviewer_id: uuid.UUID | None = Query(None),
    created_from: dt.datetime | None = Query(None),
    created_to: dt.datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    if created_from is not None and created_to is not None and created_from > created_to:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="created_from must not be after created_to")
    scope = await resolve_scope(session, ctx, environment_id)
    rows = await repository.list_cases(
        session, tenant_id=ctx.tenant_id, organization_id=scope.organization_id,
        environment_id=scope.environment_id, status=status.value if status else None,
        priority=priority.value if priority else None, case_type=case_type, agent_type=agent_type,
        assigned_to=reviewer_id or assigned_to, created_from=created_from, created_to=created_to,
        limit=limit, offset=offset,
    )
    return [_case_out(row) for row in rows]


@router.get("/{case_id}", response_model=ReviewDetailOut)
async def get_review_case(
    case_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    scope, case = await _case_scope(session, ctx, case_id)
    assignment = await repository.get_assignment(session, case=case)
    decisions = await repository.decisions(session, case)
    return ReviewDetailOut(
        **_case_out(case).model_dump(),
        assignment=ReviewAssignmentOut.model_validate(assignment, from_attributes=True) if assignment else None,
        decisions=[ReviewDecisionOut.model_validate(row, from_attributes=True) for row in decisions],
    )


@router.post("/{case_id}/assign", response_model=ReviewDetailOut)
async def assign_review_case(
    case_id: uuid.UUID,
    payload: ReviewAssignmentRequest,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    scope, _ = await _case_scope(session, ctx, case_id)
    try:
        case, assignment = await service.assign_case(session, scope, case_id=case_id, reviewer_id=payload.reviewer_id, assigned_by=ctx.user_id, expires_at=payload.expires_at)
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    decisions = await repository.decisions(session, case)
    return ReviewDetailOut(**_case_out(case).model_dump(), assignment=ReviewAssignmentOut.model_validate(assignment, from_attributes=True), decisions=[ReviewDecisionOut.model_validate(row, from_attributes=True) for row in decisions])


@router.post("/{case_id}/start", response_model=ReviewCaseOut)
async def start_review_case(
    case_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    scope, _ = await _case_scope(session, ctx, case_id)
    try:
        case = await service.start_case(session, scope, case_id=case_id, reviewer_id=ctx.user_id)
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _case_out(case)


@router.post("/{case_id}/decision", response_model=ReviewDetailOut)
async def decide_review_case(
    case_id: uuid.UUID,
    payload: ReviewDecisionRequest,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    scope, _ = await _case_scope(session, ctx, case_id)
    try:
        case, _decision = await service.decide_case(session, scope, case_id=case_id, reviewer_id=ctx.user_id, decision=payload.decision.value, rationale=payload.rationale, evidence=payload.evidence)
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    assignment = await repository.get_assignment(session, case=case)
    decisions = await repository.decisions(session, case)
    return ReviewDetailOut(**_case_out(case).model_dump(), assignment=ReviewAssignmentOut.model_validate(assignment, from_attributes=True) if assignment else None, decisions=[ReviewDecisionOut.model_validate(row, from_attributes=True) for row in decisions])


@router.post("/{case_id}/request-changes", response_model=ReviewDetailOut)
async def request_review_changes(
    case_id: uuid.UUID,
    payload: RequestChangesRequest,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    scope, _ = await _case_scope(session, ctx, case_id)
    try:
        case, _decision = await service.request_changes(session, scope, case_id=case_id, reviewer_id=ctx.user_id, rationale=payload.rationale, evidence=payload.evidence)
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    assignment = await repository.get_assignment(session, case=case)
    decisions = await repository.decisions(session, case)
    return ReviewDetailOut(**_case_out(case).model_dump(), assignment=ReviewAssignmentOut.model_validate(assignment, from_attributes=True) if assignment else None, decisions=[ReviewDecisionOut.model_validate(row, from_attributes=True) for row in decisions])


@router.post("/{case_id}/cancel", response_model=ReviewCaseOut)
async def cancel_review_case(
    case_id: uuid.UUID,
    reason: str = Query(..., min_length=1, max_length=2000),
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    scope, _ = await _case_scope(session, ctx, case_id)
    try:
        case = await service.cancel_case(session, scope, case_id=case_id, actor_user_id=ctx.user_id, reason=reason)
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _case_out(case)


@router.post("/{case_id}/expire", response_model=ReviewCaseOut)
async def expire_review_case(
    case_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    scope, _ = await _case_scope(session, ctx, case_id)
    try:
        case = await service.expire_case(session, scope, case_id=case_id, actor_user_id=ctx.user_id)
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _case_out(case)
