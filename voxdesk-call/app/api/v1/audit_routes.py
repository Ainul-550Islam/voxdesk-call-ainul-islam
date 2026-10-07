"""Tenant- and environment-aware audit query API.

This reads the established ``audit_logs`` table; it is not a second audit
store. Every query is tenant-constrained before optional filters are applied,
and environment rows are visible only when the caller's effective environment
role grants the existing ``audit:read`` permission.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.events import normalize_event_type
from app.audit.redaction import configured_secret_values, redact_text, redact_tree
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.models import AuditLog, Environment
from app.db.session import get_session
from app.environments.membership import resolve as resolve_environment_membership

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


class AuditEventOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    action: str
    event_type: str
    actor_id: str | None = None
    actor_type: str = "system"
    actor_email: str = ""
    target_user_id: str | None = None
    environment_id: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    request_id: str | None = None
    result: str = "success"
    ip_address: str = ""
    user_agent: str = ""
    detail: dict = Field(default_factory=dict)
    created_at: datetime


class AuditPageOut(BaseModel):
    items: list[AuditEventOut]
    total: int
    limit: int
    offset: int
    has_more: bool
    event_types: list[str]


def _iso_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _out(row: AuditLog) -> AuditEventOut:
    safe_detail = redact_tree(row.detail or {}, known_secrets=configured_secret_values())
    if not isinstance(safe_detail, dict):
        safe_detail = {"value": safe_detail}
    return AuditEventOut(
        id=str(row.id),
        action=row.action.value,
        event_type=row.event_type,
        actor_id=str(row.actor_user_id) if row.actor_user_id else None,
        actor_type=row.actor_type,
        actor_email=redact_text(row.actor_email or "", known_secrets=configured_secret_values(), redact_pii=False),
        target_user_id=str(row.target_user_id) if row.target_user_id else None,
        environment_id=str(row.environment_id) if row.environment_id else None,
        resource_type=row.resource_type,
        resource_id=(
            redact_text(
                row.resource_id or "",
                known_secrets=configured_secret_values(),
                redact_pii=True,
            )
            or None
        ),
        request_id=row.request_id,
        result=row.result,
        ip_address=row.ip_address,
        user_agent=redact_text(row.user_agent or "", known_secrets=configured_secret_values()),
        detail=safe_detail,
        created_at=_iso_utc(row.created_at),
    )


async def _visible_environment_ids(
    session: AsyncSession,
    ctx: TenantContext,
    explicit_environment_id: uuid.UUID | None,
) -> set[uuid.UUID]:
    """Resolve environment visibility with the existing membership/RBAC policy."""
    rows = (
        await session.execute(
            select(Environment).where(Environment.tenant_id == ctx.tenant_id)
        )
    ).scalars().all()
    if explicit_environment_id is not None:
        rows = [row for row in rows if row.id == explicit_environment_id]
        if not rows:
            raise HTTPException(status_code=404, detail="Not found")
    elif ctx.environment_id is not None:
        rows = [row for row in rows if row.id == ctx.environment_id]

    visible: set[uuid.UUID] = set()
    for environment in rows:
        access = await resolve_environment_membership(
            session, ctx.user, environment, ctx.tenant
        )
        if (
            access.allowed
            and access.role is not None
            and has_permission(access.role, Permission.AUDIT_READ)
            and (ctx.scopes is None or Permission.AUDIT_READ.value in ctx.scopes)
        ):
            visible.add(environment.id)

    if explicit_environment_id is not None and explicit_environment_id not in visible:
        raise HTTPException(status_code=404, detail="Not found")
    if ctx.environment_id is not None:
        if explicit_environment_id is not None and explicit_environment_id != ctx.environment_id:
            raise HTTPException(status_code=404, detail="Not found")
        if ctx.environment_id not in visible:
            raise HTTPException(status_code=404, detail="Not found")
    return visible


@router.get("/events", response_model=AuditPageOut)
async def query_audit_events(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    event_type: str | None = Query(default=None, min_length=1, max_length=128),
    actor_id: uuid.UUID | None = Query(default=None),
    target_user_id: uuid.UUID | None = Query(default=None),
    environment_id: uuid.UUID | None = Query(default=None),
    resource_type: str | None = Query(default=None, min_length=1, max_length=64),
    resource_id: str | None = Query(default=None, min_length=1, max_length=160),
    result: str | None = Query(default=None, min_length=1, max_length=24),
    ctx: TenantContext = Depends(require_permission(Permission.AUDIT_READ)),
    session: AsyncSession = Depends(get_session),
) -> AuditPageOut:
    """Search immutable, server-side audit records within caller visibility."""
    start_utc = _iso_utc(start)
    end_utc = _iso_utc(end)
    if start_utc is not None and end_utc is not None and start_utc >= end_utc:
        raise HTTPException(status_code=422, detail="start must be earlier than end")
    if start_utc is not None and end_utc is not None and end_utc - start_utc > timedelta(days=366):
        raise HTTPException(status_code=422, detail="The requested time range may not exceed 366 days")

    normalized_type = None
    if event_type is not None:
        try:
            normalized_type = normalize_event_type(event_type)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid event_type") from None
    normalized_result = result.lower() if result else None
    if normalized_result is not None and normalized_result not in {
        "success", "failure", "denied", "pending", "error"
    }:
        raise HTTPException(status_code=422, detail="Invalid result")
    normalized_resource_type = resource_type.strip().lower() if resource_type else None
    normalized_resource_id = resource_id.strip() if resource_id else None

    visible_environments = await _visible_environment_ids(
        session, ctx, environment_id
    )
    conditions = [AuditLog.tenant_id == ctx.tenant_id]
    if environment_id is not None:
        conditions.append(AuditLog.environment_id == environment_id)
    elif ctx.environment_id is not None:
        conditions.append(AuditLog.environment_id == ctx.environment_id)
    else:
        # Legacy tenant events without an environment remain visible to users
        # with tenant audit permission. New environment-tagged rows are limited
        # to memberships that grant audit:read at that environment.
        conditions.append(
            or_(AuditLog.environment_id.is_(None), AuditLog.environment_id.in_(visible_environments))
        )
    if start_utc is not None:
        conditions.append(AuditLog.created_at >= start_utc)
    if end_utc is not None:
        conditions.append(AuditLog.created_at < end_utc)
    if normalized_type is not None:
        conditions.append(AuditLog.event_type == normalized_type)
    if actor_id is not None:
        conditions.append(AuditLog.actor_user_id == actor_id)
    if target_user_id is not None:
        conditions.append(AuditLog.target_user_id == target_user_id)
    if normalized_resource_type is not None:
        conditions.append(AuditLog.resource_type == normalized_resource_type)
    if normalized_resource_id is not None:
        conditions.append(AuditLog.resource_id == normalized_resource_id)
    if normalized_result is not None:
        conditions.append(AuditLog.result == normalized_result)

    predicate = and_(*conditions)
    total = int((await session.scalar(select(func.count(AuditLog.id)).where(predicate))) or 0)
    rows = (
        await session.execute(
            select(AuditLog)
            .where(predicate)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    type_scope = [AuditLog.tenant_id == ctx.tenant_id]
    if environment_id is not None:
        type_scope.append(AuditLog.environment_id == environment_id)
    elif ctx.environment_id is not None:
        type_scope.append(AuditLog.environment_id == ctx.environment_id)
    else:
        type_scope.append(
            or_(AuditLog.environment_id.is_(None), AuditLog.environment_id.in_(visible_environments))
        )
    event_types = list(
        (
            await session.execute(
                select(AuditLog.event_type)
                .where(and_(*type_scope))
                .distinct()
                .order_by(AuditLog.event_type)
                .limit(256)
            )
        ).scalars().all()
    )
    return AuditPageOut(
        items=[_out(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
        has_more=offset + len(rows) < total,
        event_types=event_types,
    )
