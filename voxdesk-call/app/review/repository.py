"""Tenant-safe repository for review records.

All reads bind tenant and organization, and environment when one is supplied.
Mutation callers use ``for_update`` so a concurrent decision cannot advance a
stale version of the same case.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import ReviewAssignment, ReviewCase, ReviewDecision


def _scope_filter(model, *, tenant_id, organization_id, environment_id=None):
    clauses = [model.tenant_id == tenant_id, model.organization_id == organization_id]
    if environment_id is not None:
        clauses.append(model.environment_id == environment_id)
    return clauses


async def get_case(
    session: AsyncSession, *, tenant_id: uuid.UUID, organization_id: uuid.UUID,
    case_id: uuid.UUID, environment_id: uuid.UUID | None = None, lock: bool = False,
) -> ReviewCase | None:
    query = select(ReviewCase).where(ReviewCase.id == case_id, *_scope_filter(
        ReviewCase, tenant_id=tenant_id, organization_id=organization_id, environment_id=environment_id
    ))
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


async def get_assignment(session: AsyncSession, *, case: ReviewCase, assignment_id: uuid.UUID | None = None, reviewer_id: uuid.UUID | None = None, lock: bool = False) -> ReviewAssignment | None:
    query = select(ReviewAssignment).where(
        ReviewAssignment.case_id == case.id,
        *_scope_filter(ReviewAssignment, tenant_id=case.tenant_id, organization_id=case.organization_id, environment_id=case.environment_id),
        *( [ReviewAssignment.id == assignment_id] if assignment_id else []),
        *( [ReviewAssignment.reviewer_id == reviewer_id] if reviewer_id else []),
        ReviewAssignment.status == "active",
    ).order_by(ReviewAssignment.assignment_version.desc())
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


async def list_cases(session: AsyncSession, *, tenant_id: uuid.UUID, organization_id: uuid.UUID, environment_id: uuid.UUID | None = None, status: str | None = None, priority: str | None = None, case_type: str | None = None, agent_type: str | None = None, assigned_to: uuid.UUID | None = None, created_from=None, created_to=None, limit: int = 100, offset: int = 0) -> list[ReviewCase]:
    query = select(ReviewCase).where(*_scope_filter(ReviewCase, tenant_id=tenant_id, organization_id=organization_id, environment_id=environment_id))
    if status:
        query = query.where(ReviewCase.status == status)
    if priority:
        query = query.where(ReviewCase.priority == priority)
    if case_type:
        query = query.where(ReviewCase.case_type == case_type)
    if agent_type:
        query = query.where(ReviewCase.agent_type == agent_type)
    if created_from is not None:
        query = query.where(ReviewCase.created_at >= created_from)
    if created_to is not None:
        query = query.where(ReviewCase.created_at <= created_to)
    if assigned_to:
        query = query.join(ReviewAssignment, ReviewAssignment.case_id == ReviewCase.id).where(
            ReviewAssignment.reviewer_id == assigned_to,
            ReviewAssignment.status == "active",
            ReviewAssignment.tenant_id == tenant_id,
            ReviewAssignment.organization_id == organization_id,
        )
    query = query.order_by(ReviewCase.created_at.asc()).offset(offset).limit(limit)
    return list((await session.scalars(query)).all())


async def decisions(session: AsyncSession, case: ReviewCase) -> list[ReviewDecision]:
    query = select(ReviewDecision).where(
        ReviewDecision.case_id == case.id,
        *_scope_filter(ReviewDecision, tenant_id=case.tenant_id, organization_id=case.organization_id, environment_id=case.environment_id),
    ).order_by(ReviewDecision.decision_version.asc())
    return list((await session.scalars(query)).all())
