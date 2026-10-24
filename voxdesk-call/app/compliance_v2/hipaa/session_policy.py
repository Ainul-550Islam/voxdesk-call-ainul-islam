"""Automatic logoff and re-authentication for PHI-bearing surfaces.

``app.auth.identity.sessions`` already owns session creation, idle extension and
revocation; it enforces liveness on every read. What HIPAA adds is an
*operator-visible floor*: a tenant cannot leave a PHI-capable console signed in
forever, and each automatic logoff is recorded as an audit event rather than the
session simply disappearing.

This module therefore does three things and no more:

``limits_for``
    clamp the configured idle/absolute timeouts to the HIPAA maxima when the
    tenant is in HIPAA mode (a tenant setting may shorten a window, never
    lengthen it beyond the floor);
``sweep``
    revoke sessions that have passed either limit, using the existing
    ``revoke_sessions`` helper so refresh tokens die with the sitting, and audit
    each revocation with the reason;
``require_recent_auth``
    a guard for privileged PHI actions — break-glass approval, PHI export,
    policy change — that demands an authentication inside the configured window.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.auth.identity.models import UserSession
from app.auth.identity.sessions import revoke_sessions
from app.compliance_v2.settings import ComplianceV2Settings, get_compliance_v2_settings

#: Ceilings applied in HIPAA mode, in minutes. Idle 15 minutes is the standard
#: reading of "terminate sessions after a period of inactivity"; 12 hours is the
#: absolute ceiling for a single sitting.
HIPAA_MAX_IDLE_MINUTES = 15
HIPAA_MAX_ABSOLUTE_MINUTES = 12 * 60

REASON_IDLE = "auto_logoff_idle"
REASON_ABSOLUTE = "auto_logoff_absolute"


class ReauthRequired(RuntimeError):
    """Raised when a privileged action needs a fresher authentication."""

    code = "reauth_required"

    def __init__(self, message: str, *, authenticated_at: str = "", window_minutes: int = 0) -> None:
        super().__init__(message)
        self.authenticated_at = authenticated_at
        self.window_minutes = window_minutes


@dataclasses.dataclass(frozen=True)
class SessionLimits:
    idle_minutes: int
    absolute_minutes: int
    reauth_window_minutes: int
    hipaa_mode: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "idle_minutes": self.idle_minutes,
            "absolute_minutes": self.absolute_minutes,
            "reauth_window_minutes": self.reauth_window_minutes,
            "hipaa_mode": self.hipaa_mode,
        }


def limits_for(
    *,
    hipaa_mode: bool,
    idle_minutes: int | None = None,
    absolute_minutes: int | None = None,
    settings: ComplianceV2Settings | None = None,
) -> SessionLimits:
    """Resolve effective session limits, clamped down to the HIPAA floor."""
    cfg = settings or get_compliance_v2_settings()
    idle = cfg.session_idle_timeout_minutes if idle_minutes is None else int(idle_minutes)
    absolute = (
        cfg.session_absolute_timeout_minutes if absolute_minutes is None else int(absolute_minutes)
    )
    idle = max(1, idle)
    absolute = max(idle, absolute)
    if hipaa_mode:
        idle = min(idle, HIPAA_MAX_IDLE_MINUTES)
        absolute = min(absolute, HIPAA_MAX_ABSOLUTE_MINUTES)
    return SessionLimits(
        idle_minutes=idle,
        absolute_minutes=absolute,
        reauth_window_minutes=cfg.sensitive_reauth_window_minutes,
        hipaa_mode=bool(hipaa_mode),
    )


def _utcnow(moment: dt.datetime | None = None) -> dt.datetime:
    value = moment or dt.datetime.now(dt.timezone.utc)
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def _naive(moment: dt.datetime) -> dt.datetime:
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


async def sweep(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    limits: SessionLimits | None = None,
    now: dt.datetime | None = None,
    limit: int = 500,
) -> dict[str, int]:
    """Revoke sessions past their idle or absolute window for one tenant.

    Returns counts per reason. Idempotent: already-revoked sessions are skipped
    because the query filters on ``revoked_at IS NULL``.
    """
    moment = _naive(_utcnow(now))
    cfg = get_compliance_v2_settings()
    effective = limits or limits_for(hipaa_mode=False, settings=cfg)
    rows = list(
        (
            await session.execute(
                select(UserSession)
                .where(
                    UserSession.tenant_id == tenant_id,
                    UserSession.revoked_at.is_(None),
                    (UserSession.expires_at <= moment)
                    | (UserSession.idle_expires_at <= moment),
                )
                .limit(max(1, limit))
            )
        ).scalars()
    )
    absolute_rows = [row for row in rows if _at_or_before(row.expires_at, moment)]
    absolute_ids = {row.id for row in absolute_rows}
    idle_rows = [row for row in rows if row.id not in absolute_ids]

    counts = {"idle": 0, "absolute": 0}
    if idle_rows:
        counts["idle"] = await revoke_sessions(session, idle_rows, reason=REASON_IDLE)
    if absolute_rows:
        counts["absolute"] = await revoke_sessions(session, absolute_rows, reason=REASON_ABSOLUTE)

    for row in rows:
        await record_event(
            session,
            tenant_id=tenant_id,
            actor_user_id=row.user_id,
            event_type="session_auto_logoff",
            resource_type="user_session",
            resource_id=row.id,
            detail={
                "reason": row.revoked_reason,
                "idle_minutes": effective.idle_minutes,
                "absolute_minutes": effective.absolute_minutes,
                "hipaa_mode": effective.hipaa_mode,
            },
        )
    await session.flush()
    return {"checked": len(rows), **counts}


def _at_or_before(value: dt.datetime, moment: dt.datetime) -> bool:
    left = value.replace(tzinfo=None) if value.tzinfo is not None else value
    return left <= moment


async def session_authenticated_at(
    session: AsyncSession, *, user_id: uuid.UUID, session_id: uuid.UUID | None
) -> dt.datetime | None:
    """The most recent proof of presence for a session (password or MFA)."""
    if session_id is None:
        return None
    row = await session.get(UserSession, session_id)
    if row is None or row.user_id != user_id:
        return None
    candidates = [
        value
        for value in (row.mfa_verified_at, row.password_confirmed_at, row.last_seen_at)
        if value is not None
    ]
    if not candidates:
        return None
    newest = max(candidates)
    return newest.replace(tzinfo=dt.timezone.utc) if newest.tzinfo is None else newest


async def require_recent_auth(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID | None,
    action: str,
    window_minutes: int | None = None,
    now: dt.datetime | None = None,
    settings: ComplianceV2Settings | None = None,
) -> dt.datetime:
    """Raise :class:`ReauthRequired` unless the user authenticated recently."""
    cfg = settings or get_compliance_v2_settings()
    window = cfg.sensitive_reauth_window_minutes if window_minutes is None else int(window_minutes)
    moment = _utcnow(now)
    authenticated = await session_authenticated_at(session, user_id=user_id, session_id=session_id)
    if authenticated is None:
        raise ReauthRequired(
            f"{action} requires a recent sign-in; no authentication on record for this session",
            window_minutes=window,
        )
    age = (moment - authenticated).total_seconds()
    if age > window * 60:
        raise ReauthRequired(
            f"{action} requires re-authentication within {window} minutes "
            f"(last authentication was {int(age // 60)} minutes ago)",
            authenticated_at=authenticated.isoformat(),
            window_minutes=window,
        )
    if age < 0:
        # A clock skew large enough to make the authentication "in the future"
        # is treated as stale rather than as fresh.
        raise ReauthRequired(
            f"{action} cannot be authorised: session timestamp is in the future",
            authenticated_at=authenticated.isoformat(),
            window_minutes=window,
        )
    return authenticated


async def audit_reauth_denied(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    action: str,
    error: ReauthRequired,
) -> None:
    """Persist a denied privileged action so the SIEM sees attempts, not just successes."""
    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        event_type="sensitive_action_denied",
        resource_type="session",
        resource_id=user_id,
        detail={
            "action": action,
            "reason": "reauth_required",
            "window_minutes": error.window_minutes,
            "authenticated_at": error.authenticated_at,
        },
    )
    await session.flush()


__all__ = [
    "HIPAA_MAX_ABSOLUTE_MINUTES",
    "HIPAA_MAX_IDLE_MINUTES",
    "REASON_ABSOLUTE",
    "REASON_IDLE",
    "ReauthRequired",
    "SessionLimits",
    "audit_reauth_denied",
    "limits_for",
    "require_recent_auth",
    "session_authenticated_at",
    "sweep",
]
