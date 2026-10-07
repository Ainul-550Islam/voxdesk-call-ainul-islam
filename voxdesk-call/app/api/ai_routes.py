"""AI governance reads and policy writes.

The authenticated tenant is the only tenant. A body tenant id that disagrees
is a boundary miss. Responses never include a provider API key.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.identity.events import emit
from app.auth.permissions import Permission
from app.billing.metering import used_quantity
from app.billing.periods import calendar_period
from app.db.models import AuditAction, UsageMetric
from app.db.session import get_session
from app.organization.service import Actor
from app.ai.costs import estimate
from app.ai.gateway import health_view, load_policy, providers_view, save_policy
from app.ai.models import GovernanceError
from app.tenancy.isolation import HierarchyError, client_ip, to_http
from app.tenancy.policy import bind_tenant, require_permission as require_can

router = APIRouter(tags=["ai-governance"])


class PolicyPatch(BaseModel):
    tenant_id: uuid.UUID | None = None
    allowed_presets: list[str] | None = None
    disabled_providers: list[str] | None = None
    development_only_presets: list[str] | None = None
    token_ceiling: int | None = None
    status: str = "active"
    ceiling_supplied: bool = False


def _actor(ctx: TenantContext, request: Request) -> Actor:
    return Actor(
        user_id=ctx.user_id,
        email=ctx.user.email,
        tenant_id=ctx.tenant_id,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )


async def _audit(session, ctx, request, operation: str, detail: dict) -> None:
    who = _actor(ctx, request)
    await emit(
        session,
        AuditAction.SECURITY_SETTINGS_CHANGED,
        tenant_id=ctx.tenant_id,
        actor_user_id=who.user_id,
        actor_email=who.email,
        ip_address=who.ip_address,
        user_agent=who.user_agent,
        detail={"operation": operation, **detail},
        commit=False,
    )


@router.get("/api/ai/providers")
@router.get("/api/tenants/{tenant_id}/ai/providers")
async def list_providers(
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    try:
        if tenant_id is not None:
            bind_tenant(ctx, tenant_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"providers": providers_view()}


@router.get("/api/ai/models")
@router.get("/api/tenants/{tenant_id}/ai/models")
async def list_models(
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    try:
        if tenant_id is not None:
            bind_tenant(ctx, tenant_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"models": providers_view()}


@router.get("/api/ai/policy")
@router.get("/api/tenants/{tenant_id}/ai/policy")
async def get_policy(
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        target = ctx.tenant_id if tenant_id is None else tenant_id
        bind_tenant(ctx, target)
        policy = await load_policy(session, ctx.tenant_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return policy.as_dict()


@router.patch("/api/ai/policy")
@router.patch("/api/tenants/{tenant_id}/ai/policy")
async def patch_policy(
    payload: PolicyPatch,
    request: Request,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        target = ctx.tenant_id if tenant_id is None else tenant_id
        bind_tenant(ctx, target, payload.tenant_id)
        if not ctx.can(Permission.SECURITY_SETTINGS):
            await emit(
                session,
                AuditAction.AUTHZ_DENIED,
                tenant_id=ctx.tenant_id,
                actor_user_id=ctx.user_id,
                actor_email=ctx.user.email,
                detail={"operation": "ai_policy_change", "reason": "permission_denied"},
                commit=False,
            )
            await session.commit()
            require_can(ctx, Permission.SECURITY_SETTINGS)
        policy = await save_policy(
            session,
            ctx.tenant_id,
            allowed_presets=payload.allowed_presets,
            disabled_providers=payload.disabled_providers,
            development_only_presets=payload.development_only_presets,
            token_ceiling=payload.token_ceiling,
            status=payload.status,
            ceiling_supplied=payload.ceiling_supplied,
        )
        await _audit(session, ctx, request, "ai_policy_changed", {"status": policy.status})
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return policy.as_dict()


@router.get("/api/ai/usage")
@router.get("/api/tenants/{tenant_id}/ai/usage")
async def ai_usage(
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        target = ctx.tenant_id if tenant_id is None else tenant_id
        bind_tenant(ctx, target)
        used = await used_quantity(
            session,
            tenant_id=ctx.tenant_id,
            metric=UsageMetric.LLM_TOKEN,
            period=calendar_period(),
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {
        "tenant_id": str(ctx.tenant_id),
        "metric": UsageMetric.LLM_TOKEN.value,
        "used": used,
        "source": "billing.metering",
        "tenant_charge_usd": None,
    }


@router.get("/api/ai/health")
async def ai_health(ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ))):
    return health_view()


@router.get("/api/ai/costs")
async def ai_costs(
    tokens: int = 0,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    try:
        if tokens < 0:
            raise GovernanceError("Token count cannot be negative", code="invalid", status_code=422)
        body = estimate(provider=ctx.tenant.llm_provider or "openai", tokens=tokens)
    except HierarchyError as exc:
        raise to_http(exc) from None
    except ValueError as exc:
        raise to_http(GovernanceError(str(exc), code="invalid", status_code=422)) from None
    return body
