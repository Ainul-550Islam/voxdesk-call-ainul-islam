"""HTTP surface for the mobile app (Batch 07, block B).

Prefix ``/api/mobile``. Two rules shape every route here:

* **It is self-service by default.** A person registers their own phone, sets their
  own quiet hours, and revokes their own device. The routes therefore take the user
  from the credential, never from the body — there is no ``user_id`` parameter on any
  of them, so one agent cannot enumerate or revoke another agent's devices by
  guessing an id.
* **Tokens are secrets in transit only.** A push token goes in and is never
  returned; responses carry a hint (last six characters) and a failure count. The
  device an operator is looking at in the console is identified by its row id and
  its app/OS version, which is enough to debug and useless to an attacker.

The fan-out itself (quiet hours, dedupe, retries) lives in
:mod:`app.platform_v2.mobile.push`; these routes validate input, apply permissions,
write the audit row and commit.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.platform_v2 import http as api
from app.platform_v2.errors import NotFound
from app.platform_v2.mobile import devices as devices_mod
from app.platform_v2.mobile import push as push_mod

router = APIRouter(prefix="/api/mobile", tags=["mobile"])


# ------------------------------------------------------------------ schemas ---
class QuietHoursIn(BaseModel):
    # Unknown keys are refused rather than ignored: a client that sends
    # `{"quiet_hours": {"start": "22:00"}}` at the wrong nesting level must be
    # told, not silently given a device with no quiet hours.
    model_config = ConfigDict(extra="forbid")
    start: str = Field(description="HH:MM in the device's timezone")
    end: str = Field(description="HH:MM; an end before start means the window crosses midnight")
    timezone: str = Field(default="UTC", max_length=64)
    enabled: bool = True


class RegisterDeviceIn(BaseModel):
    # Unknown keys are refused rather than ignored: a client that sends
    # `{"quiet_hours": {"start": "22:00"}}` at the wrong nesting level must be
    # told, not silently given a device with no quiet hours.
    model_config = ConfigDict(extra="forbid")
    platform: str = Field(description="ios or android")
    device_id: str = Field(min_length=1, max_length=120)
    app_version: str = Field(default="", max_length=40)
    os_version: str = Field(default="", max_length=40)
    push_enabled: bool = True
    quiet_hours: QuietHoursIn | None = None
    token: str | None = Field(default=None, max_length=devices_mod.MAX_TOKEN_LENGTH)


class TokenIn(BaseModel):
    # Unknown keys are refused rather than ignored: a client that sends
    # `{"quiet_hours": {"start": "22:00"}}` at the wrong nesting level must be
    # told, not silently given a device with no quiet hours.
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=8, max_length=devices_mod.MAX_TOKEN_LENGTH)


class QuietHoursUpdateIn(BaseModel):
    # Unknown keys are refused rather than ignored: a client that sends
    # `{"quiet_hours": {"start": "22:00"}}` at the wrong nesting level must be
    # told, not silently given a device with no quiet hours.
    model_config = ConfigDict(extra="forbid")
    quiet_hours: QuietHoursIn | None = None


class TestPushIn(BaseModel):
    # Unknown keys are refused rather than ignored: a client that sends
    # `{"quiet_hours": {"start": "22:00"}}` at the wrong nesting level must be
    # told, not silently given a device with no quiet hours.
    model_config = ConfigDict(extra="forbid")
    template: str = Field(default="push_test", max_length=48)
    title: str = Field(default="VoxDesk test notification", max_length=120)
    body: str = Field(default="If you can read this, push is working on this device.", max_length=200)
    urgent: bool = False


# ------------------------------------------------------------------ devices ---
@router.get("/devices")
@api.endpoint_errors
async def list_devices(
    ctx: TenantContext = Depends(require_permission(Permission.MOBILE_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = await devices_mod.list_for_user(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    described = []
    for row in rows:
        tokens = await devices_mod.active_tokens(session, tenant_id=ctx.tenant_id, device=row)
        described.append(devices_mod.describe(row, tokens))
    return {"devices": described, "count": len(described)}


@router.post("/devices", status_code=status.HTTP_201_CREATED)
@api.endpoint_errors
async def register_device(
    body: RegisterDeviceIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.MOBILE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Register (or refresh) this device and, optionally, its push token."""
    claim = await api.claim(
        session,
        ctx,
        request,
        operation="mobile.device.register",
        payload={"platform": body.platform, "device_id": body.device_id},
    )
    replay = api.replay_response(claim, resource_type="mob_device")
    if replay is not None:
        return replay
    row, created = await devices_mod.register(
        session,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        platform=body.platform,
        device_id=body.device_id,
        app_version=body.app_version,
        os_version=body.os_version,
        push_enabled=body.push_enabled,
        quiet_hours=body.quiet_hours.model_dump() if body.quiet_hours else None,
    )
    if body.token:
        await devices_mod.attach_token(session, tenant_id=ctx.tenant_id, device=row, token=body.token)
    await api.audit(
        session,
        ctx,
        request,
        event_type="mobile.device.registered",
        resource_type="mob_device",
        resource_id=row.id,
        detail={"platform": row.platform, "created": created, "has_token": bool(body.token)},
    )
    await api.complete_claim(session, claim, resource_type="mob_device", resource_id=row.id)
    await session.commit()
    tokens = await devices_mod.active_tokens(session, tenant_id=ctx.tenant_id, device=row)
    payload = devices_mod.describe(row, tokens)
    payload["created"] = created
    return payload


@router.post("/devices/{device_id}/token")
@api.endpoint_errors
async def attach_token(
    device_id: uuid.UUID,
    body: TokenIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.MOBILE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Attach a rotated push token; the previous one is deactivated."""
    device = await devices_mod.get(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id, device_pk=device_id)
    claim = await api.claim(
        session, ctx, request, operation="mobile.device.token", payload={"device_id": str(device_id)}
    )
    replay = api.replay_response(claim, resource_type="mob_push_token")
    if replay is not None:
        return replay
    token = await devices_mod.attach_token(session, tenant_id=ctx.tenant_id, device=device, token=body.token)
    await api.audit(
        session,
        ctx,
        request,
        event_type="mobile.token.attached",
        resource_type="mob_push_token",
        resource_id=token.id,
        detail={"platform": token.platform, "hint": token.token_hint},
    )
    await api.complete_claim(session, claim, resource_type="mob_push_token", resource_id=token.id)
    await session.commit()
    return {"device_id": str(device.id), "token": devices_mod.describe_token(token)}


@router.post("/devices/{device_id}/quiet-hours")
@api.endpoint_errors
async def update_quiet_hours(
    device_id: uuid.UUID,
    body: QuietHoursUpdateIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.MOBILE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    claim = await api.claim(
        session, ctx, request, operation="mobile.device.quiet_hours", payload={"device_id": str(device_id)}
    )
    replay = api.replay_response(claim, resource_type="mob_device")
    if replay is not None:
        return replay
    device = await devices_mod.update_quiet_hours(
        session,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        device_pk=device_id,
        quiet_hours=body.quiet_hours.model_dump() if body.quiet_hours else None,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="mobile.quiet_hours.updated",
        resource_type="mob_device",
        resource_id=device.id,
        detail={"quiet_hours": device.quiet_hours},
    )
    await api.complete_claim(session, claim, resource_type="mob_device", resource_id=device.id)
    await session.commit()
    tokens = await devices_mod.active_tokens(session, tenant_id=ctx.tenant_id, device=device)
    return devices_mod.describe(device, tokens)


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
@api.endpoint_errors
async def revoke_device(
    device_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.MOBILE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Revoke a device: its tokens stop being used immediately."""
    await devices_mod.get(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id, device_pk=device_id)
    claim = await api.claim(
        session, ctx, request, operation="mobile.device.revoke", payload={"device_id": str(device_id)}
    )
    replay = api.replay_response(claim, resource_type="mob_device")
    if replay is not None:
        return replay
    device = await devices_mod.revoke(
        session, tenant_id=ctx.tenant_id, user_id=ctx.user_id, device_pk=device_id
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="mobile.device.revoked",
        resource_type="mob_device",
        resource_id=device.id,
        detail={"platform": device.platform},
    )
    await api.complete_claim(session, claim, resource_type="mob_device", resource_id=device.id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------- deliveries ---
@router.get("/deliveries")
@api.endpoint_errors
async def list_deliveries(
    limit: int | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.MOBILE_READ)),
    session: AsyncSession = Depends(get_session),
):
    size, _ = api.page(limit, 0)
    rows = await push_mod.deliveries_for(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id, limit=size)
    return {
        "deliveries": [push_mod.describe_delivery(row) for row in rows],
        "summary": push_mod.summarise_deliveries(rows),
    }


# ---------------------------------------------------------------- push test ---
@router.post("/push/test")
@api.endpoint_errors
async def send_test_push(
    body: TestPushIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.MOBILE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Send a test notification to the caller's own devices.

    Deliberately usable by the person themselves, and only to themselves: the
    fastest way to debug "push is not working" is from the phone that is not
    receiving anything. ``urgent`` bypasses quiet hours, which is what makes the
    test meaningful at 23:00.
    """
    claim = await api.claim(session, ctx, request, operation="mobile.push.test", payload=body.model_dump())
    replay = api.replay_response(claim, resource_type="mob_push_delivery")
    if replay is not None:
        return replay
    notification = push_mod.Notification(
        template=body.template,
        title=body.title,
        body=body.body,
        data={"test": "1"},
        collapse_key="push-test",
        urgent=body.urgent,
    )
    reports = await push_mod.send_to_user(
        session, tenant_id=ctx.tenant_id, user_id=ctx.user_id, notification=notification
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="mobile.push.test_sent",
        resource_type="mob_push_delivery",
        detail={"attempts": len(reports), "statuses": [report.status for report in reports]},
    )
    await api.complete_claim(session, claim, resource_type="mob_push_delivery", resource_id=ctx.user_id)
    await session.commit()
    return {
        "attempts": [report.as_dict() for report in reports],
        "note": (
            "A dropped attempt names its reason (quiet_hours, push_disabled, no_active_token, "
            "device_revoked, provider_not_configured). Nothing here claims a provider delivered it."
        ),
    }


@router.get("/providers")
@api.endpoint_errors
async def provider_status(
    ctx: TenantContext = Depends(require_permission(Permission.MOBILE_READ)),
):
    """Which push providers this deployment has configured, without secrets."""
    from app.platform_v2.settings import get_plat2_settings

    cfg = get_plat2_settings()
    return {
        "fcm": {"enabled": cfg.fcm_enabled, "configured": bool(cfg.fcm_service_account_json)},
        "apns": {
            "enabled": cfg.apns_enabled,
            "configured": bool(cfg.apns_key_id and cfg.apns_team_id and cfg.apns_private_key_p8 and cfg.apns_bundle_id),
            "sandbox": cfg.apns_use_sandbox,
        },
        "quiet_hours_max_span_hours": cfg.push_quiet_hours_max_span,
        "token_gc_days": cfg.push_token_gc_days,
    }


__all__ = ["router"]


# ------------------------------------------------------- mobile sign-in (PKCE) ---
# The app has no session yet on three of these routes, so they cannot use the
# tenant context. They are still tenant-safe: the authorization code and the refresh
# token are 256-bit random values that we stored hashed, so presenting one *is* the
# proof of tenant, and the row is then used to scope every following query.
class AuthorizeIn(BaseModel):
    device_id: str = Field(min_length=3, max_length=128)
    platform: str = Field(description="ios or android")
    code_challenge: str = Field(min_length=43, max_length=128)
    code_challenge_method: str = "S256"
    redirect_uri: str = Field(default="", max_length=400)


class ExchangeIn(BaseModel):
    code: str = Field(min_length=8, max_length=200)
    code_verifier: str = Field(min_length=43, max_length=128)
    device_id: str = Field(min_length=3, max_length=128)
    redirect_uri: str = Field(default="", max_length=400)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=400)
    device_id: str = Field(default="", max_length=128)


class RevokeIn(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=400)


@router.post("/oauth/authorize", status_code=status.HTTP_201_CREATED)
@api.endpoint_errors
async def authorize_mobile(
    body: AuthorizeIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.MOBILE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Issue a single-use PKCE code for the authenticated user.

    Authenticated on purpose: the platform must know *who* is signing in, and the
    code is then bound to that user and that device. A code is invalidated when the
    same device asks for another one, so a retried authorize leaves nothing live.
    """
    from app.platform_v2.mobile import oauth

    authorization = await oauth.authorize(
        session,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        device_id=body.device_id,
        platform=body.platform,
        code_challenge=body.code_challenge,
        code_challenge_method=body.code_challenge_method,
        redirect_uri=body.redirect_uri,
        commit=False,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="mobile.oauth.authorized",
        resource_type="mob_auth_code",
        resource_id=ctx.user_id,
        detail={"device_id": authorization.device_id, "expires_in": oauth.CODE_TTL_SECONDS},
    )
    await session.commit()
    return authorization.as_dict()


@router.post("/oauth/token")
@api.endpoint_errors
async def exchange_mobile_code(
    body: ExchangeIn,
    session: AsyncSession = Depends(get_session),
):
    """Exchange a code plus its verifier for an access/refresh pair."""
    from app.platform_v2.mobile import oauth

    pair = await oauth.exchange(
        session,
        code=body.code,
        code_verifier=body.code_verifier,
        device_id=body.device_id,
        redirect_uri=body.redirect_uri,
    )
    return pair.as_dict()


@router.post("/oauth/refresh")
@api.endpoint_errors
async def refresh_mobile_token(
    body: RefreshIn,
    session: AsyncSession = Depends(get_session),
):
    """Rotate a refresh token. Reuse of a revoked token kills the whole family."""
    from app.platform_v2.mobile import oauth

    pair = await oauth.refresh(
        session, refresh_token=body.refresh_token, device_id=body.device_id
    )
    return pair.as_dict()


@router.post("/oauth/revoke")
@api.endpoint_errors
async def revoke_mobile_token(
    body: RevokeIn,
    session: AsyncSession = Depends(get_session),
):
    """Sign out: revoke every refresh token in this family."""
    from app.platform_v2.mobile import oauth

    revoked = await oauth.revoke(session, refresh_token=body.refresh_token)
    return {"revoked": revoked}


@router.get("/sessions")
@api.endpoint_errors
async def list_sessions(
    ctx: TenantContext = Depends(require_permission(Permission.MOBILE_READ)),
    session: AsyncSession = Depends(get_session),
):
    """The caller's own live mobile sessions, so a user can see and end them."""
    from app.platform_v2.mobile import oauth

    rows = await oauth.active_sessions(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    return {"sessions": [oauth.describe_session(row) for row in rows], "count": len(rows)}


@router.delete("/sessions/{family_id}")
@api.endpoint_errors
async def revoke_session(
    family_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.MOBILE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """End one of the caller's own sessions by family id."""
    from sqlalchemy import select

    from app.db.platform_v2_models import MobileRefreshToken
    from app.platform_v2.mobile import oauth

    owned = (
        await session.execute(
            select(MobileRefreshToken.id).where(
                MobileRefreshToken.tenant_id == ctx.tenant_id,
                MobileRefreshToken.user_id == ctx.user_id,
                MobileRefreshToken.family_id == family_id,
            )
        )
    ).first()
    if owned is None:
        # Not "forbidden": another user's family id must be indistinguishable from a
        # family id that does not exist.
        raise NotFound("session not found")
    revoked = await oauth._revoke_family(
        session, tenant_id=ctx.tenant_id, family_id=family_id, reason="user_signed_out", commit=False
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="mobile.oauth.session_revoked",
        resource_type="mob_refresh_token",
        resource_id=family_id,
        detail={"revoked": revoked},
    )
    await session.commit()
    return {"family_id": str(family_id), "revoked": revoked}
