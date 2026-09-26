"""Queue configuration and entry intake.

The tenant is the authenticated principal. A client ``tenant_id`` that does
not match is a 404, not a switch.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.contact_center import metrics, queues
from app.contact_center.exceptions import AcdError
from app.contact_center.repository import get_queue, list_queues, members
from app.contact_center.service import enqueue
from app.db.session import get_session
from app.tenancy.context import reject_client_override
from app.tenancy.isolation import HierarchyError, to_http
from app.tenancy.policy import bind_tenant

router = APIRouter(tags=["queues"])


class QueueBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    priority: int = Field(default=0, ge=0)
    strategy: str = "least_loaded"
    required_skills: list[str] = Field(default_factory=list)
    max_concurrency: int = Field(default=10, ge=1)
    overflow_policy: dict | None = None
    environment_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


class QueuePatch(BaseModel):
    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0)
    strategy: str | None = None
    required_skills: list[str] | None = None
    max_concurrency: int | None = Field(default=None, ge=1)
    overflow_policy: dict | None = None
    tenant_id: uuid.UUID | None = None


class MemberBody(BaseModel):
    user_id: uuid.UUID
    priority: int = 0
    capacity: int | None = Field(default=None, ge=1)
    tenant_id: uuid.UUID | None = None


class EntryBody(BaseModel):
    call_id: uuid.UUID
    priority: int = Field(default=0, ge=0)
    tenant_id: uuid.UUID | None = None


def _scope(ctx: TenantContext, tenant_id: uuid.UUID | None, claimed: uuid.UUID | None) -> uuid.UUID:
    reject_client_override(ctx, claimed)
    if tenant_id is not None:
        bind_tenant(ctx, tenant_id, claimed)
    return ctx.tenant_id


def _changes(payload: QueuePatch) -> dict:
    data = payload.model_dump(exclude_none=True)
    data.pop("tenant_id", None)
    return data


@router.get("/api/queues")
@router.get("/api/tenants/{tenant_id}/queues")
async def get_queues(
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, None)
        rows = await list_queues(session, tenant)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"queues": [row.as_dict() for row in rows]}


@router.post("/api/queues", status_code=201)
@router.post("/api/tenants/{tenant_id}/queues", status_code=201)
async def post_queue(
    payload: QueueBody,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, payload.tenant_id)
        row = await queues.create_queue(
            session,
            tenant_id=tenant,
            name=payload.name,
            environment_id=payload.environment_id,
            priority=payload.priority,
            strategy=payload.strategy,
            required_skills=payload.required_skills,
            max_concurrency=payload.max_concurrency,
            overflow_policy=payload.overflow_policy,
            description=payload.description,
        )
        await session.commit()
    except AcdError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.get("/api/queues/{queue_id}")
@router.get("/api/tenants/{tenant_id}/queues/{queue_id}")
async def get_one(
    queue_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, None)
        row = await get_queue(session, tenant, queue_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.patch("/api/queues/{queue_id}")
@router.patch("/api/tenants/{tenant_id}/queues/{queue_id}")
async def patch_queue(
    queue_id: uuid.UUID,
    payload: QueuePatch,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, payload.tenant_id)
        row = await get_queue(session, tenant, queue_id)
        await queues.update_queue(session, row, _changes(payload))
        await session.commit()
    except AcdError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.post("/api/queues/{queue_id}/members", status_code=201)
@router.post("/api/tenants/{tenant_id}/queues/{queue_id}/members", status_code=201)
async def post_member(
    queue_id: uuid.UUID,
    payload: MemberBody,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, payload.tenant_id)
        row = await queues.add_member(
            session,
            tenant_id=tenant,
            queue_id=queue_id,
            user_id=payload.user_id,
            priority=payload.priority,
            capacity=payload.capacity,
        )
        await session.commit()
    except AcdError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.delete("/api/queues/{queue_id}/members/{user_id}")
@router.delete("/api/tenants/{tenant_id}/queues/{queue_id}/members/{user_id}")
async def delete_member(
    queue_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, None)
        row = await queues.disable_member(session, tenant, queue_id, user_id)
        await session.commit()
    except AcdError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.get("/api/queues/{queue_id}/members")
@router.get("/api/tenants/{tenant_id}/queues/{queue_id}/members")
async def get_members(
    queue_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, None)
        await get_queue(session, tenant, queue_id)
        rows = await members(session, tenant, queue_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"members": [row.as_dict() for row in rows]}


@router.get("/api/queues/{queue_id}/metrics")
@router.get("/api/tenants/{tenant_id}/queues/{queue_id}/metrics")
async def get_metrics(
    queue_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.QUEUE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, None)
        await get_queue(session, tenant, queue_id)
        snap = await metrics.snapshot(session, tenant, queue_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return snap


@router.post("/api/queues/{queue_id}/entries", status_code=201)
@router.post("/api/tenants/{tenant_id}/queues/{queue_id}/entries", status_code=201)
async def post_entry(
    queue_id: uuid.UUID,
    payload: EntryBody,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.ROUTING_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, payload.tenant_id)
        result = await enqueue(
            session,
            tenant_id=tenant,
            queue_id=queue_id,
            call_id=payload.call_id,
            priority=payload.priority,
            actor_id=ctx.user_id,
        )
        await session.commit()
    except AcdError as exc:
        await session.commit()
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    body = result.as_dict()
    if result.outcome in {"escalate_failed", "escalate_unavailable"}:
        raise HTTPException(status_code=409, detail=body)
    return body
