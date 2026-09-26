"""Environment access, selection, effective policy and effective quota.

No secret and no deployment credential is returned. A client-supplied
environment id is loaded and then checked; it is never trusted on its own.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_human_session, require_permission
from app.auth.permissions import Permission
from app.db.models import Environment
from app.db.session import get_session
from app.environments import access as environment_access
from app.environments.context_resolution import resolve_environment, select_environment
from app.environments.policy_inheritance import resolve_effective, set_scope_policy
from app.environments.quota import environment_quota
from app.organization.service import Actor
from app.quotas.models import QuotaKey
from app.quotas.service import set_quota
from app.tenancy.access import load_tenant_in_organization
from app.tenancy.isolation import BoundaryDenied, Forbidden, HierarchyError, client_ip, to_http

router = APIRouter(prefix="/api/tenants", tags=["environment-access"])


class EnvironmentAccessOut(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    slug: str
    kind: str
    status: str
    is_default: bool


class SelectBody(BaseModel):
    environment_id: uuid.UUID
    tenant_id: uuid.UUID | None = None
    organization_id: uuid.UUID | None = None


class PolicyOut(BaseModel):
    authorization_valid: bool
    invalid_reason: str
    mfa_required: bool
    sso_required: bool
    password_login_allowed: bool
    api_keys_allowed: bool
    service_accounts_allowed: bool
    session_idle_minutes: int
    session_max_active: int


class PolicyBody(BaseModel):
    mfa_required: bool | None = None
    sso_required: bool | None = None
    password_login_allowed: bool | None = None
    api_keys_allowed: bool | None = None
    service_accounts_allowed: bool | None = None
    session_idle_minutes: int | None = Field(default=None, ge=5, le=10080)
    session_max_active: int | None = Field(default=None, ge=1, le=100)


class QuotaBody(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    mode: str = Field(min_length=1, max_length=16)
    limit: int | None = None


def _actor(ctx: TenantContext, request: Request) -> Actor:
    return Actor(
        user_id=ctx.user_id, email=ctx.user.email, tenant_id=ctx.tenant_id,
        ip_address=client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:300],
    )


def _env_out(row: Environment) -> EnvironmentAccessOut:
    return EnvironmentAccessOut(
        id=row.id, tenant_id=row.tenant_id, name=row.name, slug=row.slug,
        kind=row.kind, status=row.status, is_default=row.is_default,
    )


async def _tenant(session, ctx, tenant_id):
    tenant = await load_tenant_in_organization(session, ctx.user, tenant_id)
    if tenant is None:
        raise BoundaryDenied()
    return tenant


@router.get("/{tenant_id}/access/environments", response_model=list[EnvironmentAccessOut])
async def list_accessible(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import select
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        rows = (
            await session.execute(
                select(Environment).where(Environment.tenant_id == tenant.id)
            )
        ).scalars().all()
        visible = []
        for row in rows:
            _environment, decision = await environment_access.can_read_environment(
                session, ctx.user, tenant.id, row.id, scopes=ctx.scopes,
            )
            if decision.allowed:
                visible.append(_env_out(row))
    except HierarchyError as exc:
        raise to_http(exc) from None
    return visible


@router.get("/{tenant_id}/access/current", response_model=EnvironmentAccessOut | None)
async def current_environment(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        environment = await resolve_environment(session, user=ctx.user, tenant=tenant)
    except HierarchyError as exc:
        raise to_http(exc) from None
    if environment is None:
        return None
    return _env_out(environment)


@router.post("/{tenant_id}/access/current", response_model=EnvironmentAccessOut)
async def select_current(
    tenant_id: uuid.UUID, body: SelectBody, request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.TENANT_READ):
        raise to_http(Forbidden())
    if body.tenant_id is not None and body.tenant_id != tenant_id:
        raise to_http(BoundaryDenied())
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        if body.organization_id is not None and body.organization_id != tenant.organization_id:
            raise BoundaryDenied()
        environment = await select_environment(
            session, user=ctx.user, tenant=tenant, environment_id=body.environment_id,
            actor=_actor(ctx, request),
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _env_out(environment)


@router.get("/{tenant_id}/access/policy", response_model=PolicyOut)
async def inspect_policy(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.IDENTITY_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        environment = None
        if environment_id is not None:
            environment, decision = await environment_access.can_read_environment(
                session, ctx.user, tenant.id, environment_id, scopes=ctx.scopes,
            )
            if environment is None or decision.via == "boundary":
                raise BoundaryDenied()
        policy = await resolve_effective(session, tenant, environment)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return PolicyOut(
        authorization_valid=policy.authorization_valid,
        invalid_reason=policy.invalid_reason,
        mfa_required=policy.mfa_required,
        sso_required=policy.sso_required,
        password_login_allowed=policy.password_login_allowed,
        api_keys_allowed=policy.api_keys_allowed,
        service_accounts_allowed=policy.service_accounts_allowed,
        session_idle_minutes=policy.session_idle_minutes,
        session_max_active=policy.session_max_active,
    )


@router.post("/{tenant_id}/access/policy", response_model=PolicyOut)
async def update_policy(
    tenant_id: uuid.UUID, body: PolicyBody,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    if not ctx.can(Permission.IDENTITY_WRITE):
        raise to_http(Forbidden())
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        values = body.model_dump(exclude_none=True)
        await set_scope_policy(
            session, kind="tenant", scope_id=tenant.id, tenant=tenant, values=values,
        )
        policy = await resolve_effective(session, tenant, None)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return PolicyOut(
        authorization_valid=policy.authorization_valid,
        invalid_reason=policy.invalid_reason,
        mfa_required=policy.mfa_required,
        sso_required=policy.sso_required,
        password_login_allowed=policy.password_login_allowed,
        api_keys_allowed=policy.api_keys_allowed,
        service_accounts_allowed=policy.service_accounts_allowed,
        session_idle_minutes=policy.session_idle_minutes,
        session_max_active=policy.session_max_active,
    )


@router.get("/{tenant_id}/access/quota")
async def inspect_quota(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        environment = None
        if environment_id is not None:
            environment, decision = await environment_access.can_read_environment(
                session, ctx.user, tenant.id, environment_id, scopes=ctx.scopes,
            )
            if environment is None:
                raise BoundaryDenied()
        results = []
        for key in QuotaKey:
            if environment is None:
                from app.tenancy.quota import tenant_quota
                decision_q = await tenant_quota(session, tenant, key)
            else:
                decision_q = await environment_quota(session, tenant, environment, key)
            results.append(decision_q.as_dict())
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"quotas": results}


@router.post("/{tenant_id}/access/quota")
async def set_environment_quota(
    tenant_id: uuid.UUID, body: QuotaBody,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
    environment_id: uuid.UUID | None = None,
):
    if ctx.role.value != "owner" or not ctx.can(Permission.TENANT_UPDATE):
        raise to_http(Forbidden())
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        scope_kind = "tenant"
        scope_id = tenant.id
        if environment_id is not None:
            environment, decision = await environment_access.can_update_environment(
                session, ctx.user, tenant.id, environment_id, scopes=ctx.scopes,
            )
            if environment is None:
                raise BoundaryDenied()
            if not decision.allowed:
                raise Forbidden()
            scope_kind = "environment"
            scope_id = environment.id
        try:
            row = await set_quota(
                session, scope_kind=scope_kind, scope_id=scope_id,
                key=body.key, mode=body.mode, limit_value=body.limit,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "quota_configuration", "message": "Quota configuration is invalid."},
            ) from exc
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"key": row.quota_key, "mode": row.mode, "limit": row.limit_value}


@router.post("/{tenant_id}/access/verify")
async def verify_access(
    tenant_id: uuid.UUID, body: SelectBody,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    if body.tenant_id is not None and body.tenant_id != tenant_id:
        raise to_http(BoundaryDenied())
    try:
        tenant = await _tenant(session, ctx, tenant_id)
        if body.organization_id is not None and body.organization_id != tenant.organization_id:
            raise BoundaryDenied()
        _environment, decision = await environment_access.can_read_environment(
            session, ctx.user, tenant.id, body.environment_id, scopes=ctx.scopes,
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    if decision.via == "boundary":
        raise to_http(BoundaryDenied())
    return {
        "allowed": decision.allowed,
        "via": decision.via,
        "reason": decision.reason,
        "role": decision.role.value if decision.role else None,
    }
