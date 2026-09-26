"""Tenant-scoped lead segments. Definitions are an allowlist, never raw SQL."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, scoped_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.leads import service
from app.leads.repository import get_segment, require_environment
from app.tenancy.isolation import HierarchyError, to_http

router = APIRouter(tags=["lead-segments"])


class SegmentBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    definition: dict
    environment_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


def _fail(exc: HierarchyError):
    raise to_http(exc) from None


@router.post("/api/tenants/{tenant_id}/lead-segments")
async def post_segment(
    tenant_id: uuid.UUID,
    payload: SegmentBody,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await service.create_segment(
            session,
            tenant_id=ctx.tenant_id,
            name=payload.name,
            definition=payload.definition,
            environment_id=payload.environment_id,
            claimed_tenant_id=payload.tenant_id,
            actor_id=ctx.user_id,
        )
        await session.commit()
    except HierarchyError as exc:
        _fail(exc)
    return {"id": str(row.id), "name": row.name, "definition": row.definition}


@router.get("/api/tenants/{tenant_id}/lead-segments")
async def list_segments(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import select

    from app.leads.models import LeadSegment

    try:
        environment = await require_environment(session, ctx.tenant_id, environment_id)
        rows = (
            await session.execute(
                select(LeadSegment)
                .where(
                    LeadSegment.tenant_id == ctx.tenant_id,
                    LeadSegment.environment_id == environment,
                )
                .order_by(LeadSegment.name)
            )
        ).scalars().all()
    except HierarchyError as exc:
        _fail(exc)
    return {"segments": [{"id": str(row.id), "name": row.name} for row in rows]}


@router.get("/api/tenants/{tenant_id}/lead-segments/{segment_id}/members")
async def get_members(
    tenant_id: uuid.UUID,
    segment_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        rows = await service.segment_members(
            session,
            tenant_id=ctx.tenant_id,
            segment_id=segment_id,
            environment_id=environment_id,
            limit=limit,
            offset=offset,
        )
    except HierarchyError as exc:
        _fail(exc)
    return {"leads": [service.lead_dict(row) for row in rows]}


@router.get("/api/tenants/{tenant_id}/lead-segments/{segment_id}")
async def get_one(
    tenant_id: uuid.UUID,
    segment_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        environment = await require_environment(session, ctx.tenant_id, environment_id)
        row = await get_segment(session, ctx.tenant_id, environment, segment_id)
    except HierarchyError as exc:
        _fail(exc)
    return {"id": str(row.id), "name": row.name, "definition": row.definition}
