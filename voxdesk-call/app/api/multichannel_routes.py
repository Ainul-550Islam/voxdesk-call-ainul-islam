# File: app/api/multichannel_routes.py — Multichannel messaging routes wired to app/messaging/sms.py, MessageChannel (W-11), and MessageWebhookReceipt
"""
Expanded multichannel messaging API (`/api/channels`).
Wired to `app/messaging/sms.py`, `MessageChannel` (`W-11`), and `MessageWebhookReceipt`:
- Channel registration, update, delete, health check, outbound send, inbound/status webhooks, and real delivery receipts
- Fails closed with HTTP 501 (`NOT_CONFIGURED`) when provider credentials are absent
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.enterprise_models import MessageChannel
from app.db.models import MessageWebhookReceipt
from app.db.session import get_session
from app.messaging import sms as sms_service

router = APIRouter(prefix="/api/channels", tags=["multichannel"])

ALLOWED_CHANNEL_TYPES = {"sms", "whatsapp", "email", "webchat"}
ALLOWED_PROVIDERS = {"twilio", "meta", "sendgrid", "internal", "webhook"}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class ChannelCreate(_Strict):
    channel_type: str = Field(pattern="^(sms|whatsapp|email|webchat)$")
    provider: str = Field(pattern="^(twilio|meta|sendgrid|internal|webhook)$")
    config: dict = Field(default_factory=dict)
    is_active: bool = True


class ChannelUpdate(_Strict):
    config: Optional[dict] = None
    is_active: Optional[bool] = None


class ChannelOut(_Strict):
    id: str
    tenant_id: str
    channel_type: str
    provider: str
    is_active: bool
    config: dict
    health_status: str
    last_health_check_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SendMessageRequest(_Strict):
    channel_type: str = Field(pattern="^(sms|whatsapp|email|webchat)$")
    to: str = Field(min_length=3, max_length=200)
    message: str = Field(min_length=1, max_length=4000)
    subject: Optional[str] = Field(default=None, max_length=200)
    metadata: dict = Field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mask_config(cfg: dict) -> dict:
    masked = {}
    for k, v in (cfg or {}).items():
        if any(s in k.lower() for s in ("secret", "token", "key", "password")):
            masked[k] = "***REDACTED***"
        else:
            masked[k] = v
    return masked


def _to_out(row: MessageChannel) -> ChannelOut:
    d = row.as_dict()
    d["config"] = _mask_config(d.get("config", {}))
    return ChannelOut(**d)


@router.post("", response_model=ChannelOut, status_code=201)
async def create_channel(
    payload: ChannelCreate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/channels — Register messaging channel (SMS/WhatsApp/Email/Webchat)."""
    row = MessageChannel(
        tenant_id=ctx.tenant_id,
        channel_type=payload.channel_type,
        provider=payload.provider,
        is_active=payload.is_active,
        config=payload.config,
        health_status="unknown",
        last_health_check_at=None,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.get("", response_model=dict)
async def list_channels(
    channel_type: Optional[str] = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    scope = [MessageChannel.tenant_id == ctx.tenant_id]
    if channel_type:
        scope.append(MessageChannel.channel_type == channel_type)
    total = (
        await session.execute(select(func.count(MessageChannel.id)).where(*scope))
    ).scalar() or 0
    stmt = (
        select(MessageChannel)
        .where(*scope)
        .order_by(MessageChannel.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "channels": [_to_out(r).model_dump() for r in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.get("/{channel_id}", response_model=ChannelOut)
async def get_channel(
    channel_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(MessageChannel, channel_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="channel not found")
    return _to_out(row)


@router.patch("/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: uuid.UUID,
    payload: ChannelUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(MessageChannel, channel_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="channel not found")
    if payload.config is not None:
        row.config = payload.config
    if payload.is_active is not None:
        row.is_active = payload.is_active
    row.updated_at = _now()
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.delete("/{channel_id}")
async def delete_channel(
    channel_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(MessageChannel, channel_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="channel not found")
    await session.delete(row)
    await session.commit()
    return {"id": str(channel_id), "deleted": True}


@router.post("/{channel_id}/health-check", response_model=dict)
async def health_check_channel(
    channel_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/channels/{id}/health-check — Check provider credentials via app/messaging/sms.py."""
    row = await session.get(MessageChannel, channel_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="channel not found")
    try:
        health = await sms_service.check_channel_health(session, row)
        await session.commit()
        return health
    except sms_service.SmsNotConfiguredError as exc:
        await session.commit()
        raise HTTPException(
            status_code=501,
            detail={
                "code": "channel_health_check_not_implemented",
                "status": "NOT_CONFIGURED",
                "message": (
                    f"{row.provider} {row.channel_type} health checks are not configured ({exc.message}); "
                    "channel health_status remains unknown."
                ),
            },
        ) from exc


@router.post("/send", response_model=dict, status_code=201)
async def send_channel_message(
    payload: SendMessageRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/channels/send — Send message over configured channel via app/messaging/sms.py."""
    channel = (
        await session.execute(
            select(MessageChannel)
            .where(
                MessageChannel.tenant_id == ctx.tenant_id,
                MessageChannel.channel_type == payload.channel_type,
                MessageChannel.is_active.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if channel is None:
        raise HTTPException(
            status_code=400,
            detail=f"no active {payload.channel_type} channel configured for tenant",
        )
    try:
        receipt = await sms_service.send_outbound_message(
            session,
            tenant_id=ctx.tenant_id,
            channel=channel,
            to_number=payload.to,
            message=payload.message,
            metadata=payload.metadata,
        )
        await session.commit()
        return receipt
    except sms_service.SmsNotConfiguredError as exc:
        raise HTTPException(
            status_code=501,
            detail={
                "code": "channel_send_not_implemented",
                "status": "NOT_CONFIGURED",
                "message": (
                    f"{channel.provider} {payload.channel_type} adapter is not implemented; "
                    "no message or call was created."
                ),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{channel_id}/receipts", response_model=dict)
async def list_channel_receipts(
    channel_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/channels/{id}/receipts — Real inbound/outbound receipts from MessageWebhookReceipt & MessageChannel."""
    row = await session.get(MessageChannel, channel_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="channel not found")

    db_receipts = list(
        (
            await session.execute(
                select(MessageWebhookReceipt)
                .where(
                    MessageWebhookReceipt.tenant_id == ctx.tenant_id,
                    MessageWebhookReceipt.channel == row.channel_type,
                )
                .order_by(MessageWebhookReceipt.received_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    cfg = row.config if isinstance(row.config, dict) else {}
    status_map = dict(cfg.get("delivery_statuses") or {})
    outbound_map = {
        str(item.get("provider_message_id")): item
        for item in (cfg.get("outbound_receipts") or [])
        if isinstance(item, dict) and item.get("provider_message_id")
    }

    receipts_out = []
    for r in db_receipts:
        st_info = status_map.get(r.provider_message_id) or {}
        out_info = outbound_map.get(r.provider_message_id) or {}
        receipts_out.append(
            {
                "id": str(r.id),
                "provider_message_id": r.provider_message_id,
                "channel_type": r.channel,
                "status": st_info.get("status") or out_info.get("status") or "received",
                "error_code": st_info.get("error_code"),
                "to": out_info.get("to"),
                "from": out_info.get("from"),
                "received_at": r.received_at.isoformat() if r.received_at else None,
            }
        )

    return {
        "channel_id": str(channel_id),
        "channel_type": row.channel_type,
        "provider": row.provider,
        "receipts": receipts_out,
        "total": len(receipts_out),
    }


@router.post("/webhook/sms", response_class=PlainTextResponse)
async def inbound_sms_channel_webhook(
    request: Request,
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(""),
    MessageSid: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """POST /api/channels/webhook/sms — Twilio inbound SMS webhook."""
    if not await sms_service.verify_twilio_sms_signature(request):
        return PlainTextResponse("forbidden", status_code=403)
    res = await sms_service.process_inbound_sms(
        session,
        from_number=From,
        to_number=To,
        body=Body,
        message_sid=MessageSid,
    )
    return PlainTextResponse(res["twiml"], media_type="application/xml")


@router.post("/webhook/status", response_class=PlainTextResponse)
async def status_sms_channel_webhook(
    request: Request,
    MessageSid: str = Form(""),
    MessageStatus: str = Form(""),
    To: str = Form(""),
    From: str = Form(""),
    ErrorCode: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """POST /api/channels/webhook/status — Twilio SMS delivery-status callback."""
    if not await sms_service.verify_twilio_sms_signature(request):
        return PlainTextResponse("forbidden", status_code=403)
    await sms_service.process_status_callback(
        session,
        message_sid=MessageSid,
        message_status=MessageStatus,
        to_number=To,
        from_number=From,
        error_code=ErrorCode,
    )
    return PlainTextResponse("", media_type="application/xml")
