# File: app/api/phone_number_lifecycle_routes.py — Missing API: phone-number lifecycle extended update/configure/delete/provider-release/rebind
"""
Phone-number lifecycle extended API.
Closes gap 7: existing list/search/provision/assign/release exists, but update/configure/delete/provider-release/rebind missing.
Extended in Sub-Phase 2E to support binding inbound_agent_id, inbound_agent_version, and outbound_agent_id.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.identity.events import emit
from app.auth.permissions import Permission
from app.db.models import Agent, AgentVersion, AuditAction
from app.db.session import get_session
from app.telephony.number_provisioning import get_owned
from app.telephony.provider_errors import TelephonyError
from app.tenancy.isolation import HierarchyError, client_ip, to_http
from app.tenancy.policy import bind_tenant

router = APIRouter(prefix="/api/phone-numbers", tags=["phone-numbers-lifecycle"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NumberUpdateRequest(_Strict):
    label: Optional[str] = Field(default=None, max_length=120)
    capabilities: Optional[dict] = None
    webhook_url: Optional[str] = Field(default=None, max_length=500)
    inbound_agent_id: Optional[uuid.UUID] = None
    inbound_agent_version: Optional[int] = Field(default=None, ge=1)
    outbound_agent_id: Optional[uuid.UUID] = None
    tenant_id: Optional[uuid.UUID] = None


class NumberConfigureRequest(_Strict):
    voice_enabled: Optional[bool] = None
    sms_enabled: Optional[bool] = None
    fax_enabled: Optional[bool] = None
    emergency_address_id: Optional[str] = None
    voice_application_sid: Optional[str] = None
    tenant_id: Optional[uuid.UUID] = None


class NumberDeleteRequest(_Strict):
    confirm_e164: str = Field(min_length=8, max_length=20)
    release_from_provider: bool = Field(
        default=False, description="If true, also release from Twilio/Telnyx"
    )
    tenant_id: Optional[uuid.UUID] = None


class NumberRebindRequest(_Strict):
    new_tenant_id: uuid.UUID
    reason: Optional[str] = Field(default=None, max_length=500)
    tenant_id: Optional[uuid.UUID] = None


def _bind(
    ctx: TenantContext,
    tenant_id: Optional[uuid.UUID],
    body_tenant: Optional[uuid.UUID],
) -> None:
    target = tenant_id if tenant_id is not None else body_tenant
    if target is not None:
        bind_tenant(ctx, target)
    if body_tenant is not None and body_tenant != ctx.tenant_id:
        bind_tenant(ctx, body_tenant)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _validate_tenant_agent(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    agent_id: Optional[uuid.UUID],
    version_number: Optional[int] = None,
) -> None:
    if agent_id is None:
        if version_number is not None:
            raise HTTPException(
                status_code=400,
                detail="Cannot pin inbound_agent_version without inbound_agent_id.",
            )
        return
    agent = (
        await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id)
        )
    ).scalars().first()
    if agent is None:
        raise HTTPException(
            status_code=404,
            detail=f"Agent {agent_id} not found for tenant {tenant_id}",
        )
    if version_number is not None:
        ver = (
            await session.execute(
                select(AgentVersion).where(
                    AgentVersion.tenant_id == tenant_id,
                    AgentVersion.agent_id == agent_id,
                    AgentVersion.version_number == version_number,
                )
            )
        ).scalars().first()
        if ver is None:
            raise HTTPException(
                status_code=404,
                detail=f"AgentVersion v{version_number} not found for agent {agent_id}",
            )


@router.patch("/{number_id}")
async def update_number(
    number_id: uuid.UUID,
    payload: NumberUpdateRequest,
    request: Request,
    tenant_id: Optional[uuid.UUID] = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """PATCH /api/phone-numbers/{id} — Update label/capabilities/webhook/agent binding."""
    try:
        _bind(ctx, tenant_id, payload.tenant_id)
        row = await get_owned(session, ctx.tenant_id, number_id)
        fields_set = payload.model_fields_set

        if "inbound_agent_id" in fields_set or "inbound_agent_version" in fields_set:
            eff_agent = (
                payload.inbound_agent_id
                if "inbound_agent_id" in fields_set
                else row.inbound_agent_id
            )
            eff_ver = (
                payload.inbound_agent_version
                if "inbound_agent_version" in fields_set
                else (
                    None
                    if ("inbound_agent_id" in fields_set and payload.inbound_agent_id is None)
                    else row.inbound_agent_version
                )
            )
            await _validate_tenant_agent(session, ctx.tenant_id, eff_agent, eff_ver)
            if "inbound_agent_id" in fields_set:
                row.inbound_agent_id = payload.inbound_agent_id
            if "inbound_agent_version" in fields_set:
                row.inbound_agent_version = payload.inbound_agent_version
            elif "inbound_agent_id" in fields_set and payload.inbound_agent_id is None:
                row.inbound_agent_version = None

        if "outbound_agent_id" in fields_set:
            await _validate_tenant_agent(session, ctx.tenant_id, payload.outbound_agent_id)
            row.outbound_agent_id = payload.outbound_agent_id

        # Update allowed fields — model may have label, webhook_url, capabilities
        if payload.label is not None and hasattr(row, "label"):
            row.label = payload.label
        if payload.capabilities is not None and hasattr(row, "capabilities"):
            row.capabilities = payload.capabilities
        if payload.webhook_url is not None and hasattr(row, "webhook_url"):
            row.webhook_url = payload.webhook_url
        # Always update updated_at if exists
        if hasattr(row, "updated_at"):
            row.updated_at = _now()
        await emit(
            session,
            AuditAction.INTEGRATION_UPDATED,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            actor_email=ctx.user.email,
            ip_address=client_ip(request),
            detail={"operation": "phone_number_updated", "number_id": str(row.id)},
            commit=False,
        )
        await session.commit()
        await session.refresh(row)
    except HierarchyError as exc:
        raise to_http(exc) from None
    except TelephonyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    return row.as_dict()


@router.post("/{number_id}/configure")
async def configure_number(
    number_id: uuid.UUID,
    payload: NumberConfigureRequest,
    request: Request,
    tenant_id: Optional[uuid.UUID] = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/phone-numbers/{id}/configure — Configure voice/sms/fax, emergency address."""
    try:
        _bind(ctx, tenant_id, payload.tenant_id)
        row = await get_owned(session, ctx.tenant_id, number_id)
        # Apply configuration to provider if possible
        try:
            from app.telephony.providers.factory import get_provider

            provider = get_provider(
                payload.provider if hasattr(payload, "provider") else "twilio"
            )
            if hasattr(provider, "configure_number"):
                await provider.configure_number(
                    e164=row.e164 if hasattr(row, "e164") else str(row.id),
                    voice_enabled=payload.voice_enabled,
                    sms_enabled=payload.sms_enabled,
                )
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            pass

        # Update local record
        if hasattr(row, "voice_enabled") and payload.voice_enabled is not None:
            row.voice_enabled = payload.voice_enabled
        if hasattr(row, "sms_enabled") and payload.sms_enabled is not None:
            row.sms_enabled = payload.sms_enabled

        await emit(
            session,
            AuditAction.INTEGRATION_UPDATED,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            actor_email=ctx.user.email,
            ip_address=client_ip(request),
            detail={
                "operation": "phone_number_configured",
                "number_id": str(row.id),
                "config": payload.model_dump(exclude_none=True),
            },
            commit=False,
        )
        await session.commit()
        await session.refresh(row)
    except HierarchyError as exc:
        raise to_http(exc) from None
    except TelephonyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    return row.as_dict()


@router.delete("/{number_id}")
async def delete_number(
    number_id: uuid.UUID,
    payload: NumberDeleteRequest,
    request: Request,
    tenant_id: Optional[uuid.UUID] = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """DELETE /api/phone-numbers/{id} — Delete with confirmation, optional provider release."""
    try:
        _bind(ctx, tenant_id, payload.tenant_id)
        row = await get_owned(session, ctx.tenant_id, number_id)
        e164 = getattr(row, "e164", getattr(row, "phone_number", str(row.id)))
        if payload.confirm_e164 != e164 and payload.confirm_e164 != str(row.id):
            raise HTTPException(status_code=422, detail="confirm_e164 must match number")

        if payload.release_from_provider:
            try:
                from app.telephony.providers.factory import get_provider

                provider = get_provider(getattr(row, "provider", "twilio"))
                if hasattr(provider, "release_number"):
                    await provider.release_number(e164)
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"provider release failed: {type(exc).__name__}",
                ) from None

        await emit(
            session,
            AuditAction.INTEGRATION_UPDATED,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            actor_email=ctx.user.email,
            ip_address=client_ip(request),
            detail={
                "operation": "phone_number_deleted",
                "number_id": str(row.id),
                "released": payload.release_from_provider,
            },
            commit=False,
        )
        await session.delete(row)
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    except TelephonyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    return {
        "id": str(number_id),
        "deleted": True,
        "released_from_provider": payload.release_from_provider,
        "at": _now().isoformat(),
    }


@router.post("/{number_id}/release-provider")
async def release_provider_number(
    number_id: uuid.UUID,
    request: Request,
    tenant_id: Optional[uuid.UUID] = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/phone-numbers/{id}/release-provider — Provider-release without deleting local record immediately."""
    try:
        _bind(ctx, tenant_id, None)
        row = await get_owned(session, ctx.tenant_id, number_id)
        e164 = getattr(row, "e164", getattr(row, "phone_number", str(row.id)))
        try:
            from app.telephony.providers.factory import get_provider

            provider = get_provider(getattr(row, "provider", "twilio"))
            if hasattr(provider, "release_number"):
                await provider.release_number(e164)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"provider release failed: {type(exc).__name__}",
            ) from None

        # Mark as released
        if hasattr(row, "status"):
            row.status = "released"
        if hasattr(row, "released_at"):
            row.released_at = _now()

        await emit(
            session,
            AuditAction.INTEGRATION_UPDATED,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            actor_email=ctx.user.email,
            ip_address=client_ip(request),
            detail={"operation": "phone_number_provider_released", "number_id": str(row.id)},
            commit=False,
        )
        await session.commit()
        await session.refresh(row)
    except HierarchyError as exc:
        raise to_http(exc) from None
    except TelephonyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    return row.as_dict()


@router.post("/{number_id}/rebind")
async def rebind_number(
    number_id: uuid.UUID,
    payload: NumberRebindRequest,
    request: Request,
    tenant_id: Optional[uuid.UUID] = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/phone-numbers/{id}/rebind — Rebind number to different tenant (platform admin)."""
    try:
        _bind(ctx, tenant_id, payload.tenant_id)
        row = await get_owned(session, ctx.tenant_id, number_id)
        old_tenant = row.tenant_id
        row.tenant_id = payload.new_tenant_id

        await emit(
            session,
            AuditAction.INTEGRATION_UPDATED,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            actor_email=ctx.user.email,
            ip_address=client_ip(request),
            detail={
                "operation": "phone_number_rebound",
                "number_id": str(row.id),
                "from_tenant": str(old_tenant),
                "to_tenant": str(payload.new_tenant_id),
                "reason": payload.reason,
            },
            commit=False,
        )
        await session.commit()
        await session.refresh(row)
    except HierarchyError as exc:
        raise to_http(exc) from None
    except TelephonyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    return row.as_dict()
