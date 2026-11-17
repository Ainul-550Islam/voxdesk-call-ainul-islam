"""Share links to a single agent (PART 14, P14-03).

A share link is a capability URL: whoever holds it can talk to one agent. The
design therefore assumes the URL will be forwarded, screenshotted and pasted
into group chats, and leans on three limits that are all enforced server-side:

* **expiry** -- mandatory, never "never";
* **usage cap** -- `max_uses`, enforced by an atomic conditional update so two
  concurrent redemptions cannot both see `used_count = max_uses - 1`;
* **passcode** -- optional, stored only as a hash.

Tokens and passcodes are hashed with the same helper the public key service uses
(`hash_secret`), so there is one hashing decision in the codebase, not two.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.channel_models import ShareLink
from app.services.public_key_service import hash_secret

MAX_TTL_HOURS = 24 * 30
DEFAULT_TTL_HOURS = 72
MAX_USES_LIMIT = 1000


class ShareError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def new_token() -> str:
    # 32 bytes of urlsafe base32-ish entropy: not guessable, not a bearer of
    # anything else.
    return secrets.token_urlsafe(24)


async def create_link(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: str,
    created_by: str,
    label: str = "",
    ttl_hours: int = DEFAULT_TTL_HOURS,
    max_uses: int = 1,
    max_seconds: int = 300,
    passcode: str | None = None,
) -> tuple[ShareLink, str]:
    """Create a link. Returns ``(row, plaintext_token)`` -- the token once only."""
    if not agent_id or not str(agent_id).strip():
        raise ShareError("invalid_agent", "a share link must name an agent")
    if ttl_hours < 1 or ttl_hours > MAX_TTL_HOURS:
        raise ShareError("invalid_ttl", f"ttl_hours must be between 1 and {MAX_TTL_HOURS}")
    if max_uses < 1 or max_uses > MAX_USES_LIMIT:
        raise ShareError("invalid_max_uses", f"max_uses must be between 1 and {MAX_USES_LIMIT}")
    if max_seconds < 30 or max_seconds > 3600:
        raise ShareError("invalid_max_seconds", "max_seconds must be between 30 and 3600")
    if passcode is not None and len(passcode) < 6:
        raise ShareError("weak_passcode", "a passcode must be at least 6 characters")

    token = new_token()
    row = ShareLink(
        tenant_id=tenant_id,
        agent_id=str(agent_id),
        label=label[:120],
        token_hash=hash_secret(token),
        passcode_hash=hash_secret(passcode) if passcode else None,
        expires_at=_now() + timedelta(hours=ttl_hours),
        max_uses=max_uses,
        max_seconds=max_seconds,
        created_by=created_by[:120],
    )
    session.add(row)
    await session.flush()
    return row, token


async def get_by_token(session: AsyncSession, token: str) -> ShareLink | None:
    if not token:
        return None
    return (
        await session.execute(
            select(ShareLink).where(ShareLink.token_hash == hash_secret(token))
        )
    ).scalar_one_or_none()


def check_usable(row: ShareLink | None, *, moment: datetime | None = None) -> None:
    """Raise the specific reason a link cannot be used. Never a generic 404."""
    if row is None:
        raise ShareError("not_found", "this share link does not exist")
    now = _aware(moment) or _now()
    if row.revoked_at is not None:
        raise ShareError("revoked", "this share link was revoked")
    if _aware(row.expires_at) <= now:
        raise ShareError("expired", f"this share link expired at {row.expires_at.isoformat()}")
    if int(row.used_count) >= int(row.max_uses):
        raise ShareError("exhausted", f"this share link has been used {row.used_count} times")


async def redeem(
    session: AsyncSession,
    *,
    token: str,
    passcode: str | None = None,
    moment: datetime | None = None,
) -> ShareLink:
    """Consume one use of a link, atomically."""
    row = await get_by_token(session, token)
    check_usable(row, moment=moment)
    assert row is not None  # check_usable raised otherwise
    if row.passcode_hash is not None:
        if not passcode:
            raise ShareError("passcode_required", "this share link requires a passcode")
        if hash_secret(passcode) != row.passcode_hash:
            raise ShareError("bad_passcode", "the passcode is wrong")
    now = _aware(moment) or _now()
    result = await session.execute(
        update(ShareLink)
        .where(
            ShareLink.id == row.id,
            ShareLink.used_count < ShareLink.max_uses,
            ShareLink.revoked_at.is_(None),
        )
        .values(used_count=ShareLink.used_count + 1, last_used_at=now)
    )
    if result.rowcount != 1:
        # Somebody else took the last use between our read and our write.
        raise ShareError("exhausted", "this share link has just been used up")
    await session.flush()
    await session.refresh(row)
    return row


async def revoke(
    session: AsyncSession, *, tenant_id: uuid.UUID, link_id: uuid.UUID
) -> ShareLink:
    row = (
        await session.execute(
            select(ShareLink).where(ShareLink.id == link_id, ShareLink.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise ShareError("not_found", "no such share link for this tenant")
    if row.revoked_at is None:
        row.revoked_at = _now()
        await session.flush()
    return row


async def list_links(
    session: AsyncSession, *, tenant_id: uuid.UUID, agent_id: str | None = None
) -> list[ShareLink]:
    statement = select(ShareLink).where(ShareLink.tenant_id == tenant_id)
    if agent_id:
        statement = statement.where(ShareLink.agent_id == agent_id)
    return list(
        (await session.execute(statement.order_by(ShareLink.created_at.desc()))).scalars().all()
    )


def view(row: ShareLink, *, base_url: str = "", moment: datetime | None = None) -> dict[str, Any]:
    """The API view. Never includes the token or the passcode hash."""
    now = _aware(moment) or _now()
    state = "active"
    if row.revoked_at is not None:
        state = "revoked"
    elif _aware(row.expires_at) <= now:
        state = "expired"
    elif int(row.used_count) >= int(row.max_uses):
        state = "exhausted"
    return {
        "id": str(row.id),
        "agent_id": row.agent_id,
        "label": row.label,
        "state": state,
        "expires_at": row.expires_at.isoformat(),
        "max_uses": row.max_uses,
        "used_count": row.used_count,
        "remaining_uses": max(int(row.max_uses) - int(row.used_count), 0),
        "max_seconds": row.max_seconds,
        "requires_passcode": row.passcode_hash is not None,
        "share_url_path": f"/share/{row.id}",
        "created_by": row.created_by,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
    }


# ---------------------------------------------------------------------------
# Session minting for a redeemed link.
#
# Redeeming a link must not be the end of the story: the browser still needs a
# short-lived credential to open a chat or voice session, and that credential
# must not be forgeable, reusable past the link's own cap, or valid for any other
# agent. A signed, expiring envelope (HMAC over the share link id, agent and
# expiry) is enough for that and needs no new table.
# ---------------------------------------------------------------------------

import base64 as _b64  # noqa: E402
import hashlib as _hashlib  # noqa: E402
import hmac as _hmac  # noqa: E402
import json as _json  # noqa: E402

SESSION_TTL_SECONDS = 900


def _b64e(raw: bytes) -> str:
    return _b64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return _b64.urlsafe_b64decode(text + padding)


def _sign(secret: str, payload: dict[str, Any]) -> str:
    body = _json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return _b64e(_hmac.new(secret.encode(), body, _hashlib.sha256).digest())


def mint_session(
    row: ShareLink, *, secret: str, ttl_seconds: int = SESSION_TTL_SECONDS
) -> tuple[str, dict[str, Any]]:
    """``(token, claims)`` for a redeemed link."""
    if ttl_seconds < 30 or ttl_seconds > 3600:
        raise ShareError("invalid_ttl", "a share session must last between 30 and 3600 seconds")
    ttl = min(int(ttl_seconds), int(row.max_seconds))
    claims = {
        "share_id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "agent_id": row.agent_id,
        "exp": int(_now().timestamp()) + ttl,
        "max_seconds": int(row.max_seconds),
    }
    token = f"{_b64e(_json.dumps(claims, sort_keys=True, separators=(',', ':')).encode())}.{_sign(secret, claims)}"
    return token, claims


def verify_session(token: str, *, secret: str, moment: datetime | None = None) -> dict[str, Any]:
    """Validate a share session token, or raise the specific reason."""
    if not token or token.count(".") != 1:
        raise ShareError("bad_session", "the share session token is malformed")
    body, signature = token.split(".", 1)
    try:
        claims = _json.loads(_b64d(body))
    except Exception:
        raise ShareError("bad_session", "the share session token is not readable") from None
    if not isinstance(claims, dict):
        raise ShareError("bad_session", "the share session token is not an object")
    if not _hmac.compare_digest(_sign(secret, claims), signature):
        raise ShareError("bad_signature", "the share session token was not issued by us")
    now = int((_aware(moment) or _now()).timestamp())
    if int(claims.get("exp", 0)) <= now:
        raise ShareError("expired", "the share session has expired")
    return claims
