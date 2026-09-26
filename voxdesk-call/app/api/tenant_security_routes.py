"""Tenant security surface for residency, flags and redacted settings.

The path tenant is the authenticated tenant. A body tenant id that disagrees
is a boundary miss. Region and flag calls do not write product columns.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.organization.service import Actor
from app.tenancy import data_residency, feature_flags, settings as tenant_settings
from app.tenancy.context import load_hierarchy
from app.tenancy.exceptions import HierarchyError
from app.tenancy.isolation import client_ip, to_http
from app.tenancy.policy import bind_tenant, require_permission as require_can

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["tenant-security"])


class ResidencyIn(BaseModel):
    region: str = Field(min_length=1, max_length=64)
    tenant_id: uuid.UUID | None = None


class FlagEvalIn(BaseModel):
    flag: str = Field(min_length=1, max_length=64)
    tenant_id: uuid.UUID | None = None
    environment_override: bool | None = None
    tenant_override: bool | None = None
    organization_override: bool | None = None


def _actor(ctx: TenantContext, request: Request) -> Actor:
    return Actor(
        user_id=ctx.user_id,
        email=ctx.user.email,
        tenant_id=ctx.tenant_id,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )


@router.get("/security/posture")
async def security_posture(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Server-resolved hierarchy. The client does not supply the tenant."""
    try:
        bind_tenant(ctx, tenant_id)
        hierarchy = await load_hierarchy(session, ctx)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {
        "organization_id": str(hierarchy.organization_id),
        "tenant_id": str(hierarchy.tenant_id),
        "environment_id": str(hierarchy.environment_id) if hierarchy.environment_id else None,
        "default_environment_id": (
            str(hierarchy.default_environment_id) if hierarchy.default_environment_id else None
        ),
        "auth_method": hierarchy.auth_method,
        "is_machine": hierarchy.is_machine,
        "client_tenant_override": False,
    }


@router.get("/settings")
async def tenant_settings_view(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    try:
        bind_tenant(ctx, tenant_id)
        view = tenant_settings.public_view(ctx.tenant)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return view


@router.post("/residency")
async def residency_change(
    tenant_id: uuid.UUID,
    payload: ResidencyIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Evaluate a region label. A refusal is audited. Nothing is stored."""
    try:
        bind_tenant(ctx, tenant_id, payload.tenant_id)
        if not ctx.can(Permission.TENANT_UPDATE):
            await data_residency.audit_rejection(
                session,
                tenant_id=ctx.tenant_id,
                actor=_actor(ctx, request),
                requested=payload.region,
                reason="permission_denied",
            )
            await session.commit()
            require_can(ctx, Permission.TENANT_UPDATE)
        decision = data_residency.evaluate(payload.region)
        data_residency.require_applicable(decision)
    except HierarchyError as exc:
        if exc.code != "forbidden":
            try:
                await data_residency.audit_rejection(
                    session,
                    tenant_id=ctx.tenant_id,
                    actor=_actor(ctx, request),
                    requested=payload.region,
                    reason=exc.code,
                )
                await session.commit()
            except HierarchyError:
                raise to_http(exc) from None
        raise to_http(exc) from None
    return decision.as_dict()


@router.post("/feature-flags/evaluate")
async def evaluate_flag(
    tenant_id: uuid.UUID,
    payload: FlagEvalIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Evaluate a catalog flag. Client overrides are refused and not stored."""
    try:
        bind_tenant(ctx, tenant_id, payload.tenant_id)
        feature_flags.reject_client_override(
            environment_override=payload.environment_override,
            tenant_override=payload.tenant_override,
            organization_override=payload.organization_override,
        )
        if not ctx.can(Permission.TENANT_UPDATE):
            await data_residency.audit_rejection(
                session,
                tenant_id=ctx.tenant_id,
                actor=_actor(ctx, request),
                requested=payload.flag,
                reason="flag_permission_denied",
                operation="feature_flag_change",
            )
            await session.commit()
            require_can(ctx, Permission.TENANT_UPDATE)
        decision = feature_flags.evaluate(payload.flag, trust_overrides=False)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return decision.as_dict()
