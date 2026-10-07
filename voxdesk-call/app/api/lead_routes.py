"""Enterprise lead routes.

The legacy ``/api/tenants/{tenant_id}/leads`` collection stays on the existing
router. These paths do not replace it. A client ``tenant_id`` cannot retarget
the authenticated tenant.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, scoped_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.leads import service
from app.leads.repository import get_identity, get_lead
from app.tenancy.isolation import HierarchyError, to_http

router = APIRouter(tags=["leads"])


class LeadBody(BaseModel):
    phone: str
    name: str = ""
    email: str | None = None
    company: str | None = None
    notes: str | None = None
    campaign_id: uuid.UUID | None = None
    custom_fields: dict | None = None
    environment_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


class LeadUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    company: str | None = None
    notes: str | None = None
    phone: str | None = None
    campaign_id: uuid.UUID | None = None
    custom_fields: dict | None = None
    tenant_id: uuid.UUID | None = None
    status: str | None = None


class TransitionBody(BaseModel):
    status: str
    reason: str = ""
    expected: str | None = None
    tenant_id: uuid.UUID | None = None


class OwnerBody(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None = None


class ConsentBody(BaseModel):
    channel: str
    decision: str
    source: str = "api"
    tenant_id: uuid.UUID | None = None


class MergeBody(BaseModel):
    survivor_id: uuid.UUID
    duplicate_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=200)
    environment_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


def _fail(exc: HierarchyError):
    raise to_http(exc) from None


@router.post("/api/tenants/{tenant_id}/lead-records")
async def post_lead(
    tenant_id: uuid.UUID,
    payload: LeadBody,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_CREATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        lead, created = await service.create_lead(
            session,
            tenant_id=ctx.tenant_id,
            claimed_tenant_id=payload.tenant_id,
            phone=payload.phone,
            name=payload.name,
            email=payload.email,
            company=payload.company,
            notes=payload.notes,
            campaign_id=payload.campaign_id,
            custom_fields=payload.custom_fields,
            environment_id=payload.environment_id,
            actor_id=ctx.user_id,
        )
        await session.commit()
    except HierarchyError as exc:
        _fail(exc)
    body = service.lead_dict(lead)
    body["created"] = created
    return body


@router.get("/api/tenants/{tenant_id}/lead-records")
async def get_leads(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    status: str | None = None,
    phone: str | None = None,
    limit: int = 50,
    offset: int = 0,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        rows = await service.search(
            session,
            tenant_id=ctx.tenant_id,
            environment_id=environment_id,
            status=status,
            phone=phone,
            limit=limit,
            offset=offset,
        )
    except HierarchyError as exc:
        _fail(exc)
    return {"leads": [service.lead_dict(row) for row in rows]}


@router.get("/api/tenants/{tenant_id}/leads/{lead_id}")
async def get_one(
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        lead = await get_lead(session, ctx.tenant_id, lead_id)
        identity = await get_identity(session, ctx.tenant_id, lead.id)
    except HierarchyError as exc:
        _fail(exc)
    return service.lead_dict(lead, identity)


@router.patch("/api/tenants/{tenant_id}/leads/{lead_id}")
async def patch_lead(
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    payload: LeadUpdate,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        lead = await service.update_lead(
            session,
            tenant_id=ctx.tenant_id,
            lead_id=lead_id,
            claimed_tenant_id=payload.tenant_id,
            actor_id=ctx.user_id,
            fields=payload.model_dump(exclude_unset=True),
        )
        await session.commit()
    except HierarchyError as exc:
        _fail(exc)
    return service.lead_dict(lead)


@router.post("/api/tenants/{tenant_id}/leads/{lead_id}/transition")
async def post_transition(
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    payload: TransitionBody,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        lead = await service.change_status(
            session,
            tenant_id=ctx.tenant_id,
            lead_id=lead_id,
            target=payload.status,
            reason=payload.reason,
            expected=payload.expected,
            claimed_tenant_id=payload.tenant_id,
            actor_id=ctx.user_id,
        )
        await session.commit()
    except HierarchyError as exc:
        _fail(exc)
    return service.lead_dict(lead)


@router.post("/api/tenants/{tenant_id}/leads/{lead_id}/owner")
async def post_owner(
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    payload: OwnerBody,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        lead = await service.assign_owner(
            session,
            tenant_id=ctx.tenant_id,
            lead_id=lead_id,
            user_id=payload.user_id,
            actor_id=ctx.user_id,
        )
        await session.commit()
        identity = await get_identity(session, ctx.tenant_id, lead.id)
    except HierarchyError as exc:
        _fail(exc)
    return service.lead_dict(lead, identity)


@router.post("/api/tenants/{tenant_id}/leads/{lead_id}/score")
async def post_score(
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        body = await service.score_lead(session, tenant_id=ctx.tenant_id, lead_id=lead_id)
        await session.commit()
    except HierarchyError as exc:
        _fail(exc)
    return body


@router.get("/api/tenants/{tenant_id}/leads/{lead_id}/duplicates")
async def get_duplicates(
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await service.duplicates(session, tenant_id=ctx.tenant_id, lead_id=lead_id)
    except HierarchyError as exc:
        _fail(exc)


@router.post("/api/tenants/{tenant_id}/lead-merges")
async def post_merge(
    tenant_id: uuid.UUID,
    payload: MergeBody,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        body = await service.merge_leads(
            session,
            tenant_id=ctx.tenant_id,
            survivor_id=payload.survivor_id,
            duplicate_id=payload.duplicate_id,
            reason=payload.reason,
            environment_id=payload.environment_id,
            claimed_tenant_id=payload.tenant_id,
            actor_id=ctx.user_id,
        )
        await session.commit()
    except HierarchyError as exc:
        _fail(exc)
    return body


@router.post("/api/tenants/{tenant_id}/leads/{lead_id}/consent")
async def post_consent(
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    payload: ConsentBody,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await service.record_consent(
            session,
            tenant_id=ctx.tenant_id,
            lead_id=lead_id,
            channel=payload.channel,
            decision=payload.decision,
            source=payload.source,
            claimed_tenant_id=payload.tenant_id,
            actor_id=ctx.user_id,
        )
        await session.commit()
    except HierarchyError as exc:
        _fail(exc)
    return {"channel": row.channel, "decision": row.decision, "version": row.version}


@router.post("/api/tenants/{tenant_id}/leads/{lead_id}/enrich")
async def post_enrich(
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        body = await service.enrich(session, tenant_id=ctx.tenant_id, lead_id=lead_id)
        await session.commit()
    except HierarchyError as exc:
        _fail(exc)
    return body
