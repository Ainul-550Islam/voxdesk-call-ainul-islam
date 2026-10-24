"""HIPAA technical safeguards: PHI policy, break-glass, session limits.

The PHI policy decides whether tagged fields are redacted on the way out; the
break-glass endpoints give a time-boxed, dual-approved way to see them anyway;
the session endpoints expose the limits the tenant is held to and can run the
logoff sweep on demand.

Nothing here says the product is HIPAA-compliant. It exposes the controls and
their current state, including the uncomfortable ones: a tenant with HIPAA mode
on and no BAA on file is shown as redacting, with the reason.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.compliance_v2.common import (
    require_recent_auth_for,
    audit_action,
    http_error,
    not_found,
    require_human,
    scoped_tenant,
)
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.compliance_v2.hipaa import (
    break_glass,
    minimum_necessary,
    phi_tagging,
    session_policy,
)

from app.db.compliance_v2_models import BreakGlassGrant
from app.db.session import get_session

router = APIRouter(prefix="/hipaa", tags=["compliance-v2-hipaa"])


class PolicyUpdate(BaseModel):
    hipaa_mode: bool
    baa_on_file: bool = False
    baa_reference: str = Field(default="", max_length=200)
    redact_webhooks: bool = True
    minimum_necessary: dict[str, list[str]] = Field(default_factory=dict)


class GrantRequest(BaseModel):
    reason: str = Field(min_length=break_glass.MIN_REASON_LENGTH, max_length=2000)
    scope: dict[str, Any]
    dual_approval: bool | None = None


class GrantDecision(BaseModel):
    note: str = Field(default="", max_length=200)


@router.get("/phi-policy")
async def get_phi_policy(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_PHI_POLICY_READ)),
    session: AsyncSession = Depends(get_session),
):
    """The tenant's PHI policy plus the tagged-field table it applies to."""
    scoped_tenant(ctx, tenant_id)
    policy = await phi_tagging.load_policy(session, tenant_id)
    return {
        "policy": policy.as_dict(),
        "tagged_fields": phi_tagging.tagged_columns_document(),
        "minimum_necessary": {
            surface: sorted(minimum_necessary.allowlist_for(ctx.role, surface, policy=policy))
            for surface in minimum_necessary.SURFACES
        },
        "external_disclosure_allowed": policy.external_disclosure_allowed,
    }


@router.put("/phi-policy")
async def set_phi_policy(
    tenant_id: uuid.UUID,
    payload: PolicyUpdate,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_PHI_POLICY_MANAGE)),
    session: AsyncSession = Depends(get_session),
):
    """Turn HIPAA mode on or off, and record the BAA reference if there is one."""
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    unknown_surfaces = sorted(
        set(payload.minimum_necessary) - set(minimum_necessary.SURFACES)
    )
    if unknown_surfaces:
        raise http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "unknown_surface",
            f"unknown minimum-necessary surface(s): {', '.join(unknown_surfaces)}",
        )
    try:
        policy = await phi_tagging.set_policy(
            session,
            tenant_id=tenant_id,
            hipaa_mode=payload.hipaa_mode,
            baa_on_file=payload.baa_on_file,
            baa_reference=payload.baa_reference,
            redact_webhooks=payload.redact_webhooks,
            minimum_necessary=payload.minimum_necessary,
            updated_by=ctx.user_id,
        )
    except ValueError as exc:
        raise http_error(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_policy", str(exc)) from None

    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="phi_policy_updated",
        resource_type="phi_policy",
        resource_id=tenant_id,
        detail={
            "hipaa_mode": policy.hipaa_mode,
            "baa_on_file": policy.baa_on_file,
            "redact_webhooks": policy.redact_webhooks,
        },
    )
    await session.commit()
    return policy.as_dict()


@router.get("/break-glass")
async def list_grants(
    tenant_id: uuid.UUID,
    active_only: bool = Query(default=True),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_PHI_POLICY_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Break-glass grants for this tenant, newest first."""
    scoped_tenant(ctx, tenant_id)
    query = select(BreakGlassGrant).where(BreakGlassGrant.tenant_id == tenant_id)
    if active_only:
        query = query.where(BreakGlassGrant.status == break_glass.STATUS_ACTIVE)
    rows = await session.execute(query.order_by(BreakGlassGrant.created_at.desc()).limit(100))
    return [break_glass.as_dict(row) for row in rows.scalars()]


@router.post("/break-glass", status_code=status.HTTP_201_CREATED)
async def request_grant(
    tenant_id: uuid.UUID,
    payload: GrantRequest,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_BREAK_GLASS_REQUEST)),
    session: AsyncSession = Depends(get_session),
):
    """Open a break-glass request. It needs a reason and an explicit scope."""
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    try:
        grant = await break_glass.request_grant(
            session,
            tenant_id=tenant_id,
            requested_by=ctx.user_id,
            reason=payload.reason,
            scope=payload.scope,
            dual_approval=payload.dual_approval,
        )
    except break_glass.BreakGlassError as exc:
        raise http_error(status.HTTP_422_UNPROCESSABLE_ENTITY, "break_glass_invalid", str(exc)) from None
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="break_glass_requested",
        resource_type="break_glass_grant",
        resource_id=grant.id,
        detail={"scope": grant.scope, "dual_approval_required": grant.dual_approval_required},
    )
    await session.commit()
    await session.refresh(grant)
    return break_glass.as_dict(grant)


@router.post("/break-glass/{grant_id}/approve")
async def approve_grant(
    tenant_id: uuid.UUID,
    grant_id: uuid.UUID,
    payload: GrantDecision,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_BREAK_GLASS_APPROVE)),
    session: AsyncSession = Depends(get_session),
    _reauth: None = Depends(require_recent_auth_for("hipaa.break_glass_approve")),
):
    """Approve a pending grant. The requester cannot approve their own."""
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    try:
        grant = await break_glass.approve_grant(
            session, tenant_id=tenant_id, grant_id=grant_id, approver_id=ctx.user_id
        )
    except break_glass.GrantNotFound:
        raise not_found("break_glass_grant") from None
    except break_glass.ApprovalRequired as exc:
        raise http_error(status.HTTP_403_FORBIDDEN, "approval_separation", str(exc)) from None
    except break_glass.BreakGlassError as exc:
        raise http_error(status.HTTP_409_CONFLICT, "break_glass_state", str(exc)) from None
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="break_glass_approved",
        resource_type="break_glass_grant",
        resource_id=grant.id,
        detail={"expires_at": break_glass.as_dict(grant)["expires_at"], "note": payload.note[:200]},
    )
    await session.commit()
    await session.refresh(grant)
    return break_glass.as_dict(grant)


@router.post("/break-glass/{grant_id}/revoke")
async def revoke_grant(
    tenant_id: uuid.UUID,
    grant_id: uuid.UUID,
    payload: GrantDecision,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_BREAK_GLASS_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    try:
        grant = await break_glass.revoke_grant(
            session,
            tenant_id=tenant_id,
            grant_id=grant_id,
            actor_user_id=ctx.user_id,
            reason=payload.note,
        )
    except break_glass.GrantNotFound:
        raise not_found("break_glass_grant") from None
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="break_glass_revoked",
        resource_type="break_glass_grant",
        resource_id=grant.id,
        detail={"note": payload.note[:200]},
    )
    await session.commit()
    await session.refresh(grant)
    return break_glass.as_dict(grant)


@router.post("/break-glass/{grant_id}/authorize")
async def authorize_read(
    tenant_id: uuid.UUID,
    grant_id: uuid.UUID,
    resource_type: str = Query(min_length=3, max_length=32),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_BREAK_GLASS_REQUEST)),
    session: AsyncSession = Depends(get_session),
):
    """Ask whether a live grant covers a PHI read right now."""
    scoped_tenant(ctx, tenant_id)
    grant = await session.scalar(
        select(BreakGlassGrant).where(
            BreakGlassGrant.id == grant_id, BreakGlassGrant.tenant_id == tenant_id
        )
    )
    if grant is None:
        raise not_found("break_glass_grant")
    covering = await break_glass.authorize_read(
        session,
        tenant_id=tenant_id,
        user_id=grant.requested_by,
        resource_type=resource_type,
    )
    return {
        "grant_id": str(grant.id),
        "resource_type": resource_type,
        "authorized": covering is not None and covering.id == grant.id,
        "grant": break_glass.as_dict(grant),
    }


@router.post("/break-glass/expire")
async def expire_grants(
    tenant_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_BREAK_GLASS_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    """Expire grants whose window has closed, outside the scheduler cadence."""
    scoped_tenant(ctx, tenant_id)
    outcome = await break_glass.expire_due(session)
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="break_glass_expiry_sweep",
        resource_type="break_glass_grant",
        detail=dict(outcome),
    )
    await session.commit()
    return outcome


@router.get("/sessions")
async def session_limits(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_PHI_POLICY_READ)),
    session: AsyncSession = Depends(get_session),
):
    """The idle/absolute limits this tenant is held to right now."""
    scoped_tenant(ctx, tenant_id)
    policy = await phi_tagging.load_policy(session, tenant_id)
    limits = session_policy.limits_for(hipaa_mode=policy.hipaa_mode)
    return {
        "hipaa_mode": policy.hipaa_mode,
        "limits": limits.as_dict(),
        "hipaa_maxima": {
            "idle_minutes": session_policy.HIPAA_MAX_IDLE_MINUTES,
            "absolute_minutes": session_policy.HIPAA_MAX_ABSOLUTE_MINUTES,
        },
    }


@router.post("/sessions/sweep")
async def sweep_sessions(
    tenant_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_PHI_POLICY_MANAGE)),
    session: AsyncSession = Depends(get_session),
):
    """Revoke sessions past their idle or absolute window, and audit each one."""
    scoped_tenant(ctx, tenant_id)
    policy = await phi_tagging.load_policy(session, tenant_id)
    limits = session_policy.limits_for(hipaa_mode=policy.hipaa_mode)
    outcome = await session_policy.sweep(session, tenant_id=tenant_id, limits=limits)
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="session_session_policy_sweep",
        resource_type="user_session",
        detail=dict(outcome, limits=limits.as_dict()),
    )
    await session.commit()
    return {"swept": outcome, "limits": limits.as_dict(), "at": dt.datetime.now(dt.timezone.utc)}


__all__ = ["router"]
