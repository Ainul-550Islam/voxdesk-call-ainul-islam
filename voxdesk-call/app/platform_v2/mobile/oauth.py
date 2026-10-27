"""Mobile sign-in: PKCE authorization codes and rotating refresh tokens.

The mobile apps never hold a long-lived password. The flow is:

1. the app signs in against the normal auth endpoints (password, MFA, whatever the
   tenant requires) and receives a short-lived session token;
2. it calls ``POST /api/mobile/oauth/authorize`` with a PKCE ``code_challenge`` and
   its device id — this is authenticated, so the platform knows *who* is signing in;
3. it exchanges the single-use code at ``POST /api/mobile/oauth/token`` together with
   the ``code_verifier``, receiving an access token and a refresh token bound to
   that device;
4. it refreshes by rotation: every refresh returns a new refresh token and revokes
   the old one.

Refresh-token reuse detection is the point of the family design: if a revoked token
is presented again, either the token was stolen or the client is replaying — and
both mean the whole family must die. The response is a plain 409, and the family is
revoked so the attacker's copy stops working too.

``code_challenge_method`` must be ``S256``. ``plain`` is refused: OAuth 2.1 removed
it because with a plain verifier the challenge is the secret, and a leaked
authorization response is then as good as a leaked token.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    refresh_expiry,
)
from app.db.models import User
from app.db.platform_v2_models import MobileAuthCode, MobileRefreshToken
from app.platform_v2.errors import Conflict, NotFound, ValidationFailed
from app.platform_v2.mobile.devices import PLATFORMS, validate_platform

#: Authorization codes are short-lived: they exist only to survive one redirect.
CODE_TTL_SECONDS = 300
#: Whitespace and characters a PKCE verifier may contain (RFC 7636 §4.2).
VERIFIER_LEN = (43, 128)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """Attach UTC to a naive timestamp read back from the database.

    A driver that returns naive datetimes (SQLite in tests, and any deployment with
    ``timezone=False``) would otherwise make every expiry check raise instead of
    expiring the row.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def s256(verifier: str) -> str:
    """``BASE64URL(SHA256(ASCII(verifier)))`` without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def validate_verifier(verifier: str) -> str:
    if not isinstance(verifier, str) or not VERIFIER_LEN[0] <= len(verifier) <= VERIFIER_LEN[1]:
        raise ValidationFailed(
            f"code_verifier must be {VERIFIER_LEN[0]}-{VERIFIER_LEN[1]} characters"
        )
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    if set(verifier) - allowed:
        raise ValidationFailed("code_verifier contains characters PKCE does not allow")
    return verifier


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def validate_redirect(redirect_uri: str) -> str:
    """Only a custom-scheme or https redirect is accepted — never ``http://``."""
    cleaned = (redirect_uri or "").strip()
    if not cleaned:
        return ""
    if cleaned.startswith("https://"):
        return cleaned
    if "://" in cleaned and not cleaned.startswith("http://"):
        # e.g. voxdesk://auth/callback
        return cleaned
    raise ValidationFailed("redirect_uri must be https:// or a custom scheme")


@dataclass(frozen=True)
class Authorization:
    code: str
    expires_at: datetime
    device_id: str
    redirect_uri: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "expires_at": self.expires_at.isoformat(),
            "expires_in": CODE_TTL_SECONDS,
            "device_id": self.device_id,
            "redirect_uri": self.redirect_uri,
            "token_type": "Bearer",
        }


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_at: datetime
    family_id: uuid.UUID
    token_type: str = "Bearer"

    def as_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "refresh_expires_at": self.refresh_expires_at.isoformat(),
            "family_id": str(self.family_id),
        }


async def authorize(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    device_id: str,
    platform: str,
    code_challenge: str,
    code_challenge_method: str = "S256",
    redirect_uri: str = "",
    commit: bool = True,
) -> Authorization:
    """Issue a single-use authorization code for an authenticated user."""
    platform = validate_platform(platform)
    if code_challenge_method != "S256":
        raise ValidationFailed(
            "only S256 PKCE is accepted",
            detail={"code_challenge_method": code_challenge_method},
        )
    challenge = (code_challenge or "").strip()
    if not 43 <= len(challenge) <= 128:
        raise ValidationFailed("code_challenge must be 43-128 characters")
    cleaned_device = (device_id or "").strip()
    if not 3 <= len(cleaned_device) <= 128:
        raise ValidationFailed("device_id must be 3-128 characters")
    clean_redirect = validate_redirect(redirect_uri)

    # An older code for the same device is invalidated: a retried authorize must
    # not leave two live codes behind.
    previous = (
        await session.execute(
            select(MobileAuthCode).where(
                MobileAuthCode.tenant_id == tenant_id,
                MobileAuthCode.user_id == user_id,
                MobileAuthCode.device_id == cleaned_device,
                MobileAuthCode.consumed_at.is_(None),
            )
        )
    ).scalars()
    for row in previous:
        row.consumed_at = _now()

    code = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(seconds=CODE_TTL_SECONDS)
    session.add(
        MobileAuthCode(
            tenant_id=tenant_id,
            user_id=user_id,
            device_id=cleaned_device,
            platform=platform,
            code_hash=hash_code(code),
            code_challenge=challenge,
            code_challenge_method="S256",
            redirect_uri=clean_redirect,
            expires_at=expires_at,
        )
    )
    if commit:
        await session.commit()
    return Authorization(
        code=code, expires_at=expires_at, device_id=cleaned_device, redirect_uri=clean_redirect
    )


async def _user(session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User:
    row = await session.get(User, user_id)
    if row is None or row.tenant_id != tenant_id or not row.is_active:
        raise NotFound("user not found")
    return row


def _access_token(user: User, device_id: str) -> tuple[str, int]:
    token, expires_in = create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=user.role.value if hasattr(user.role, "value") else str(user.role),
        token_version=user.token_version,
        auth_method="mobile_pkce",
    )
    return token, expires_in


async def exchange(
    session: AsyncSession,
    *,
    code: str,
    code_verifier: str,
    device_id: str,
    tenant_id: uuid.UUID | None = None,
    redirect_uri: str = "",
    commit: bool = True,
) -> TokenPair:
    """Exchange a code for tokens, checking PKCE and consuming the code.

    The caller of this endpoint (the app) has no session yet, so ``tenant_id`` is
    optional: when it is omitted the tenant is taken *from the code row itself*.
    That is safe because the code is a 256-bit random value that we hashed on
    issue — knowing it already proves the tenant, and the lookup stays scoped
    afterwards. Passing ``tenant_id`` explicitly keeps the lookup tenant-scoped
    for callers that do have one (tests, and any future hosted flow).
    """
    verifier = validate_verifier(code_verifier or "")
    cleaned_device = (device_id or "").strip()
    conditions = [MobileAuthCode.code_hash == hash_code((code or "").strip())]
    if tenant_id is not None:
        conditions.append(MobileAuthCode.tenant_id == tenant_id)
    row = (
        await session.execute(select(MobileAuthCode).where(*conditions))
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("authorization code not found")
    if row.consumed_at is not None:
        # A replayed code means one of the two parties is not what it claims.
        raise Conflict("authorization code has already been used")
    if _aware(row.expires_at) <= _now():
        raise Conflict("authorization code has expired")
    if row.device_id != cleaned_device:
        raise Conflict("authorization code was issued for another device")
    if redirect_uri and row.redirect_uri and validate_redirect(redirect_uri) != row.redirect_uri:
        raise Conflict("redirect_uri does not match the authorization request")
    if s256(verifier) != row.code_challenge:
        # Same class as a spent code: the grant as presented is not valid, and the
        # SDK treats this as "restart the flow", never "retry with the same code".
        raise Conflict("code_verifier does not match the code_challenge")

    tenant_id = row.tenant_id
    user = await _user(session, tenant_id=tenant_id, user_id=row.user_id)
    row.consumed_at = _now()
    plaintext, digest = generate_refresh_token()
    family_id = uuid.uuid4()
    expires = refresh_expiry()
    session.add(
        MobileRefreshToken(
            tenant_id=tenant_id,
            token_hash=digest,
            family_id=family_id,
            user_id=row.user_id,
            device_id=row.device_id,
            expires_at=expires,
        )
    )
    access, expires_in = _access_token(user, row.device_id)
    if commit:
        await session.commit()
    return TokenPair(
        access_token=access,
        refresh_token=plaintext,
        expires_in=expires_in,
        refresh_expires_at=expires,
        family_id=family_id,
    )


async def refresh(
    session: AsyncSession,
    *,
    refresh_token: str,
    device_id: str = "",
    tenant_id: uuid.UUID | None = None,
    commit: bool = True,
) -> TokenPair:
    """Rotate a refresh token, detecting reuse of a revoked one.

    Like :func:`exchange`, the tenant is resolved from the token row when the caller
    does not have one: a refresh happens precisely when the access token (the thing
    that carries the tenant claim) has expired.
    """
    digest = hash_refresh_token((refresh_token or "").strip())
    conditions = [MobileRefreshToken.token_hash == digest]
    if tenant_id is not None:
        conditions.append(MobileRefreshToken.tenant_id == tenant_id)
    row = (
        await session.execute(select(MobileRefreshToken).where(*conditions))
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("refresh token not found")
    tenant_id = row.tenant_id
    if row.revoked_at is not None:
        # Reuse: revoke the whole family, record the detection and refuse.
        await _revoke_family(
            session,
            tenant_id=tenant_id,
            family_id=row.family_id,
            reason="reuse_detected",
            commit=False,
        )
        row.reuse_detected_at = _now()
        if commit:
            await session.commit()
        raise Conflict(
            "refresh token reuse detected; all sessions for this device family were revoked",
            detail={"family_id": str(row.family_id)},
        )
    if _aware(row.expires_at) <= _now():
        raise Conflict("refresh token has expired")
    if device_id and row.device_id and device_id.strip() != row.device_id:
        raise Conflict("refresh token was issued for another device")

    user = await _user(session, tenant_id=tenant_id, user_id=row.user_id)
    plaintext, new_digest = generate_refresh_token()
    expires = refresh_expiry()
    successor = MobileRefreshToken(
        tenant_id=tenant_id,
        token_hash=new_digest,
        family_id=row.family_id,
        user_id=row.user_id,
        device_id=row.device_id,
        expires_at=expires,
    )
    session.add(successor)
    await session.flush()
    row.revoked_at = _now()
    row.revoked_reason = "rotated"
    row.replaced_by_id = successor.id
    access, expires_in = _access_token(user, row.device_id or "")
    if commit:
        await session.commit()
    return TokenPair(
        access_token=access,
        refresh_token=plaintext,
        expires_in=expires_in,
        refresh_expires_at=expires,
        family_id=row.family_id,
    )


async def _revoke_family(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    family_id: uuid.UUID,
    reason: str,
    commit: bool = True,
) -> int:
    rows = (
        await session.execute(
            select(MobileRefreshToken).where(
                MobileRefreshToken.tenant_id == tenant_id,
                MobileRefreshToken.family_id == family_id,
                MobileRefreshToken.revoked_at.is_(None),
            )
        )
    ).scalars()
    revoked = 0
    for row in rows:
        row.revoked_at = _now()
        row.revoked_reason = reason
        revoked += 1
    if commit:
        await session.commit()
    return revoked


async def revoke(
    session: AsyncSession,
    *,
    refresh_token: str,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    commit: bool = True,
) -> int:
    """Sign a device out: revoke the whole family behind this token."""
    digest = hash_refresh_token((refresh_token or "").strip())
    conditions = [MobileRefreshToken.token_hash == digest]
    if tenant_id is not None:
        conditions.append(MobileRefreshToken.tenant_id == tenant_id)
    row = (
        await session.execute(select(MobileRefreshToken).where(*conditions))
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("refresh token not found")
    if user_id is not None and row.user_id != user_id:
        raise NotFound("refresh token not found")
    tenant_id = row.tenant_id
    return await _revoke_family(
        session, tenant_id=tenant_id, family_id=row.family_id, reason="signed_out", commit=commit
    )


async def revoke_device_sessions(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    device_id: str,
    commit: bool = True,
) -> int:
    rows = (
        await session.execute(
            select(MobileRefreshToken).where(
                MobileRefreshToken.tenant_id == tenant_id,
                MobileRefreshToken.user_id == user_id,
                MobileRefreshToken.device_id == device_id,
                MobileRefreshToken.revoked_at.is_(None),
            )
        )
    ).scalars()
    revoked = 0
    for row in rows:
        row.revoked_at = _now()
        row.revoked_reason = "device_revoked"
        revoked += 1
    if commit:
        await session.commit()
    return revoked


async def active_sessions(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> list[MobileRefreshToken]:
    return list(
        (
            await session.execute(
                select(MobileRefreshToken)
                .where(
                    MobileRefreshToken.tenant_id == tenant_id,
                    MobileRefreshToken.user_id == user_id,
                    MobileRefreshToken.revoked_at.is_(None),
                    MobileRefreshToken.expires_at > _now(),
                )
                .order_by(MobileRefreshToken.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


def describe_session(row: MobileRefreshToken) -> dict[str, Any]:
    return {
        "family_id": str(row.family_id),
        "device_id": row.device_id,
        "expires_at": row.expires_at.isoformat(),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "reuse_detected_at": (
            row.reuse_detected_at.isoformat() if row.reuse_detected_at else None
        ),
    }


async def purge_expired(
    session: AsyncSession, *, tenant_id: uuid.UUID, commit: bool = True
) -> int:
    """Delete codes and refresh tokens past their lifetime (and their audit value)."""
    cutoff = _now() - timedelta(days=30)
    removed = 0
    for model, condition in (
        (MobileAuthCode, MobileAuthCode.expires_at < _now()),
        (MobileRefreshToken, MobileRefreshToken.expires_at < cutoff),
    ):
        for row in (
            await session.execute(
                select(model).where(model.tenant_id == tenant_id, condition)
            )
        ).scalars():
            await session.delete(row)
            removed += 1
    if commit:
        await session.commit()
    return removed


__all__ = [
    "Authorization",
    "CODE_TTL_SECONDS",
    "PLATFORMS",
    "TokenPair",
    "VERIFIER_LEN",
    "active_sessions",
    "authorize",
    "describe_session",
    "exchange",
    "hash_code",
    "purge_expired",
    "refresh",
    "revoke",
    "revoke_device_sessions",
    "s256",
    "validate_redirect",
    "validate_verifier",
]
