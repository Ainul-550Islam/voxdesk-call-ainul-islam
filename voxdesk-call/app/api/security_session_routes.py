"""Session management and security-event inspection.

The existing ``/api/sessions`` contract lives here so there is one place that
lists, revokes and inspects a human session. ``app.api.session_routes`` re-exports
the session router, which keeps the application import and every current path
working. A second router, ``/api/security``, lists only the caller's own
security events. It does not accept a tenant id from the client.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    TenantContext,
    _client_ip,
    get_identity_context,
    require_human_session,
)
from app.auth.identity import events as identity_events
from app.auth.identity import sessions as identity_sessions
from app.auth.identity.service import IdentityContext
from app.db.models import AuditAction, AuditLog
from app.db.session import get_session

log = structlog.get_logger()
router = APIRouter(prefix="/api/sessions", tags=["identity"])
security_router = APIRouter(prefix="/api/security", tags=["identity"])


class SessionOut(BaseModel):
    id: str
    device: str
    user_agent: str = ""
    ip_address: str = ""
    auth_method: str = "password"
    mfa_verified: bool = False
    current: bool = False
    created_at: datetime
    last_seen_at: datetime
    idle_expires_at: datetime
    expires_at: datetime


class SessionListOut(BaseModel):
    sessions: list[SessionOut]
    current_session_id: str | None = None
    limits: dict


def _out(row, *, current_id: uuid.UUID | None) -> SessionOut:
    return SessionOut(
        id=str(row.id),
        device=row.device_label or identity_sessions.describe_device(row.user_agent),
        user_agent=row.user_agent,
        ip_address=row.ip_address,
        auth_method=row.auth_method,
        mfa_verified=bool(row.mfa_verified),
        current=row.id == current_id,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        idle_expires_at=row.idle_expires_at,
        expires_at=row.expires_at,
    )


@router.get("", response_model=SessionListOut)
async def list_sessions(
    ctx: TenantContext = Depends(require_human_session),
    ictx: IdentityContext = Depends(get_identity_context),
    session: AsyncSession = Depends(get_session),
):
    """Every live session for the caller, newest first, with the current one marked."""
    await identity_sessions.sweep_expired(session, user_id=ctx.user_id)
    rows = await identity_sessions.live_sessions(session, user_id=ctx.user_id)
    return SessionListOut(
        sessions=[_out(row, current_id=ctx.session_id) for row in rows],
        current_session_id=str(ctx.session_id) if ctx.session_id else None,
        limits={
            "idle_minutes": ictx.policy.session_idle_minutes,
            "max_active": ictx.policy.session_max_active,
            "refresh_token_days": ictx.policy.refresh_token_days,
        },
    )


async def _own_session(session: AsyncSession, ctx: TenantContext, session_id: uuid.UUID):
    row = await identity_sessions.get_session(session, ctx.user_id, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Not found"})
    return row


@router.delete("/{session_id}", status_code=204)
async def revoke_session(
    session_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    """Sign out one of the caller's devices. Another user's session id is not found."""
    row = await _own_session(session, ctx, session_id)
    revoked_tokens = await identity_sessions.revoke_session(
        session, row, reason="user_revoked", commit=False
    )
    await identity_events.emit(
        session,
        AuditAction.SESSION_REVOKED,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        target_user_id=ctx.user_id,
        actor_email=ctx.user.email,
        ip_address=_client_ip(request),
        detail={
            "session_id": str(row.id),
            "reason": "user_revoked",
            "device": row.device_label,
            "tokens_revoked": revoked_tokens,
        },
        commit=False,
    )
    await session.commit()


class RevokeOthersOut(BaseModel):
    revoked: int


@router.post("/revoke-others", response_model=RevokeOthersOut)
async def revoke_other_sessions(
    request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    """Sign out every other device, keeping this one."""
    revoked = await identity_sessions.revoke_all_for_user(
        session,
        user_id=ctx.user_id,
        reason="user_revoked_others",
        keep_session_id=ctx.session_id,
        commit=False,
    )
    await identity_events.emit(
        session,
        AuditAction.SESSION_REVOKED,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        target_user_id=ctx.user_id,
        actor_email=ctx.user.email,
        ip_address=_client_ip(request),
        detail={"reason": "user_revoked_others", "revoked": revoked},
        commit=False,
    )
    await session.commit()
    return RevokeOthersOut(revoked=revoked)


@router.delete("", status_code=204)
async def revoke_all_sessions(
    request: Request,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    """Sign out everywhere, including here."""
    from app.auth import service as auth_service

    await auth_service.revoke_all_for_user(session, ctx.user_id, bump_version=True)
    revoked = await identity_sessions.revoke_all_for_user(
        session, user_id=ctx.user_id, reason="user_revoked_all", commit=False
    )
    await identity_events.emit(
        session,
        AuditAction.SESSION_REVOKED,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        target_user_id=ctx.user_id,
        actor_email=ctx.user.email,
        ip_address=_client_ip(request),
        detail={"reason": "user_revoked_all", "revoked": revoked, "token_version_bumped": True},
        commit=False,
    )
    await session.commit()


class RenameIn(BaseModel):
    label: str = Field(min_length=1, max_length=120)


@router.patch("/{session_id}", response_model=SessionOut)
async def rename_session(
    session_id: uuid.UUID,
    payload: RenameIn,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    """Give one of the caller's devices a name. Another user's row is not found."""
    row = await _own_session(session, ctx, session_id)
    row.device_label = payload.label.strip()[:120]
    await session.commit()
    return _out(row, current_id=ctx.session_id)


class SecurityEventOut(BaseModel):
    id: str
    action: str
    actor_user_id: str | None = None
    target_user_id: str | None = None
    created_at: datetime | None = None
    detail: dict = {}


@security_router.get("/events", response_model=list[SecurityEventOut])
async def own_security_events(
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, ge=1, le=100),
):
    """Security events for this caller in this tenant.

    The tenant comes from the session, never from a query parameter. Detail is
    scrubbed again on the way out so a historical row cannot leak a secret.
    """
    names = {AuditAction(value) for value in identity_events.event_names()}
    rows = (
        (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.tenant_id == ctx.tenant_id,
                    AuditLog.action.in_(names),
                    or_(
                        AuditLog.actor_user_id == ctx.user_id,
                        AuditLog.target_user_id == ctx.user_id,
                    ),
                )
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        SecurityEventOut(
            id=str(row.id),
            action=row.action.value,
            actor_user_id=str(row.actor_user_id) if row.actor_user_id else None,
            target_user_id=str(row.target_user_id) if row.target_user_id else None,
            created_at=row.created_at,
            detail=identity_events.scrub(row.detail or {}),
        )
        for row in rows
    ]
