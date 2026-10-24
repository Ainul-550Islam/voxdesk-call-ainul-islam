"""Data residency: regions, tenant pins, and guarded object-key checks.

Two ideas are kept apart on purpose:

* an **intent** is what a tenant asked for. It is created through the existing
  governance API (``/governance/residency``), and it becomes a *pin* only when
  an authoritative verification receipt is attached;
* a **pin** is what the product will actually enforce.

The read endpoints here report which of the two applies and why, so nobody
mistakes a request for a guarantee. The write endpoint records a pin request; it
never marks one verified, because verification is an external fact.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.compliance_v2.common import (
    require_recent_auth_for,
    audit_action,
    http_error,
    scoped_tenant,
)
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.compliance_v2.residency import policy, storage_keys
from app.db.models import AuditLog
from app.db.session import get_session

router = APIRouter(prefix="/residency", tags=["compliance-v2-residency"])

#: Audit events that represent a refused placement attempt.
VIOLATION_EVENTS = (
    "residency_cross_region_blocked",
    "residency_legacy_key_refused",
)


class PinRequest(BaseModel):
    region: str = Field(min_length=2, max_length=64, description="A configured DATA_REGIONS label")


class KeyCheckIn(BaseModel):
    key: str = Field(min_length=3, max_length=500)
    operation: str = Field(default="read", max_length=120)


@router.get("/regions")
async def regions(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_RESIDENCY_READ)),
):
    """The regions this deployment declares, and the default."""
    scoped_tenant(ctx, tenant_id)
    return policy.region_catalogue()


@router.get("/pin")
async def current_pin(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_RESIDENCY_READ)),
    session: AsyncSession = Depends(get_session),
):
    """The effective region for this tenant and where it came from."""
    scoped_tenant(ctx, tenant_id)
    return await policy.pin_document(session, tenant_id)


@router.put("/pin")
@router.post("/pin")
async def request_pin(
    tenant_id: uuid.UUID,
    payload: PinRequest,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_RESIDENCY_WRITE)),
    session: AsyncSession = Depends(get_session),
    _reauth: None = Depends(require_recent_auth_for("residency.pin_change")),
):
    """Request a region for this tenant through the governance intent path."""
    scoped_tenant(ctx, tenant_id)
    from app.governance.context import resolve_scope
    from app.governance.residency import request_intent

    scope = await resolve_scope(session, ctx, None)
    try:
        intent = await request_intent(
            session, scope, region=payload.region, actor_user_id=ctx.user_id
        )
    except Exception as exc:  # noqa: BLE001 - a rejected label is a 422
        raise http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_region", str(exc)[:300]
        ) from exc
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="residency_pin_requested",
        resource_type="residency_intent",
        resource_id=intent.id,
        detail={
            "region": intent.requested_region,
            "status": intent.status,
            "version": intent.version,
        },
    )
    await session.commit()
    return {
        "intent_id": str(intent.id),
        "requested_region": intent.requested_region,
        "status": intent.status,
        "version": intent.version,
        "note": (
            "an intent becomes an enforced pin only after an authoritative "
            "verification receipt is recorded; until then the deployment default applies"
        ),
    }


@router.get("/check")
async def check_region(
    tenant_id: uuid.UUID,
    region: str = Query(min_length=2, max_length=64),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_RESIDENCY_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Would this tenant be allowed to use ``region``? No audit, no side effect."""
    scoped_tenant(ctx, tenant_id)
    decision = await policy.check_region(session, tenant_id=tenant_id, requested_region=region)
    return decision.as_dict()


@router.post("/check-key")
async def check_object_key(
    tenant_id: uuid.UUID,
    payload: KeyCheckIn,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_RESIDENCY_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Classify an object key and say whether it satisfies the pin.

    This is the endpoint an operator uses when a storage error names a key they
    did not expect. It performs no I/O and writes nothing.
    """
    scoped_tenant(ctx, tenant_id)
    classification = storage_keys.classify(payload.key)
    if classification != "regional":
        return {
            "key": payload.key,
            "classification": classification,
            "allowed": False,
            "reason": "the key carries no region segment, so its placement cannot be verified",
        }
    parsed = storage_keys.parse(payload.key)
    document = await policy.pin_document(session, tenant_id)
    return {
        "key": payload.key,
        "classification": classification,
        "region": parsed.region,
        "kind": parsed.kind,
        "pinned_region": document.get("region", ""),
        "allowed": parsed.region == document.get("region"),
        "reason": "" if parsed.region == document.get("region") else "key region differs from the pin",
    }


@router.get("/violations")
async def violations(
    tenant_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_RESIDENCY_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Refused cross-region attempts, read back from the audit trail."""
    scoped_tenant(ctx, tenant_id)
    rows = await session.execute(
        select(AuditLog)
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.event_type.in_(VIOLATION_EVENTS),
        )
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "event_type": row.event_type,
            "result": row.result,
            "actor_user_id": str(row.actor_user_id or ""),
            "created_at": row.created_at,
            "detail": dict(row.detail or {}),
        }
        for row in rows.scalars()
    ]


__all__ = ["router"]
