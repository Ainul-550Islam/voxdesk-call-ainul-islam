"""Routes for querying and refreshing carrier-sourced Number Trust Profiles (Part 7 / Gate G9).

Exposes:
- ``GET /api/phone-numbers/{number_id}/trust-profile``
- ``POST /api/phone-numbers/{number_id}/trust-profile/refresh``
- ``GET /api/v1/telephony/phone-numbers/{number_id}/trust-profile``
- ``POST /api/v1/telephony/phone-numbers/{number_id}/trust-profile/refresh``

Security invariants:
- Strict tenant scoping (cross-tenant access returns HTTP 404).
- Never accepts or writes ``shaken_attestation``, ``branded_caller_name``,
  ``cnam``, ``spam_status``, or ``a2p_registration`` from user input
  (``extra="forbid"`` on refresh request).
- Fails closed with HTTP 501 ``NOT_CONFIGURED`` when provider trust credentials
  are not configured.
- Records an ``AuditLog`` entry in the same database transaction on refresh.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.identity.events import emit
from app.auth.permissions import Permission
from app.db.models import AuditAction
from app.db.session import get_session
from app.telephony.number_trust import (
    NumberTrustError,
    NumberTrustNotConfiguredError,
    get_number_trust_profile,
    refresh_number_trust_profile,
)

router = APIRouter(tags=["number-trust"])


class NumberTrustRefreshRequest(BaseModel):
    """Request body for triggering a provider-API refresh of a number's trust profile.

    Explicitly forbids any extra fields so user input can never supply
    ``shaken_attestation``, ``branded_caller_name``, ``cnam``, ``spam_status``,
    or ``a2p_registration``.
    """

    model_config = ConfigDict(extra="forbid")

    force: bool = Field(
        default=True,
        description="Trigger an immediate lookup against the carrier Trust Hub / Number Lookup API.",
    )


class NumberTrustProfileResponse(BaseModel):
    id: str | None = None
    tenant_id: str
    phone_number_id: str
    e164: str
    provider: str
    shaken_attestation: str | None = None
    branded_caller_name: str | None = None
    cnam: str | None = None
    spam_status: str
    a2p_registration: str
    provider_bundle_sid: str | None = None
    last_checked: str | None = None
    source: str


async def _handle_get_trust_profile(
    number_id: uuid.UUID,
    ctx: TenantContext,
    session: AsyncSession,
) -> dict[str, Any]:
    try:
        return await get_number_trust_profile(
            session, tenant_id=ctx.tenant_id, number_id=number_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Phone number not found") from exc


async def _handle_refresh_trust_profile(
    number_id: uuid.UUID,
    payload: NumberTrustRefreshRequest | None,
    ctx: TenantContext,
    session: AsyncSession,
) -> dict[str, Any]:
    _ = payload or NumberTrustRefreshRequest()
    try:
        profile = await refresh_number_trust_profile(
            session,
            tenant_id=ctx.tenant_id,
            number_id=number_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Phone number not found") from exc
    except NumberTrustNotConfiguredError as exc:
        raise HTTPException(
            status_code=501,
            detail={
                "code": exc.code,
                "provider": exc.provider,
                "message": str(exc),
            },
        ) from exc
    except NumberTrustError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "code": exc.code,
                "message": str(exc),
            },
        ) from exc

    await emit(
        session,
        AuditAction.GOVERNANCE_EVENT,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        detail={
            "event": "number_trust.refreshed",
            "target_type": "number_trust_profile",
            "target_id": str(profile.id),
            "phone_number_id": str(number_id),
            "provider": profile.provider,
            "shaken_attestation": profile.shaken_attestation,
            "spam_status": profile.spam_status,
            "a2p_registration": profile.a2p_registration,
        },
        commit=False,
    )
    await session.commit()
    return profile.as_dict()


@router.get(
    "/api/phone-numbers/{number_id}/trust-profile",
    response_model=NumberTrustProfileResponse,
)
async def get_trust_profile_route(
    number_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await _handle_get_trust_profile(number_id, ctx, session)


@router.post(
    "/api/phone-numbers/{number_id}/trust-profile/refresh",
    response_model=NumberTrustProfileResponse,
)
async def refresh_trust_profile_route(
    number_id: uuid.UUID,
    payload: NumberTrustRefreshRequest | None = Body(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await _handle_refresh_trust_profile(number_id, payload, ctx, session)


@router.get(
    "/api/v1/telephony/phone-numbers/{number_id}/trust-profile",
    response_model=NumberTrustProfileResponse,
)
async def get_trust_profile_v1_route(
    number_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await _handle_get_trust_profile(number_id, ctx, session)


@router.post(
    "/api/v1/telephony/phone-numbers/{number_id}/trust-profile/refresh",
    response_model=NumberTrustProfileResponse,
)
async def refresh_trust_profile_v1_route(
    number_id: uuid.UUID,
    payload: NumberTrustRefreshRequest | None = Body(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await _handle_refresh_trust_profile(number_id, payload, ctx, session)
