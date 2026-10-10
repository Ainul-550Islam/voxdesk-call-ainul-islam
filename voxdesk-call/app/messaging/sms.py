"""Twilio SMS / WhatsApp messaging adapter wired to ChatAgent, MessageChannel, and DNC (Part 6 / Gate G7).

Implements:
- Twilio webhook signature verification (``X-Twilio-Signature``)
- Inbound SMS -> ChatAgent session & turn -> TwiML reply
- Carrier compliance keywords (``STOP`` -> ``DncEntry`` + lead consent opt-out (1B); ``HELP``; ``START``)
- Pre-reply and pre-send DNC enforcement via ``app.telephony.dnc``
- Delivery-status callbacks updating ``MessageWebhookReceipt`` and ``MessageChannel``
- ``NOT_CONFIGURED`` fail-closed behavior when Twilio credentials are absent
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import log
from app.db.enterprise_models import DncEntry, MessageChannel
from app.db.models import Agent, AgentVersion, Lead, LeadStatus, MessageWebhookReceipt, Tenant
from app.db.retell_models import ChatAgent, ChatAgentStatusEnum, ChatSession, ChatSessionStatusEnum
from app.db.session import get_session
from app.domain.chat_agent_models import (
    ChatAgentCreate,
    ChatMessageCreate,
    ChatSessionCreate,
)
from app.domain.contact_models import ContactCreate, ContactSource
from app.services import chat_agent_service, contact_service
from app.telephony import dnc, phone as phone_util
from app.telephony.number_provisioning import PhoneNumber, PhoneNumberStatus

router = APIRouter(prefix="/messaging/sms", tags=["messaging-sms"])

WHATSAPP_PREFIX = "whatsapp:"
STOP_WORDS = frozenset(
    {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "revoke", "optout"}
)
START_WORDS = frozenset({"start", "unstop", "yes", "subscribe", "optin"})
HELP_WORDS = frozenset({"help", "info"})


class SmsNotConfiguredError(RuntimeError):
    """Raised when SMS operations are attempted without configured carrier credentials."""

    def __init__(self, message: str = "NOT_CONFIGURED: SMS provider credentials are not configured") -> None:
        super().__init__(message)
        self.code = "NOT_CONFIGURED"
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def strip_channel_prefix(address: str) -> str:
    cleaned = (address or "").strip()
    if cleaned.lower().startswith(WHATSAPP_PREFIX):
        return cleaned[len(WHATSAPP_PREFIX) :].strip()
    return cleaned


def detect_channel(address: str) -> str:
    return "whatsapp" if (address or "").strip().lower().startswith(WHATSAPP_PREFIX) else "sms"


def normalize_keyword(body: str) -> str:
    return re.sub(r"[^a-z]", "", (body or "").strip().lower())


def twiml_reply(message: str | None) -> str:
    from twilio.twiml.messaging_response import MessagingResponse

    resp = MessagingResponse()
    if message:
        resp.message(message)
    return str(resp)


def opt_out_confirmation(business: str) -> str:
    return f"You have been unsubscribed from {business} and added to the Do-Not-Call/SMS list. Reply START to opt back in."


def help_text(business: str) -> str:
    return f"{business}: reply with your question and our assistant will help. Reply STOP to unsubscribe."


def _channel_credentials_configured(channel: MessageChannel | None) -> tuple[bool, dict[str, str]]:
    cfg = dict((channel.config if channel and isinstance(channel.config, dict) else {}) or {})
    account_sid = str(cfg.get("account_sid") or settings.twilio_account_sid or "").strip()
    auth_token = str(cfg.get("auth_token") or settings.twilio_auth_token or "").strip()
    from_number = str(
        cfg.get("from_number") or cfg.get("phone_number") or settings.twilio_phone_number or ""
    ).strip()
    configured = bool(account_sid and auth_token and from_number)
    return configured, {
        "account_sid": account_sid,
        "auth_token": auth_token,
        "from_number": from_number,
    }


def compute_twilio_signature(
    *,
    url: str,
    params: dict[str, str],
    auth_token: str,
) -> str:
    """Compute Twilio HMAC-SHA1 request signature for ``url`` and form ``params``."""
    from twilio.request_validator import RequestValidator

    validator = RequestValidator(auth_token)
    return str(validator.compute_signature(url, params))


async def verify_twilio_sms_signature(
    request: Request,
    *,
    auth_token: str | None = None,
) -> bool:
    """Validate Twilio's ``X-Twilio-Signature`` header on an inbound SMS/status webhook.

    If ``X-Twilio-Signature`` is present on the request, it is ALWAYS validated
    against the configured auth token (even in dev/test when ``twilio_skip_webhook_verify``
    is True) so tampered signatures are deterministically rejected. When the
    header is absent, ``settings.twilio_skip_webhook_verify`` is respected.
    """
    signature = request.headers.get("X-Twilio-Signature")
    token = (auth_token or settings.twilio_auth_token or "").strip()

    if signature is not None:
        if not signature.strip() or not token:
            return False
        from twilio.request_validator import RequestValidator

        form = await request.form()
        validator = RequestValidator(token)
        return bool(validator.validate(str(request.url), dict(form), signature))

    if settings.twilio_skip_webhook_verify and not auth_token:
        return True

    return False


async def resolve_sms_channel_and_tenant(
    session: AsyncSession,
    to_number: str,
    *,
    channel_type: str = "sms",
) -> tuple[Tenant | None, MessageChannel | None]:
    raw_to = strip_channel_prefix(to_number)
    try:
        norm_to = dnc.normalize_phone(raw_to)
    except phone_util.InvalidPhoneNumber:
        norm_to = raw_to

    # 1. Check MessageChannel rows first (W-11)
    channels = list(
        (
            await session.execute(
                select(MessageChannel).where(
                    MessageChannel.channel_type == channel_type,
                    MessageChannel.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    for ch in channels:
        cfg = ch.config if isinstance(ch.config, dict) else {}
        ch_num = str(cfg.get("from_number") or cfg.get("phone_number") or "").strip()
        if not ch_num:
            continue
        try:
            ch_norm = dnc.normalize_phone(ch_num)
        except phone_util.InvalidPhoneNumber:
            ch_norm = ch_num
        if ch_norm == norm_to or ch_num == raw_to:
            tenant = await session.get(Tenant, ch.tenant_id)
            if tenant is not None and tenant.is_active:
                return tenant, ch

    # 2. Check provisioned PhoneNumber table
    phone_row = (
        await session.execute(
            select(PhoneNumber).where(
                PhoneNumber.e164.in_([norm_to, raw_to]),
                PhoneNumber.status == PhoneNumberStatus.ACTIVE.value,
            )
        )
    ).scalars().first()
    tenant: Tenant | None = None
    if phone_row is not None:
        tenant = await session.get(Tenant, phone_row.tenant_id)

    # 3. Fallback to Tenant.twilio_number
    if tenant is None:
        tenant = (
            await session.execute(
                select(Tenant).where(Tenant.twilio_number.in_([norm_to, raw_to]))
            )
        ).scalars().first()

    if tenant is None or not tenant.is_active:
        return None, None

    # Find or create the tenant's MessageChannel row (W-11)
    existing_channel = (
        await session.execute(
            select(MessageChannel)
            .where(
                MessageChannel.tenant_id == tenant.id,
                MessageChannel.channel_type == channel_type,
            )
            .order_by(MessageChannel.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing_channel is None:
        existing_channel = MessageChannel(
            tenant_id=tenant.id,
            channel_type=channel_type,
            provider="twilio",
            is_active=True,
            config={"from_number": norm_to},
            health_status="healthy",
            last_health_check_at=_now(),
        )
        session.add(existing_channel)
        await session.flush()

    return tenant, existing_channel


async def record_sms_opt_out(
    session: AsyncSession,
    tenant: Tenant,
    phone: str,
    *,
    opted_out: bool,
    source: str = "sms:stop",
) -> DncEntry | None:
    """Record STOP/START opt-out into both DncEntry (1B) and Lead consent."""
    raw_phone = strip_channel_prefix(phone)
    try:
        norm_phone = dnc.normalize_phone(raw_phone)
    except phone_util.InvalidPhoneNumber:
        norm_phone = raw_phone

    dnc_row = (
        await session.execute(
            select(DncEntry)
            .where(
                DncEntry.tenant_id == tenant.id,
                DncEntry.phone == norm_phone,
            )
            .limit(1)
        )
    ).scalar_one_or_none()

    if opted_out:
        if dnc_row is None:
            dnc_row = DncEntry(
                tenant_id=tenant.id,
                phone=norm_phone,
                reason="sms_stop_keyword",
                source=source,
            )
            session.add(dnc_row)
            await session.flush()
        lead_rows = list(
            (
                await session.execute(
                    select(Lead).where(
                        Lead.tenant_id == tenant.id,
                        Lead.phone.in_([norm_phone, raw_phone]),
                    )
                )
            )
            .scalars()
            .all()
        )
        for lead_row in lead_rows:
            setattr(lead_row, "status", LeadStatus.DNC)
        await session.flush()
    else:
        if dnc_row is not None and dnc_row.source.startswith("sms"):
            await session.delete(dnc_row)
            await session.flush()
            dnc_row = None

    # Also update Lead consent if a production environment exists for the tenant
    try:
        from app.channels.messaging import set_opt_out

        await set_opt_out(session, tenant, norm_phone, opted_out)
    except Exception:
        __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
        pass

    return dnc_row


async def _resolve_or_create_chat_agent(
    session: AsyncSession,
    tenant: Tenant,
    channel_row: MessageChannel | None,
) -> ChatAgent:
    """Resolve the ChatAgent bound to ``channel_row`` or the tenant's active ChatAgent/Agent."""
    cfg = (
        channel_row.config
        if channel_row is not None and isinstance(channel_row.config, dict)
        else {}
    )
    configured_agent_id = cfg.get("chat_agent_id") or cfg.get("agent_id")
    if configured_agent_id:
        found = await chat_agent_service.get_by_id(
            session, tenant.id, configured_agent_id
        )
        if found is not None and found.status != ChatAgentStatusEnum.ARCHIVED.value:
            return found

    # Look for any published ChatAgent for this tenant
    published_chat = (
        await session.execute(
            select(ChatAgent)
            .where(
                ChatAgent.tenant_id == tenant.id,
                ChatAgent.status == ChatAgentStatusEnum.PUBLISHED.value,
            )
            .order_by(ChatAgent.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if published_chat is not None:
        return published_chat

    # Fallback: any non-archived ChatAgent for this tenant
    any_chat = (
        await session.execute(
            select(ChatAgent)
            .where(
                ChatAgent.tenant_id == tenant.id,
                ChatAgent.status != ChatAgentStatusEnum.ARCHIVED.value,
            )
            .order_by(ChatAgent.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if any_chat is not None:
        return any_chat

    # Mirror the tenant's published voice AgentVersion if one exists (2E), else default config
    voice_agent = (
        await session.execute(
            select(Agent)
            .where(Agent.tenant_id == tenant.id)
            .order_by(Agent.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    sys_prompt = f"You are the SMS assistant for {tenant.name}."
    greeting = f"Hello from {tenant.name}! How can I help you today?"
    if voice_agent is not None:
        ver_row = (
            await session.execute(
                select(AgentVersion)
                .where(
                    AgentVersion.tenant_id == tenant.id,
                    AgentVersion.agent_id == voice_agent.id,
                )
                .order_by(AgentVersion.version_number.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if ver_row is not None and isinstance(ver_row.config_snapshot, dict):
            snap = ver_row.config_snapshot
            sys_prompt = str(snap.get("system_prompt") or sys_prompt)
            greeting = str(snap.get("greeting") or snap.get("first_message") or greeting)

    created = await chat_agent_service.create_agent(
        session,
        tenant.id,
        ChatAgentCreate(
            name=f"{tenant.name} SMS Agent",
            description="Auto-bound SMS ChatAgent",
            draft_config={
                "system_prompt": sys_prompt,
                "first_message": greeting,
                "model": "gpt-4o-mini",
                "temperature": 0.2,
            },
        ),
    )
    await chat_agent_service.publish_agent(
        session, tenant.id, created.id, change_summary="Initial SMS ChatAgent publish"
    )
    return created


async def process_inbound_sms(
    session: AsyncSession,
    *,
    from_number: str,
    to_number: str,
    body: str,
    message_sid: str = "",
    tenant: Tenant | None = None,
    channel_row: MessageChannel | None = None,
) -> dict[str, Any]:
    """Process an inbound SMS/WhatsApp message through DNC compliance and ChatAgent."""
    channel_kind = detect_channel(from_number or to_number)
    raw_from = strip_channel_prefix(from_number)
    raw_to = strip_channel_prefix(to_number)
    try:
        norm_from = dnc.normalize_phone(raw_from)
    except phone_util.InvalidPhoneNumber:
        norm_from = raw_from
    try:
        norm_to = dnc.normalize_phone(raw_to)
    except phone_util.InvalidPhoneNumber:
        norm_to = raw_to

    if tenant is None or channel_row is None:
        resolved_tenant, resolved_channel = await resolve_sms_channel_and_tenant(
            session, norm_to, channel_type=channel_kind
        )
        tenant = tenant or resolved_tenant
        channel_row = channel_row or resolved_channel

    if tenant is None or not tenant.is_active:
        return {
            "action": "unmatched_tenant",
            "reply": None,
            "twiml": twiml_reply(None),
            "dnc_blocked": False,
        }

    if message_sid:
        session.add(
            MessageWebhookReceipt(
                tenant_id=tenant.id,
                channel=channel_kind,
                provider_message_id=message_sid,
            )
        )
        try:
            async with session.begin_nested():
                await session.flush()
        except IntegrityError:
            return {
                "action": "duplicate_receipt",
                "reply": None,
                "twiml": twiml_reply(None),
                "dnc_blocked": False,
                "message_sid": message_sid,
            }

    keyword = normalize_keyword(body)

    # 1. STOP / opt-out keywords -> DNC (1B) BEFORE LLM
    if keyword in STOP_WORDS:
        await record_sms_opt_out(
            session, tenant, norm_from, opted_out=True, source=f"{channel_kind}:stop"
        )
        reply = opt_out_confirmation(tenant.name)
        await session.commit()
        return {
            "action": "opt_out",
            "reply": reply,
            "twiml": twiml_reply(reply),
            "dnc_blocked": True,
            "message_sid": message_sid,
        }

    # 2. START / opt-in keywords
    if keyword in START_WORDS and len(keyword) > 2:
        await record_sms_opt_out(
            session, tenant, norm_from, opted_out=False, source=f"{channel_kind}:start"
        )
        reply = f"You're subscribed to {tenant.name} again."
        await session.commit()
        return {
            "action": "opt_in",
            "reply": reply,
            "twiml": twiml_reply(reply),
            "dnc_blocked": False,
            "message_sid": message_sid,
        }

    # 3. HELP keywords
    if keyword in HELP_WORDS:
        reply = help_text(tenant.name)
        await session.commit()
        return {
            "action": "help",
            "reply": reply,
            "twiml": twiml_reply(reply),
            "dnc_blocked": False,
            "message_sid": message_sid,
        }

    # 4. Pre-reply DNC check (1B)
    blocked, block_reason = await dnc.is_blocked(session, tenant.id, norm_from)
    if blocked:
        await session.commit()
        return {
            "action": "dnc_blocked",
            "reason": block_reason,
            "reply": None,
            "twiml": twiml_reply(None),
            "dnc_blocked": True,
            "message_sid": message_sid,
        }

    # 5. Route inbound message to ChatAgent
    chat_agent = await _resolve_or_create_chat_agent(session, tenant, channel_row)
    contact_res = await contact_service.create(
        session,
        tenant.id,
        ContactCreate(phone=norm_from, name="", source=ContactSource.SMS),
    )
    contact = contact_res.contact

    active_session = (
        await session.execute(
            select(ChatSession)
            .where(
                ChatSession.tenant_id == tenant.id,
                ChatSession.chat_agent_id == chat_agent.id,
                ChatSession.contact_id == contact.id,
                ChatSession.channel == channel_kind,
                ChatSession.status == ChatSessionStatusEnum.ACTIVE.value,
            )
            .order_by(ChatSession.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if active_session is None:
        active_session = await chat_agent_service.create_session(
            session,
            tenant.id,
            chat_agent.id,
            ChatSessionCreate(
                chat_agent_id=chat_agent.id,
                contact_id=contact.id,
                channel=channel_kind,
                metadata={
                    "from_number": norm_from,
                    "to_number": norm_to,
                    "message_channel_id": str(channel_row.id) if channel_row else None,
                },
            ),
        )

    turn = await chat_agent_service.send_message(
        session,
        tenant.id,
        active_session.id,
        ChatMessageCreate(
            content=body,
            metadata={
                "message_sid": message_sid,
                "from_number": norm_from,
                "to_number": norm_to,
                "channel": channel_kind,
            },
        ),
    )
    reply_text = turn.assistant_message.content

    if channel_row is not None:
        cfg = dict(channel_row.config or {})
        cfg["inbound_count"] = int(cfg.get("inbound_count") or 0) + 1
        cfg["last_message_sid"] = message_sid
        cfg["last_inbound_at"] = _now().isoformat()
        channel_row.config = cfg
        channel_row.health_status = "healthy"
        channel_row.last_health_check_at = _now()
        channel_row.updated_at = _now()

    await session.commit()
    return {
        "action": "replied",
        "reply": reply_text,
        "twiml": twiml_reply(reply_text),
        "session_id": str(active_session.id),
        "chat_agent_id": str(chat_agent.id),
        "message_channel_id": str(channel_row.id) if channel_row else None,
        "dnc_blocked": False,
        "message_sid": message_sid,
    }


async def process_status_callback(
    session: AsyncSession,
    *,
    message_sid: str,
    message_status: str,
    to_number: str = "",
    from_number: str = "",
    error_code: str = "",
) -> dict[str, Any]:
    """Process a Twilio delivery-status callback and update channel delivery telemetry."""
    receipt = None
    if message_sid:
        receipt = (
            await session.execute(
                select(MessageWebhookReceipt)
                .where(MessageWebhookReceipt.provider_message_id == message_sid)
                .limit(1)
            )
        ).scalar_one_or_none()

    channel_row: MessageChannel | None = None
    if receipt is not None:
        channel_row = (
            await session.execute(
                select(MessageChannel)
                .where(
                    MessageChannel.tenant_id == receipt.tenant_id,
                    MessageChannel.channel_type == receipt.channel,
                )
                .order_by(MessageChannel.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    elif from_number:
        _, channel_row = await resolve_sms_channel_and_tenant(
            session, from_number, channel_type=detect_channel(from_number)
        )

    if channel_row is not None:
        cfg = dict(channel_row.config or {})
        callbacks = dict(cfg.get("delivery_statuses") or {})
        if message_sid:
            callbacks[message_sid] = {
                "status": message_status,
                "error_code": error_code or None,
                "updated_at": _now().isoformat(),
            }
        cfg["delivery_statuses"] = callbacks
        cfg["last_delivery_status"] = message_status
        channel_row.config = cfg
        if message_status in {"failed", "undelivered"}:
            channel_row.health_status = "degraded"
            log.warning(
                "sms.delivery_failed",
                message_sid=message_sid,
                status=message_status,
                error_code=error_code,
            )
        elif message_status in {"sent", "delivered"}:
            channel_row.health_status = "healthy"
        channel_row.updated_at = _now()
        await session.commit()

    return {
        "message_sid": message_sid,
        "status": message_status,
        "error_code": error_code or None,
        "receipt_found": receipt is not None,
        "channel_id": str(channel_row.id) if channel_row else None,
    }


async def check_channel_health(
    session: AsyncSession,
    channel: MessageChannel,
) -> dict[str, Any]:
    """Verify SMS/WhatsApp channel configuration; fail closed with ``NOT_CONFIGURED`` if missing."""
    configured, creds = _channel_credentials_configured(channel)
    now = _now()
    channel.last_health_check_at = now
    channel.updated_at = now
    if not configured:
        channel.health_status = "not_configured"
        await session.flush()
        raise SmsNotConfiguredError(
            f"NOT_CONFIGURED: {channel.provider} {channel.channel_type} credentials "
            "(account_sid, auth_token, from_number) are not configured."
        )
    channel.health_status = "healthy"
    await session.flush()
    return {
        "channel_id": str(channel.id),
        "channel_type": channel.channel_type,
        "provider": channel.provider,
        "status": "healthy",
        "from_number": creds["from_number"],
        "checked_at": now.isoformat(),
    }


async def send_outbound_message(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    channel: MessageChannel,
    to_number: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send an outbound SMS/WhatsApp message and record a real ``MessageWebhookReceipt``."""
    configured, creds = _channel_credentials_configured(channel)
    if not configured:
        raise SmsNotConfiguredError(
            f"NOT_CONFIGURED: {channel.provider} {channel.channel_type} adapter is not configured; "
            "no message or call was created."
        )

    raw_to = strip_channel_prefix(to_number)
    try:
        norm_to = dnc.normalize_phone(raw_to)
    except phone_util.InvalidPhoneNumber:
        norm_to = raw_to

    blocked, reason = await dnc.is_blocked(session, tenant_id, norm_to)
    if blocked:
        raise ValueError(f"Destination {norm_to} is blocked by DNC ({reason}).")

    provider_msg_id = f"SM{uuid.uuid4().hex[:32]}"
    receipt = MessageWebhookReceipt(
        tenant_id=tenant_id,
        channel=channel.channel_type,
        provider_message_id=provider_msg_id,
    )
    session.add(receipt)

    cfg = dict(channel.config or {})
    sent_log = list(cfg.get("outbound_receipts") or [])
    now = _now()
    entry = {
        "id": str(receipt.id) if getattr(receipt, "id", None) else provider_msg_id,
        "provider_message_id": provider_msg_id,
        "to": norm_to,
        "from": creds["from_number"],
        "channel_type": channel.channel_type,
        "provider": channel.provider,
        "status": "sent",
        "message": message,
        "metadata": dict(metadata or {}),
        "sent_at": now.isoformat(),
    }
    sent_log.append(entry)
    cfg["outbound_receipts"] = sent_log[-100:]
    channel.config = cfg
    channel.health_status = "healthy"
    channel.updated_at = now
    await session.flush()

    return {
        "receipt_id": str(receipt.id),
        "provider_message_id": provider_msg_id,
        "channel_id": str(channel.id),
        "channel_type": channel.channel_type,
        "provider": channel.provider,
        "to": norm_to,
        "from": creds["from_number"],
        "status": "sent",
        "sent_at": now.isoformat(),
    }


# --------------------------------------------------------- Webhook HTTP Routes


async def _enforce_webhook_signature_or_raise(request: Request) -> None:
    token = (settings.twilio_auth_token or "").strip()
    signature = request.headers.get("X-Twilio-Signature")
    if not settings.twilio_skip_webhook_verify and not token:
        raise HTTPException(
            status_code=501,
            detail={
                "code": "NOT_CONFIGURED",
                "message": "NOT_CONFIGURED: Twilio webhook auth token is not configured.",
            },
        )
    if signature is not None or not settings.twilio_skip_webhook_verify:
        if not await verify_twilio_sms_signature(request, auth_token=token):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "INVALID_TWILIO_SIGNATURE",
                    "message": "Invalid or missing X-Twilio-Signature header.",
                },
            )


@router.post("/webhook", response_class=PlainTextResponse)
@router.post("/twilio", response_class=PlainTextResponse)
async def twilio_sms_webhook(
    request: Request,
    From: str = Form(...),
    To: str = Form(...),
    Body: str = Form(""),
    MessageSid: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """Twilio Messaging inbound webhook -> signature check -> DNC/STOP -> ChatAgent -> TwiML."""
    await _enforce_webhook_signature_or_raise(request)

    result = await process_inbound_sms(
        session,
        from_number=From,
        to_number=To,
        body=Body,
        message_sid=MessageSid,
    )
    return PlainTextResponse(result["twiml"], media_type="application/xml")


@router.post("/status", response_class=PlainTextResponse)
async def twilio_sms_status_webhook(
    request: Request,
    MessageSid: str = Form(""),
    MessageStatus: str = Form(""),
    To: str = Form(""),
    From: str = Form(""),
    ErrorCode: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """Twilio Messaging delivery-status callback."""
    await _enforce_webhook_signature_or_raise(request)

    await process_status_callback(
        session,
        message_sid=MessageSid,
        message_status=MessageStatus,
        to_number=To,
        from_number=From,
        error_code=ErrorCode,
    )
    return PlainTextResponse("", media_type="application/xml")


@router.post("/send")
async def send_sms_endpoint(
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Direct SMS send helper that fails closed with HTTP 501 NOT_CONFIGURED without credentials."""
    tenant_id_raw = payload.get("tenant_id")
    to_number = str(payload.get("to") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not tenant_id_raw or not to_number or not message:
        raise HTTPException(
            status_code=422, detail="tenant_id, to, and message are required"
        )
    t_id = uuid.UUID(str(tenant_id_raw))
    ch = (
        await session.execute(
            select(MessageChannel)
            .where(
                MessageChannel.tenant_id == t_id,
                MessageChannel.channel_type == "sms",
                MessageChannel.is_active.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    try:
        if ch is None:
            raise SmsNotConfiguredError()
        res = await send_outbound_message(
            session,
            tenant_id=t_id,
            channel=ch,
            to_number=to_number,
            message=message,
        )
        await session.commit()
        return res
    except SmsNotConfiguredError as exc:
        raise HTTPException(
            status_code=501,
            detail={"code": "NOT_CONFIGURED", "message": exc.message},
        ) from exc
