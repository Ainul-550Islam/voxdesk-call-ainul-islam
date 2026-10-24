"""Time-boxed emergency access to PHI.

Break-glass exists because a clinician-facing system must be usable during an
emergency and accountable afterwards. The controls:

* a **written reason** of at least ten characters is required — the database
  enforces the same minimum, so an API bug cannot create a silent grant;
* **dual approval** by default: the requester cannot approve their own request,
  and a second distinct approver is required before the grant becomes active;
* a hard **expiry** (``BREAK_GLASS_MAX_MINUTES``) computed at request time, so
  approval delays never extend the window;
* the grant is **scoped** to named resource types; an unscoped grant is refused;
* every step — request, approve, activate, revoke, expire — is audited, and
  those audit rows are what the SIEM dispatcher exports. There is no separate
  "security event" channel to fall out of sync.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.compliance_v2.settings import ComplianceV2Settings, get_compliance_v2_settings
from app.db.compliance_v2_models import BreakGlassGrant

STATUS_PENDING = "pending_approval"
STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"
STATUS_REVOKED = "revoked"
STATUS_DENIED = "denied"

#: Resource classes a grant may name. An unlisted scope is refused, so a grant
#: cannot quietly mean "everything".
SCOPES = frozenset({"recording", "transcript", "call_summary", "analysis"})

MIN_REASON_LENGTH = 10


class BreakGlassError(RuntimeError):
    code = "break_glass_error"


class GrantNotFound(BreakGlassError):
    code = "grant_not_found"


class ApprovalRequired(BreakGlassError):
    code = "approval_required"


def _utcnow(moment: dt.datetime | None = None) -> dt.datetime:
    value = moment or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def validate_scope(scope: dict[str, Any] | None) -> dict[str, Any]:
    """Normalise and validate the scope of a request."""
    if not isinstance(scope, dict):
        raise BreakGlassError("a break-glass request needs an explicit scope")
    resource_types = scope.get("resource_types")
    if not isinstance(resource_types, list) or not resource_types:
        raise BreakGlassError("scope.resource_types must name at least one resource type")
    cleaned = {str(item).strip().lower() for item in resource_types}
    unknown = sorted(cleaned - SCOPES)
    if unknown:
        raise BreakGlassError(f"unknown resource type(s): {', '.join(unknown)}")
    output = {"resource_types": sorted(cleaned)}
    for key in ("call_id", "contact_id", "tenant_note"):
        value = scope.get(key)
        if isinstance(value, str) and value.strip():
            output[key] = value.strip()[:200]
    return output


async def request_grant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    requested_by: uuid.UUID,
    reason: str,
    scope: dict[str, Any],
    settings: ComplianceV2Settings | None = None,
    dual_approval: bool | None = None,
) -> BreakGlassGrant:
    """Open a grant request. Flushes."""
    cfg = settings or get_compliance_v2_settings()
    text = (reason or "").strip()
    if len(text) < MIN_REASON_LENGTH:
        raise BreakGlassError(
            f"a break-glass reason of at least {MIN_REASON_LENGTH} characters is required"
        )
    cleaned = validate_scope(scope)
    requires_approval = cfg.break_glass_dual_approval if dual_approval is None else bool(dual_approval)
    now = _utcnow()
    grant = BreakGlassGrant(
        tenant_id=tenant_id,
        requested_by=requested_by,
        reason=text[:2000],
        scope=cleaned,
        status=STATUS_PENDING if requires_approval else STATUS_ACTIVE,
        dual_approval_required=requires_approval,
        activated_at=None if requires_approval else now,
        expires_at=now + dt.timedelta(minutes=cfg.break_glass_max_minutes),
    )
    session.add(grant)
    await session.flush()
    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=requested_by,
        event_type="break_glass_requested",
        resource_type="break_glass_grant",
        resource_id=grant.id,
        detail={
            "scope": cleaned,
            "dual_approval_required": requires_approval,
            "expires_at": grant.expires_at.isoformat(),
        },
    )
    return grant


async def get_grant(
    session: AsyncSession, *, tenant_id: uuid.UUID, grant_id: uuid.UUID
) -> BreakGlassGrant:
    row = await session.scalar(
        select(BreakGlassGrant).where(
            BreakGlassGrant.id == grant_id, BreakGlassGrant.tenant_id == tenant_id
        )
    )
    if row is None:
        raise GrantNotFound("break-glass grant not found")
    return row


async def approve_grant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    grant_id: uuid.UUID,
    approver_id: uuid.UUID,
    settings: ComplianceV2Settings | None = None,
    now: dt.datetime | None = None,
) -> BreakGlassGrant:
    """Approve and activate a pending grant. Flushes."""
    cfg = settings or get_compliance_v2_settings()
    row = await get_grant(session, tenant_id=tenant_id, grant_id=grant_id)
    if row.status != STATUS_PENDING:
        raise BreakGlassError(f"a grant in state {row.status!r} cannot be approved")
    if row.requested_by == approver_id:
        raise ApprovalRequired("the requester cannot approve their own break-glass request")

    moment = _utcnow(now)
    row.approved_by = approver_id
    row.approved_at = moment
    row.status = STATUS_ACTIVE
    row.activated_at = moment
    # The window is measured from the request, not from the approval, so a slow
    # approver cannot extend it.
    row.expires_at = _aware(row.created_at) + dt.timedelta(minutes=cfg.break_glass_max_minutes)
    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=approver_id,
        event_type="break_glass_approved",
        resource_type="break_glass_grant",
        resource_id=row.id,
        detail={"expires_at": row.expires_at.isoformat(), "requested_by": str(row.requested_by)},
    )
    await session.flush()
    return row


async def revoke_grant(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    grant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    reason: str = "",
    now: dt.datetime | None = None,
) -> BreakGlassGrant:
    """End a grant early. Flushes."""
    row = await get_grant(session, tenant_id=tenant_id, grant_id=grant_id)
    if row.status in (STATUS_REVOKED, STATUS_EXPIRED):
        return row
    row.status = STATUS_REVOKED
    row.revoked_at = _utcnow(now)
    row.revoked_by = actor_user_id
    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        event_type="break_glass_revoked",
        resource_type="break_glass_grant",
        resource_id=row.id,
        detail={"reason": (reason or "")[:200]},
    )
    await session.flush()
    return row


def is_active(row: BreakGlassGrant, *, now: dt.datetime | None = None) -> bool:
    """An active grant whose window has not closed."""
    moment = _utcnow(now)
    expires = _aware(row.expires_at)
    return row.status == STATUS_ACTIVE and expires is not None and expires > moment


async def active_grants(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    now: dt.datetime | None = None,
) -> list[BreakGlassGrant]:
    """Currently valid grants for the tenant (optionally for one user)."""
    moment = _utcnow(now)
    query = select(BreakGlassGrant).where(
        BreakGlassGrant.tenant_id == tenant_id,
        BreakGlassGrant.status == STATUS_ACTIVE,
        BreakGlassGrant.expires_at > moment,
    )
    if user_id is not None:
        query = query.where(BreakGlassGrant.requested_by == user_id)
    return list((await session.execute(query.order_by(BreakGlassGrant.expires_at))).scalars())


async def authorize_read(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    resource_type: str,
    now: dt.datetime | None = None,
) -> BreakGlassGrant | None:
    """Return the grant that authorises this PHI read, or ``None``."""
    wanted = (resource_type or "").strip().lower()
    for grant in await active_grants(session, tenant_id=tenant_id, user_id=user_id, now=now):
        if wanted in {str(item).lower() for item in (grant.scope or {}).get("resource_types", [])}:
            return grant
    return None


async def expire_due(
    session: AsyncSession, *, now: dt.datetime | None = None, limit: int = 200
) -> dict:
    """Mark expired grants. Idempotent: only active rows past their window move."""
    moment = _utcnow(now)
    rows = list(
        (
            await session.execute(
                select(BreakGlassGrant)
                .where(
                    BreakGlassGrant.status == STATUS_ACTIVE,
                    BreakGlassGrant.expires_at <= moment,
                )
                .order_by(BreakGlassGrant.expires_at)
                .limit(max(1, limit))
            )
        ).scalars()
    )
    for row in rows:
        row.status = STATUS_EXPIRED
        await record_event(
            session,
            tenant_id=row.tenant_id,
            event_type="break_glass_expired",
            resource_type="break_glass_grant",
            resource_id=row.id,
            detail={"requested_by": str(row.requested_by)},
        )
    await session.flush()
    return {"expired": len(rows)}


def as_dict(row: BreakGlassGrant, *, now: dt.datetime | None = None) -> dict:
    moment = _utcnow(now)
    expires = _aware(row.expires_at)
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "requested_by": str(row.requested_by),
        "reason": row.reason,
        "scope": dict(row.scope or {}),
        "status": row.status,
        "active": is_active(row, now=moment),
        "dual_approval_required": row.dual_approval_required,
        "approved_by": str(row.approved_by) if row.approved_by else "",
        "created_at": _aware(row.created_at).isoformat() if row.created_at else "",
        "expires_at": expires.isoformat() if expires else "",
        "seconds_remaining": max(0, int((expires - moment).total_seconds())) if expires else 0,
        "revoked_at": _aware(row.revoked_at).isoformat() if row.revoked_at else "",
    }


__all__ = [
    "MIN_REASON_LENGTH",
    "SCOPES",
    "STATUS_ACTIVE",
    "STATUS_DENIED",
    "STATUS_EXPIRED",
    "STATUS_PENDING",
    "STATUS_REVOKED",
    "ApprovalRequired",
    "BreakGlassError",
    "GrantNotFound",
    "active_grants",
    "approve_grant",
    "as_dict",
    "authorize_read",
    "expire_due",
    "get_grant",
    "is_active",
    "request_grant",
    "revoke_grant",
    "validate_scope",
]
