"""Share links API (PART 14, P14-03).

Two audiences share one router, separated by prefix:

* `/api/share-links`            -- authenticated: create, list, revoke;
* `/api/v1/public/share/{token}` -- unauthenticated: what is this link, and
  redeem it for a short-lived session.

The public half never reveals the agent's configuration, the tenant, or whether
a *different* token exists: an attacker probing tokens must not learn anything
from the error except that theirs is wrong.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.config import get_settings
from app.core.rate_limit import client_ip as client_ip_from_request
from app.db.session import get_session
from app.services import agent_share_service

router = APIRouter(tags=["share-links"])

_PUBLIC_ERROR_STATUS = {
    "not_found": 404,
    "revoked": 410,
    "expired": 410,
    "exhausted": 410,
    "passcode_required": 401,
    "bad_passcode": 401,
    "bad_session": 401,
    "bad_signature": 401,
}


class CreateShareLink(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = Field(min_length=1, max_length=120)
    label: str = Field(default="", max_length=120)
    ttl_hours: int = Field(default=agent_share_service.DEFAULT_TTL_HOURS, ge=1, le=720)
    max_uses: int = Field(default=1, ge=1, le=1000)
    max_seconds: int = Field(default=300, ge=30, le=3600)
    passcode: str | None = Field(default=None, min_length=6, max_length=128)


class RedeemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    passcode: str | None = Field(default=None, max_length=128)


def _public_error(exc: agent_share_service.ShareError) -> HTTPException:
    return HTTPException(
        status_code=_PUBLIC_ERROR_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST),
        detail={"code": exc.code, "message": str(exc)},
    )


@router.post("/api/share-links", status_code=status.HTTP_201_CREATED)
async def create(
    payload: CreateShareLink,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        row, token = await agent_share_service.create_link(
            session,
            tenant_id=ctx.tenant_id,
            agent_id=payload.agent_id,
            created_by=getattr(ctx, "user_email", None) or str(ctx.tenant_id),
            label=payload.label,
            ttl_hours=payload.ttl_hours,
            max_uses=payload.max_uses,
            max_seconds=payload.max_seconds,
            passcode=payload.passcode,
        )
    except agent_share_service.ShareError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await session.commit()
    body = agent_share_service.view(row)
    # The plaintext token is returned exactly once, here. It is not stored.
    body["token"] = token
    return body


@router.get("/api/share-links")
async def list_links(
    agent_id: str | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await agent_share_service.list_links(session, tenant_id=ctx.tenant_id, agent_id=agent_id)
    return {"links": [agent_share_service.view(row) for row in rows]}


@router.delete("/api/share-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke(
    link_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        await agent_share_service.revoke(session, tenant_id=ctx.tenant_id, link_id=link_id)
    except agent_share_service.ShareError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await session.commit()


@router.get("/api/v1/public/share/{token}")
async def describe(
    token: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """What this link is, without consuming a use."""
    row = await agent_share_service.get_by_token(session, token)
    try:
        agent_share_service.check_usable(row)
    except agent_share_service.ShareError as exc:
        raise _public_error(exc) from None
    assert row is not None
    return {
        "agent_id": row.agent_id,
        "label": row.label,
        "requires_passcode": row.passcode_hash is not None,
        "max_seconds": row.max_seconds,
        "remaining_uses": max(int(row.max_uses) - int(row.used_count), 0),
        "expires_at": row.expires_at.isoformat(),
    }


@router.post("/api/v1/public/share/{token}/redeem")
async def redeem(
    token: str,
    payload: RedeemRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Consume one use and return a short-lived session token."""
    try:
        row = await agent_share_service.redeem(
            session, token=token, passcode=payload.passcode
        )
    except agent_share_service.ShareError as exc:
        await session.rollback()
        raise _public_error(exc) from None
    settings = get_settings()
    session_token, claims = agent_share_service.mint_session(
        row, secret=settings.secret_key, ttl_seconds=row.max_seconds
    )
    await session.commit()
    return {
        "session_token": session_token,
        "agent_id": claims["agent_id"],
        "max_seconds": claims["max_seconds"],
        "expires_at_unix": claims["exp"],
        # Recorded so an operator can see where a link was redeemed from without
        # keeping the IP anywhere else.
        "redeemed_from_fingerprint": hashlib.sha256(
            (client_ip_from_request(request) or "").encode()
        ).hexdigest()[:16],
    }


class SessionVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=8, max_length=2000)


@router.post("/api/v1/public/share/session/verify")
async def verify(payload: SessionVerify) -> dict[str, Any]:
    """Validate a session token minted by `/redeem`.

    The voice and chat bootstraps call this instead of trusting the browser's
    copy of the claims: an expired or tampered token has to be refused by the
    server, not by a client that had already given up.
    """
    settings = get_settings()
    try:
        claims = agent_share_service.verify_session(payload.token, secret=settings.secret_key)
    except agent_share_service.ShareError as exc:
        raise HTTPException(
            status_code=_PUBLIC_ERROR_STATUS.get(exc.code, 400),
            detail={"code": exc.code, "message": str(exc)},
        ) from None
    return {"valid": True, "claims": claims}
