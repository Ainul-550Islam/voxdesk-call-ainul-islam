"""Tenant-scoped voice-profile lifecycle API.

A ``VoiceProfile`` is the tenant's own record of a synthesized voice: either a
catalogue voice it pinned, or one it cloned. These routes are the write side
of that record; :mod:`app.api.voice_catalog_routes` is the read-only catalogue
that merges them with the built-in provider voices.

Two properties matter here:

* **Every query is tenant-scoped.** ``list_profiles`` and ``get_profile`` take
  the tenant id from the authenticated context, never from the request body or
  a path parameter, so one tenant cannot enumerate or mutate another's
  profiles.
* **State changes are audited.** Activating, deactivating and deleting a
  profile each write an audit event with the actor's email, staged in the same
  transaction as the mutation so an audit record cannot exist without the
  change it describes (or vice versa).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.audit.service import record_event
from app.db.session import get_session
from app.voice import voice_profile_service

router = APIRouter(prefix="/api/agent/voice-profiles", tags=["voice-profiles"])

MAX_METADATA_KEYS = 50
VALID_VOICE_TYPES = tuple(sorted(voice_profile_service.VALID_TYPES))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class VoiceProfileCreateRequest(StrictModel):
    """A new voice profile for this tenant.

    ``metadata`` and ``capabilities`` are stored as JSON and are validated for
    size by the service layer, which rejects forbidden or oversized fields
    rather than truncating them — a silently truncated voice configuration is
    a call that sounds wrong for a reason nobody can find later.
    """

    name: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=64)
    provider_voice_id: str = Field(min_length=1, max_length=255)
    language: str = Field(default="en-US", min_length=2, max_length=32)
    locale: str = Field(default="", max_length=32)
    voice_type: str = Field(default="provider", max_length=32)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VoiceProfileStateRequest(StrictModel):
    reason: str | None = Field(default=None, max_length=500)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


async def _record_audit(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    event_type: str,
    result: str,
    profile_id: uuid.UUID,
    detail: dict[str, Any],
) -> None:
    actor_type = (
        ctx.auth_method if ctx.auth_method in {"api_key", "service_account", "scim"} else "human"
    )
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        actor_type=actor_type,
        actor_email=ctx.user.email,
        environment_id=ctx.environment_id,
        event_type=event_type,
        resource_type="voice_profile",
        resource_id=profile_id,
        result=result,
        detail=detail,
    )


async def _load_profile(
    session: AsyncSession, ctx: TenantContext, profile_id: uuid.UUID
) -> Any:
    profile = await voice_profile_service.get_profile(
        session, tenant_id=ctx.tenant_id, profile_id=profile_id
    )
    if profile is None:
        raise _error(404, "voice_profile_not_found", "voice profile not found")
    return profile


@router.get("")
async def list_voice_profiles(
    provider: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=32),
    voice_type: str | None = Query(default=None, max_length=32),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """List this tenant's voice profiles, newest-named first."""
    rows = await voice_profile_service.list_profiles(session, ctx.tenant_id)
    items = []
    for row in rows:
        item = voice_profile_service.public_profile(row)
        if provider and item["provider"] != provider.strip().lower():
            continue
        if status and item["status"] != status.strip().lower():
            continue
        if voice_type and item["voice_type"] != voice_type.strip().lower():
            continue
        items.append(item)
    return {"items": items, "total": len(items)}


@router.post("", status_code=201)
async def create_voice_profile(
    payload: VoiceProfileCreateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Create a voice profile for this tenant."""
    if payload.voice_type not in voice_profile_service.VALID_TYPES:
        raise _error(
            422,
            "voice_type_invalid",
            f"voice_type must be one of {', '.join(VALID_VOICE_TYPES)}",
        )
    for label, blob in (("capabilities", payload.capabilities), ("metadata", payload.metadata)):
        if len(blob) > MAX_METADATA_KEYS:
            raise _error(
                422,
                f"{label}_too_large",
                f"{label} must contain at most {MAX_METADATA_KEYS} keys",
            )
    try:
        profile = await voice_profile_service.create_profile(
            session,
            tenant_id=ctx.tenant_id,
            name=payload.name,
            provider=payload.provider,
            provider_voice_id=payload.provider_voice_id,
            language=payload.language,
            locale=payload.locale or payload.language,
            voice_type=payload.voice_type,
            capabilities=dict(payload.capabilities),
            metadata=dict(payload.metadata),
            created_by=ctx.user_id,
        )
        await _record_audit(
            session,
            ctx,
            event_type="voice_profile.created",
            result="success",
            profile_id=profile.id,
            detail={"provider": profile.provider, "voice_type": profile.voice_type},
        )
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        raise _error(422, "voice_profile_invalid", str(exc)) from exc
    except IntegrityError:
        await session.rollback()
        raise _error(
            409,
            "voice_profile_duplicate",
            "A voice profile for this provider and provider voice id already exists.",
        ) from None
    return voice_profile_service.public_profile(profile)


@router.get("/{profile_id}")
async def get_voice_profile(
    profile_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Fetch one voice profile owned by this tenant."""
    profile = await _load_profile(session, ctx, profile_id)
    return voice_profile_service.public_profile(profile)


@router.post("/{profile_id}/activate")
async def activate_voice_profile(
    profile_id: uuid.UUID,
    payload: VoiceProfileStateRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Make a profile usable again after deactivation."""
    profile = await _load_profile(session, ctx, profile_id)
    try:
        await voice_profile_service.activate_profile(session, profile)
    except ValueError as exc:
        raise _error(422, "voice_profile_not_activatable", str(exc)) from exc
    await _record_audit(
        session,
        ctx,
        event_type="voice_profile.activated",
        result="success",
        profile_id=profile.id,
        detail={"reason": (payload.reason if payload else None) or ""},
    )
    await session.commit()
    return voice_profile_service.public_profile(profile)


@router.post("/{profile_id}/deactivate")
async def deactivate_voice_profile(
    profile_id: uuid.UUID,
    payload: VoiceProfileStateRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Stop a profile from being selectable. It is kept, not destroyed."""
    profile = await _load_profile(session, ctx, profile_id)
    await voice_profile_service.deactivate_profile(session, profile)
    await _record_audit(
        session,
        ctx,
        event_type="voice_profile.deactivated",
        result="success",
        profile_id=profile.id,
        detail={"reason": (payload.reason if payload else None) or ""},
    )
    await session.commit()
    return voice_profile_service.public_profile(profile)


@router.delete("/{profile_id}", status_code=200)
async def delete_voice_profile(
    profile_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Delete a voice profile.

    The audit event is staged *before* the delete so the row's own id can be
    referenced, then the profile is removed and both are committed together.
    """
    profile = await _load_profile(session, ctx, profile_id)
    snapshot = voice_profile_service.public_profile(profile)
    await _record_audit(
        session,
        ctx,
        event_type="voice_profile.deleted",
        result="success",
        profile_id=profile.id,
        detail={"provider": snapshot["provider"], "name": snapshot["name"]},
    )
    await session.delete(profile)
    await session.commit()
    return {"deleted": True, "id": snapshot["id"]}
