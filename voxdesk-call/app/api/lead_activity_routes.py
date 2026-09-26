"""Lead activities and tasks. Activities reference source objects; they do not copy transcripts."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, scoped_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.leads import service
from app.tenancy.isolation import HierarchyError, to_http

router = APIRouter(tags=["lead-activities"])


class ActivityBody(BaseModel):
    summary: str = Field(min_length=1, max_length=300)
    call_id: uuid.UUID | None = None
    appointment_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


class TaskBody(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    assignee_id: uuid.UUID | None = None
    due_at: datetime | None = None
    tenant_id: uuid.UUID | None = None


class TaskUpdate(BaseModel):
    status: str | None = None
    assignee_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


def _fail(exc: HierarchyError):
    raise to_http(exc) from None


@router.get("/api/tenants/{tenant_id}/leads/{lead_id}/activities")
async def get_activities(
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        rows = await service.lead_timeline(
            session,
            tenant_id=ctx.tenant_id,
            lead_id=lead_id,
            limit=limit,
            offset=offset,
        )
    except HierarchyError as exc:
        _fail(exc)
    return {"activities": rows}


@router.post("/api/tenants/{tenant_id}/leads/{lead_id}/activities")
async def post_activity(
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    payload: ActivityBody,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    if payload.tenant_id is not None and payload.tenant_id != ctx.tenant_id:
        _fail(HierarchyError("Not found", code="not_found", status_code=404))
    try:
        body = await service.add_note(
            session,
            tenant_id=ctx.tenant_id,
            lead_id=lead_id,
            summary=payload.summary,
            actor_id=ctx.user_id,
            call_id=payload.call_id,
            appointment_id=payload.appointment_id,
        )
        await session.commit()
    except HierarchyError as exc:
        _fail(exc)
    return body


@router.post("/api/tenants/{tenant_id}/leads/{lead_id}/tasks")
async def post_task(
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    payload: TaskBody,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    if payload.tenant_id is not None and payload.tenant_id != ctx.tenant_id:
        _fail(HierarchyError("Not found", code="not_found", status_code=404))
    try:
        row = await service.open_task(
            session,
            tenant_id=ctx.tenant_id,
            lead_id=lead_id,
            title=payload.title,
            assignee_id=payload.assignee_id,
            due_at=payload.due_at,
        )
        await session.commit()
    except HierarchyError as exc:
        _fail(exc)
    return {"id": str(row.id), "status": row.status, "title": row.title}


@router.patch("/api/tenants/{tenant_id}/lead-tasks/{task_id}")
async def patch_task(
    tenant_id: uuid.UUID,
    task_id: uuid.UUID,
    payload: TaskUpdate,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    if payload.tenant_id is not None and payload.tenant_id != ctx.tenant_id:
        _fail(HierarchyError("Not found", code="not_found", status_code=404))
    try:
        row = await service.update_task(
            session,
            tenant_id=ctx.tenant_id,
            task_id=task_id,
            status=payload.status,
            assignee_id=payload.assignee_id,
        )
        await session.commit()
    except HierarchyError as exc:
        _fail(exc)
    return {"id": str(row.id), "status": row.status}
