"""Short-lived, channel-bound authentication for CC2 realtime streams."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import ExpiredSignatureError, InvalidTokenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.ws.monitor_ws import _signing_secret
from app.db.models import User, UserRole

REALTIME_TOKEN_SCOPE = "cc2.realtime"
REALTIME_CHANNELS = frozenset({"assist", "supervisor"})
DEFAULT_REALTIME_TICKET_TTL_SECONDS = 300


class RealtimeTicketError(ValueError):
    """A realtime ticket failed authentication, scope, or channel validation."""


@dataclass(frozen=True)
class RealtimePrincipal:
    tenant_id: uuid.UUID
    environment_id: uuid.UUID
    user_id: uuid.UUID
    channel: str
    call_id: uuid.UUID | None
    token_version: int
    supervisor: bool


def issue_realtime_ticket(
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    user_id: uuid.UUID,
    token_version: int,
    channel: str,
    call_id: uuid.UUID | None = None,
    supervisor: bool = False,
    ttl_seconds: int = DEFAULT_REALTIME_TICKET_TTL_SECONDS,
) -> str:
    if channel not in REALTIME_CHANNELS:
        raise RealtimeTicketError("Unknown realtime channel")
    if channel == "assist" and call_id is None:
        raise RealtimeTicketError("Assist tickets require a call binding")
    now = int(time.time())
    claims = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "environment_id": str(environment_id),
        "call_id": str(call_id) if call_id else "",
        "channel": channel,
        "token_version": int(token_version),
        "supervisor": bool(supervisor),
        "scope": REALTIME_TOKEN_SCOPE,
        "iat": now,
        "nbf": now - 1,
        "exp": now + min(DEFAULT_REALTIME_TICKET_TTL_SECONDS, max(1, int(ttl_seconds))),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, _signing_secret(), algorithm="HS256")


def verify_realtime_ticket(
    token: str | None, *, channel: str, call_id: uuid.UUID | None = None
) -> RealtimePrincipal:
    if not token or not token.strip():
        raise RealtimeTicketError("Realtime ticket is required")
    try:
        claims: dict[str, Any] = jwt.decode(
            token.strip(),
            _signing_secret(),
            algorithms=["HS256"],
            options={"require": [
                "sub", "tenant_id", "environment_id", "channel", "scope",
                "token_version", "exp", "iat", "nbf", "jti",
            ]},
        )
        principal = RealtimePrincipal(
            tenant_id=uuid.UUID(str(claims["tenant_id"])),
            environment_id=uuid.UUID(str(claims["environment_id"])),
            user_id=uuid.UUID(str(claims["sub"])),
            channel=str(claims["channel"]),
            call_id=uuid.UUID(str(claims["call_id"])) if claims.get("call_id") else None,
            token_version=int(claims["token_version"]),
            supervisor=claims.get("supervisor") is True,
        )
    except ExpiredSignatureError as exc:
        raise RealtimeTicketError("Realtime ticket has expired") from exc
    except (InvalidTokenError, ValueError, TypeError, KeyError) as exc:
        raise RealtimeTicketError("Realtime ticket is invalid") from exc
    if claims.get("scope") != REALTIME_TOKEN_SCOPE or principal.channel != channel:
        raise RealtimeTicketError("Realtime ticket channel mismatch")
    if channel == "assist" and (
        principal.call_id is None or call_id is None or principal.call_id != call_id
    ):
        raise RealtimeTicketError("Realtime ticket call mismatch")
    return principal


async def active_realtime_user(
    session: AsyncSession, principal: RealtimePrincipal
) -> tuple[User, bool]:
    """Revalidate tenant membership/token version and current supervisor role."""
    user = await session.get(User, principal.user_id)
    if (
        user is None
        or user.tenant_id != principal.tenant_id
        or not user.is_active
        or user.token_version != principal.token_version
    ):
        raise RealtimeTicketError("Realtime principal is no longer active")
    supervisor = principal.supervisor and user.role in {
        UserRole.OWNER, UserRole.ADMIN, UserRole.MANAGER,
    }
    if principal.channel == "supervisor" and not supervisor:
        raise RealtimeTicketError("Supervisor role is no longer active")
    return user, supervisor


__all__ = [
    "DEFAULT_REALTIME_TICKET_TTL_SECONDS",
    "REALTIME_CHANNELS",
    "REALTIME_TOKEN_SCOPE",
    "RealtimePrincipal",
    "RealtimeTicketError",
    "active_realtime_user",
    "issue_realtime_ticket",
    "verify_realtime_ticket",
]
