"""Multi-channel messaging API (``/api/channels``) wired to the ``ChannelAdapter`` registry (Capability 11).

Supports ``twilio:sms``, ``twilio:whatsapp``, ``telnyx:sms``, and ``vonage:sms``:
- Unsupported channels (e.g. ``slack``, ``teams``, ``email``, ``webchat``) fail with HTTP 422 (``UNSUPPORTED_CHANNEL``).
- Unconfigured provider credentials fail closed with HTTP 409 (``NOT_CONFIGURED``).
- Upstream carrier rejections fail with HTTP 502 (``UPSTREAM_PROVIDER_ERROR``).
- Inbound webhooks verify provider cryptographic signatures (Twilio HMAC-SHA1, Telnyx Ed25519, Vonage JWT/HMAC) and enforce TCPA/CTIA ``STOP`` / ``START`` / ``HELP`` DNC transitions.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from xml.sax.saxutils import escape as xml_escape

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
from app.messaging.channels import (
    ChannelDeliveryError,
    ChannelNotConfiguredError,
    ChannelUnsupportedError,
    apply_inbound_keyword_dnc,
    get_channel_adapter,
    list_registered_channel_adapters,
    normalize_e164,
    resolve_tenant_for_destination,
)
from app.schemas.messaging import KeywordDisposition
from app.telephony import dnc

router = APIRouter(prefix="/api/channels", tags=["multichannel"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class ChannelCreate(_Strict):
    channel_type: str = Field(..., min_length=1, max_length=32)
    provider: str = Field(..., min_length=1, max_length=32)
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class ChannelUpdate(_Strict):
    config: Optional[dict[str, Any]] = None
    is_active: Optional[bool] = None


class ChannelOut(_Strict):
    id: str
    tenant_id: str
    channel_type: str
    provider: str
    is_active: bool
    config: dict[str, Any]
    health_status: str
    last_health_check_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SendMessageRequest(_Strict):
    channel_type: str = Field(..., min_length=1, max_length=32)
    provider: Optional[str] = Field(default=None, max_length=32)
    to: str = Field(..., min_length=3, max_length=200)
    from_number: Optional[str] = Field(default=None, max_length=200)
    message: str = Field(..., min_length=1, max_length=4000)
    media_urls: list[str] = Field(default_factory=list)
    template_sid: Optional[str] = Field(default=None, max_length=128)
    template_variables: dict[str, Any] = Field(default_factory=dict)
    subject: Optional[str] = Field(default=None, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mask_config(cfg: dict[str, Any]) -> dict[str, Any]:
    masked: dict[str, Any] = {}
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


def _twiml_reply(text: str | None) -> str:
    if not text:
        return '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
    return f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{xml_escape(text)}</Message></Response>'


async def _record_inbound_receipt_and_keywords(
    session: AsyncSession,
    *,
    provider: str,
    channel_type: str,
    provider_message_id: str,
    from_e164: str,
    to_e164: str,
    body: str,
) -> dict[str, Any]:
    existing = await session.scalar(
        select(MessageWebhookReceipt).where(
            MessageWebhookReceipt.channel == channel_type,
            MessageWebhookReceipt.provider_message_id == provider_message_id,
        )
    )
    if existing is not None:
        return {
            "duplicate": True,
            "provider_message_id": provider_message_id,
            "keyword": KeywordDisposition.NONE.value,
            "reply": None,
        }

    tenant_id = await resolve_tenant_for_destination(
        session,
        to_e164=to_e164,
        channel_type=channel_type,
        provider=provider,
    )
    keyword, reply = await apply_inbound_keyword_dnc(
        session,
        tenant_id=tenant_id,
        phone_e164=from_e164,
        body=body,
        source=f"{provider}_{channel_type}_webhook",
    )

    if tenant_id is not None:
        receipt = MessageWebhookReceipt(
            tenant_id=tenant_id,
            channel=channel_type,
            provider_message_id=provider_message_id,
            received_at=_now(),
        )
        session.add(receipt)
    await session.commit()

    return {
        "duplicate": False,
        "provider_message_id": provider_message_id,
        "tenant_id": str(tenant_id) if tenant_id else None,
        "keyword": keyword.value,
        "reply": reply,
    }


@router.get("/adapters", response_model=dict)
async def get_channel_adapters(
    _ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
) -> dict[str, Any]:
    """List supported messaging ``(provider, channel_type)`` adapters and deployment readiness."""
    adapters = [item.model_dump() for item in list_registered_channel_adapters()]
    return {"adapters": adapters, "total": len(adapters)}


@router.post("", response_model=ChannelOut, status_code=201)
async def create_channel(
    payload: ChannelCreate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ChannelOut:
    chan_type = payload.channel_type.strip().lower()
    prov = payload.provider.strip().lower()
    try:
        adapter = get_channel_adapter(prov, chan_type)
    except ChannelUnsupportedError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "UNSUPPORTED_CHANNEL", "message": str(exc)},
        ) from None

    existing = (
        await session.execute(
            select(MessageChannel).where(
                MessageChannel.tenant_id == ctx.tenant_id,
                MessageChannel.channel_type == chan_type,
                MessageChannel.provider == prov,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "CHANNEL_ALREADY_EXISTS",
                "message": f"{prov} {chan_type} channel already exists for tenant",
            },
        )

    health = adapter.health_check(payload.config)
    now = _now()
    row = MessageChannel(
        tenant_id=ctx.tenant_id,
        channel_type=chan_type,
        provider=prov,
        config=payload.config or {},
        is_active=payload.is_active,
        health_status="healthy" if health.configured else "unknown",
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.get("", response_model=dict)
async def list_channels(
    channel_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    scope = [MessageChannel.tenant_id == ctx.tenant_id]
    if channel_type:
        scope.append(MessageChannel.channel_type == channel_type.strip().lower())
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
) -> ChannelOut:
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
) -> ChannelOut:
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
) -> dict[str, Any]:
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
) -> dict[str, Any]:
    """Check provider credentials for a tenant messaging channel (409 ``NOT_CONFIGURED`` when absent)."""
    row = await session.get(MessageChannel, channel_id)
    if row is None or row.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail="channel not found")

    try:
        adapter = get_channel_adapter(row.provider, row.channel_type)
    except ChannelUnsupportedError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "UNSUPPORTED_CHANNEL", "message": str(exc)},
        ) from None

    now = _now()
    row.last_health_check_at = now
    row.updated_at = now
    health = adapter.health_check(row.config if isinstance(row.config, dict) else {})
    if not health.configured:
        row.health_status = "unconfigured"
        await session.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NOT_CONFIGURED",
                "status": "NOT_CONFIGURED",
                "missing": health.missing_credentials,
                "message": (
                    f"{row.provider} {row.channel_type} credentials are NOT_CONFIGURED "
                    f"({health.reason}); channel health_status is unconfigured."
                ),
            },
        )

    row.health_status = "healthy"
    await session.commit()
    return {
        "channel_id": str(row.id),
        "provider": row.provider,
        "channel_type": row.channel_type,
        "status": "healthy",
        "configured": True,
        "checked_at": now.isoformat(),
    }


@router.post("/send", response_model=dict, status_code=201)
async def send_channel_message(
    payload: SendMessageRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Send an outbound SMS or WhatsApp message via the configured ``ChannelAdapter``."""
    chan_type = payload.channel_type.strip().lower()
    requested_prov = payload.provider.strip().lower() if payload.provider else None

    if chan_type not in {"sms", "whatsapp"}:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "UNSUPPORTED_CHANNEL",
                "message": f"Channel '{chan_type}' is not supported; supported channels are sms and whatsapp.",
            },
        )
    if requested_prov is not None:
        try:
            get_channel_adapter(requested_prov, chan_type)
        except ChannelUnsupportedError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "UNSUPPORTED_CHANNEL", "message": str(exc)},
            ) from None

    stmt = select(MessageChannel).where(
        MessageChannel.tenant_id == ctx.tenant_id,
        MessageChannel.channel_type == chan_type,
        MessageChannel.is_active.is_(True),
    )
    if requested_prov:
        stmt = stmt.where(MessageChannel.provider == requested_prov)
    channel = (await session.execute(stmt.limit(1))).scalar_one_or_none()
    if channel is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NOT_CONFIGURED",
                "status": "NOT_CONFIGURED",
                "message": (
                    f"No active {chan_type} channel is configured for this tenant; "
                    "no message or call was created."
                ),
            },
        )

    try:
        adapter = get_channel_adapter(channel.provider, channel.channel_type)
    except ChannelUnsupportedError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "UNSUPPORTED_CHANNEL", "message": str(exc)},
        ) from None

    try:
        dest_e164 = normalize_e164(payload.to)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": str(exc)},
        ) from None

    if (await dnc.evaluate_dnc(session, ctx.tenant_id, dest_e164)).blocked:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "RECIPIENT_OPTED_OUT",
                "message": "Recipient is on the tenant Do-Not-Contact list; message blocked.",
            },
        )

    cfg = dict(channel.config or {})
    default_sender = (
        payload.from_number
        or cfg.get("from_number")
        or cfg.get("phone_number")
        or cfg.get("whatsapp_number")
        or getattr(ctx.tenant, "twilio_number", None)
    )
    try:
        result = await adapter.send_message(
            to_number=payload.to,
            from_number=str(default_sender) if default_sender else None,
            body=payload.message,
            media_urls=payload.media_urls,
            template_sid=payload.template_sid,
            template_variables=payload.template_variables,
            metadata=payload.metadata,
            channel_config=cfg,
        )
    except ChannelNotConfiguredError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NOT_CONFIGURED",
                "status": "NOT_CONFIGURED",
                "missing": exc.missing,
                "message": str(exc),
            },
        ) from None
    except ChannelDeliveryError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "UPSTREAM_PROVIDER_ERROR", "message": str(exc)},
        ) from None
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": str(exc)},
        ) from None

    now = _now()
    receipt_row = MessageWebhookReceipt(
        tenant_id=ctx.tenant_id,
        channel=channel.channel_type,
        provider_message_id=result.provider_message_id,
        received_at=now,
    )
    session.add(receipt_row)
    outbound_receipts = list(cfg.get("outbound_receipts") or [])
    outbound_receipts.append(
        {
            "provider_message_id": result.provider_message_id,
            "status": result.status,
            "to": result.to_address,
            "from": result.from_address,
            "sent_at": now.isoformat(),
        }
    )
    cfg["outbound_receipts"] = outbound_receipts[-200:]
    channel.config = cfg
    channel.updated_at = now
    await session.commit()

    return {
        "channel_id": str(channel.id),
        "provider": result.provider,
        "channel_type": result.channel_type,
        "provider_message_id": result.provider_message_id,
        "status": result.status,
        "to": result.to_address,
        "from": result.from_address,
        "sent_at": now.isoformat(),
    }


@router.get("/{channel_id}/receipts", response_model=dict)
async def list_channel_receipts(
    channel_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return persisted inbound/outbound receipts from ``MessageWebhookReceipt`` & ``MessageChannel``."""
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


@router.post("/webhook/whatsapp", response_class=PlainTextResponse)
async def inbound_whatsapp_channel_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """POST /api/channels/webhook/whatsapp — Twilio WhatsApp inbound webhook with HMAC-SHA1 verification."""
    adapter = get_channel_adapter("twilio", "whatsapp")
    try:
        valid = await adapter.verify_webhook(request)
    except ChannelNotConfiguredError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "NOT_CONFIGURED", "message": str(exc)},
        ) from None
    if not valid:
        return PlainTextResponse("forbidden", status_code=403)

    form = await request.form()
    payload = {str(k): str(v) for k, v in form.items()}
    try:
        event = adapter.parse_inbound(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": str(exc)},
        ) from None

    outcome = await _record_inbound_receipt_and_keywords(
        session,
        provider="twilio",
        channel_type="whatsapp",
        provider_message_id=event.provider_message_id,
        from_e164=event.from_e164,
        to_e164=event.to_e164,
        body=event.body,
    )
    return PlainTextResponse(_twiml_reply(outcome.get("reply")), media_type="application/xml")


@router.post("/webhook/telnyx", response_model=dict)
async def inbound_telnyx_channel_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """POST /api/channels/webhook/telnyx — Telnyx v2 inbound SMS webhook with Ed25519 signature verification."""
    raw_body = await request.body()
    adapter = get_channel_adapter("telnyx", "sms")
    try:
        valid = await adapter.verify_webhook(request, raw_body=raw_body)
    except ChannelNotConfiguredError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "NOT_CONFIGURED", "message": str(exc)},
        ) from None
    if not valid:
        raise HTTPException(
            status_code=403,
            detail={"code": "INVALID_WEBHOOK_SIGNATURE", "message": "Telnyx Ed25519 signature verification failed."},
        )

    try:
        parsed = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        event = adapter.parse_inbound(parsed)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": str(exc)},
        ) from None

    return await _record_inbound_receipt_and_keywords(
        session,
        provider="telnyx",
        channel_type="sms",
        provider_message_id=event.provider_message_id,
        from_e164=event.from_e164,
        to_e164=event.to_e164,
        body=event.body,
    )


@router.post("/webhook/vonage", response_model=dict)
async def inbound_vonage_channel_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """POST /api/channels/webhook/vonage — Vonage Messages v1 inbound SMS webhook with JWT/HMAC verification."""
    raw_body = await request.body()
    adapter = get_channel_adapter("vonage", "sms")
    try:
        valid = await adapter.verify_webhook(request, raw_body=raw_body)
    except ChannelNotConfiguredError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "NOT_CONFIGURED", "message": str(exc)},
        ) from None
    if not valid:
        raise HTTPException(
            status_code=403,
            detail={"code": "INVALID_WEBHOOK_SIGNATURE", "message": "Vonage webhook signature verification failed."},
        )

    payload: dict[str, Any] = dict(request.query_params)
    if raw_body:
        try:
            body_json = json.loads(raw_body.decode("utf-8"))
            if isinstance(body_json, dict):
                payload.update(body_json)
        except ValueError:
            pass
    try:
        event = adapter.parse_inbound(payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": str(exc)},
        ) from None

    return await _record_inbound_receipt_and_keywords(
        session,
        provider="vonage",
        channel_type="sms",
        provider_message_id=event.provider_message_id,
        from_e164=event.from_e164,
        to_e164=event.to_e164,
        body=event.body,
    )


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
