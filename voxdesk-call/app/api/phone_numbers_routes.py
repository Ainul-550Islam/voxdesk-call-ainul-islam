"""Authorized phone-number inventory.

The tenant comes from the authenticated principal. A path or body tenant id
that does not match is a 404. Responses never include a provider credential.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.identity.events import emit
from app.auth.permissions import Permission
from app.db.models import AuditAction
from app.db.session import get_session
from app.telephony.number_provisioning import (
    assign,
    capabilities_of,
    get_owned,
    list_owned,
    provision,
    release,
    search,
)
from app.telephony.provider_errors import TelephonyError
from app.telephony.providers.factory import public_config
from app.tenancy.isolation import HierarchyError, client_ip, to_http
from app.tenancy.policy import bind_tenant

router = APIRouter(tags=["phone-numbers"])


class SearchRequest(BaseModel):
    country: str = Field(min_length=2, max_length=2)
    provider: str = "twilio"
    limit: int = Field(default=10, ge=1, le=20)
    tenant_id: uuid.UUID | None = None


class ProvisionRequest(BaseModel):
    e164: str = Field(min_length=8, max_length=20)
    provider: str = "twilio"
    country: str = ""
    tenant_id: uuid.UUID | None = None


class AssignRequest(BaseModel):
    use: str = "voice"
    tenant_id: uuid.UUID | None = None


def _bind(ctx: TenantContext, tenant_id: uuid.UUID | None, body_tenant: uuid.UUID | None) -> None:
    target = tenant_id if tenant_id is not None else body_tenant
    if target is not None:
        bind_tenant(ctx, target)
    if body_tenant is not None and body_tenant != ctx.tenant_id:
        bind_tenant(ctx, body_tenant)


@router.get("/api/phone-numbers")
@router.get("/api/tenants/{tenant_id}/phone-numbers")
async def list_numbers(
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _bind(ctx, tenant_id, None)
        rows = await list_owned(session, ctx.tenant_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"numbers": [row.as_dict() for row in rows], "providers": public_config()}


@router.post("/api/phone-numbers/search")
@router.post("/api/tenants/{tenant_id}/phone-numbers/search")
async def search_numbers(
    payload: SearchRequest,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _bind(ctx, tenant_id, payload.tenant_id)
        found = await search(
            session,
            ctx.tenant,
            country=payload.country,
            provider=payload.provider,
            limit=payload.limit,
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    except TelephonyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    return {"numbers": found}


@router.post("/api/phone-numbers/provision", status_code=201)
@router.post("/api/tenants/{tenant_id}/phone-numbers/provision", status_code=201)
async def provision_number(
    payload: ProvisionRequest,
    request: Request,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _bind(ctx, tenant_id, payload.tenant_id)
        row = await provision(
            session,
            ctx.tenant,
            e164=payload.e164,
            provider=payload.provider,
            country=payload.country,
        )
        await emit(
            session,
            AuditAction.INTEGRATION_UPDATED,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            actor_email=ctx.user.email,
            ip_address=client_ip(request),
            detail={
                "operation": "phone_number_provisioned",
                "number_id": str(row.id),
                "provider": row.provider,
            },
            commit=False,
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    except TelephonyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    return row.as_dict()


@router.post("/api/phone-numbers/{number_id}/assign")
@router.post("/api/tenants/{tenant_id}/phone-numbers/{number_id}/assign")
async def assign_number(
    number_id: uuid.UUID,
    payload: AssignRequest,
    request: Request,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _bind(ctx, tenant_id, payload.tenant_id)
        row = await assign(session, ctx.tenant_id, number_id, use=payload.use)
        await emit(
            session,
            AuditAction.INTEGRATION_UPDATED,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            actor_email=ctx.user.email,
            ip_address=client_ip(request),
            detail={
                "operation": "phone_number_assigned",
                "number_id": str(row.id),
                "use": row.assigned_use,
            },
            commit=False,
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    except TelephonyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    return row.as_dict()


@router.post("/api/phone-numbers/{number_id}/release")
@router.post("/api/tenants/{tenant_id}/phone-numbers/{number_id}/release")
async def release_number(
    number_id: uuid.UUID,
    request: Request,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _bind(ctx, tenant_id, None)
        row = await release(session, ctx.tenant_id, number_id)
        await emit(
            session,
            AuditAction.INTEGRATION_UPDATED,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            actor_email=ctx.user.email,
            ip_address=client_ip(request),
            detail={
                "operation": "phone_number_released",
                "number_id": str(row.id),
                "provider": row.provider,
            },
            commit=False,
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    except TelephonyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    return row.as_dict()


@router.get("/api/phone-numbers/{number_id}/capabilities")
@router.get("/api/tenants/{tenant_id}/phone-numbers/{number_id}/capabilities")
async def number_capabilities(
    number_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _bind(ctx, tenant_id, None)
        row = await get_owned(session, ctx.tenant_id, number_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return capabilities_of(row)
