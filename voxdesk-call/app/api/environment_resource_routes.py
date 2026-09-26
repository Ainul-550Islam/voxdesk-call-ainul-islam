"""List and inspect environment-scoped resources.

The path names the tenant and the environment. A body cannot choose a different
pair. Unknown resource types are rejected before any query runs.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_human_session
from app.auth.identity.events import emit
from app.db.models import AuditAction, Lead
from app.db.session import get_session
from app.environments.resource_types import EnvironmentResourceType
from app.resources.access import authorize, require_known_type
from app.resources.exceptions import InvalidResourceTransition
from app.resources.serialization import page_payload, scope_fields
from app.resources.service import bind_new_lead, read_one, read_page, refuse_rebind
from app.tenancy.isolation import HierarchyError, client_ip, to_http

router = APIRouter(
    prefix="/api/tenants/{tenant_id}/environments/{environment_id}/resources",
    tags=["environment-resources"],
)

_PAGE_MAX = 100


class LeadIn(BaseModel):
    name: str = Field(default="", max_length=200)
    phone: str = Field(min_length=3, max_length=32)
    email: str | None = Field(default=None, max_length=255)


class ArchiveIn(BaseModel):
    reason: str = Field(default="", max_length=200)


def _http(exc: HierarchyError):
    raise to_http(exc)


@router.get("")
async def list_supported(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant, environment = await authorize(
            session, ctx.user, tenant_id=tenant_id, environment_id=environment_id,
            resource_type=EnvironmentResourceType.CALL, action="read", scopes=ctx.scopes,
        )
    except HierarchyError as exc:
        _http(exc)
    return {
        "tenant_id": str(tenant.id),
        "environment_id": str(environment.id),
        "environment_kind": environment.kind,
        "environment_status": environment.status,
        "resources": [item.value for item in EnvironmentResourceType],
    }


@router.get("/{resource_type}")
async def list_resources(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    resource_type: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=_PAGE_MAX),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    try:
        kind = require_known_type(resource_type)
        _tenant, environment = await authorize(
            session, ctx.user, tenant_id=tenant_id, environment_id=environment_id,
            resource_type=kind, action="read", scopes=ctx.scopes,
        )
        rows, total = await read_page(
            session, kind, tenant_id=tenant_id, environment_id=environment_id,
            limit=limit, offset=offset,
        )
    except HierarchyError as exc:
        _http(exc)
    return page_payload(
        resource_type=kind.value,
        items=[scope_fields(row, environment) for row in rows],
        limit=limit,
        offset=offset,
        total=total,
    )


@router.get("/{resource_type}/{resource_id}")
async def get_resource(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    resource_type: str,
    resource_id: str,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    try:
        kind = require_known_type(resource_type)
        _tenant, environment = await authorize(
            session, ctx.user, tenant_id=tenant_id, environment_id=environment_id,
            resource_type=kind, action="read", scopes=ctx.scopes,
        )
        row = await read_one(
            session, kind, tenant_id=tenant_id, environment_id=environment_id,
            resource_id=resource_id,
        )
        if row is None:
            from app.tenancy.isolation import BoundaryDenied
            raise BoundaryDenied()
    except HierarchyError as exc:
        _http(exc)
    return scope_fields(row, environment)


@router.post("/leads", status_code=201)
async def create_lead(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    body: LeadIn,
    request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    try:
        _tenant, environment = await authorize(
            session, ctx.user, tenant_id=tenant_id, environment_id=environment_id,
            resource_type=EnvironmentResourceType.LEAD, action="create", scopes=ctx.scopes,
        )
        lead = await bind_new_lead(
            session, tenant_id=tenant_id, environment=environment,
            name=body.name, phone=body.phone, email=body.email,
        )
        await emit(
            session, AuditAction.RESOURCE_BOUND,
            tenant_id=tenant_id, actor_user_id=ctx.user.id, actor_email=ctx.user.email,
            ip_address=client_ip(request), user_agent=request.headers.get("user-agent", "")[:300],
            detail={
                "organization_id": str(ctx.tenant.organization_id),
                "tenant_id": str(tenant_id),
                "environment_id": str(environment.id),
                "resource_type": "lead",
                "resource_id": str(lead.id),
                "action": "create",
            },
            commit=False,
        )
        await session.commit()
        await session.refresh(lead)
    except HierarchyError as exc:
        await session.rollback()
        _http(exc)
    return scope_fields(lead, environment)


@router.post("/{resource_type}/{resource_id}/archive")
async def archive_resource(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    resource_type: str,
    resource_id: str,
    body: ArchiveIn,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    try:
        kind = require_known_type(resource_type)
        spec = __import__("app.resources.registry", fromlist=["spec_for"]).spec_for(kind)
        if not spec.supports_archive:
            raise InvalidResourceTransition("This resource cannot be archived here")
        _tenant, environment = await authorize(
            session, ctx.user, tenant_id=tenant_id, environment_id=environment_id,
            resource_type=kind, action="archive", scopes=ctx.scopes,
        )
        row = await read_one(
            session, kind, tenant_id=tenant_id, environment_id=environment_id,
            resource_id=resource_id,
        )
        if row is None:
            from app.tenancy.isolation import BoundaryDenied
            raise BoundaryDenied()
        refuse_rebind(row, environment.id)
        if isinstance(row, Lead):
            from app.db.models import LeadStatus
            row.status = LeadStatus.DNC
        else:
            from app.db.models import DocumentStatus, KnowledgeDocument
            if not isinstance(row, KnowledgeDocument):
                raise InvalidResourceTransition("This resource cannot be archived here")
            row.status = DocumentStatus.ARCHIVED
        await session.commit()
    except HierarchyError as exc:
        await session.rollback()
        _http(exc)
    return scope_fields(row, environment)
