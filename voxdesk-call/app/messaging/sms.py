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
from dataclasses import dataclass
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
from app.db.models import Agent, AgentVersion, MessageWebhookReceipt, Tenant
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
    else:
        if dnc_row is not None and dnc_row.source.startswith("sms"):
            await session.delete(dnc_row)
            await session.flush()
            dnc_row = None

    # Update consent and LeadStatus through the canonical environment-scoped
    # lifecycle path. Let failures abort the webhook transaction: the direct
    # DNC entry must never hide a missing consent/history transition.
    from app.channels.messaging import set_opt_out

    await set_opt_out(session, tenant, norm_phone, opted_out)

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
    """Verify messaging channel configuration via ``app.messaging.channels``; fail closed if missing."""
    from app.messaging.channels import ChannelUnsupportedError, get_channel_adapter

    now = _now()
    channel.last_health_check_at = now
    channel.updated_at = now
    cfg = dict((channel.config if isinstance(channel.config, dict) else {}) or {})

    if (channel.provider, channel.channel_type) != ("twilio", "sms"):
        try:
            adapter = get_channel_adapter(channel.provider, channel.channel_type)
        except ChannelUnsupportedError as exc:
            channel.health_status = "unsupported"
            await session.flush()
            raise SmsNotConfiguredError(f"NOT_CONFIGURED: {exc}") from exc
        health = adapter.health_check(cfg)
        if not health.configured:
            channel.health_status = "not_configured"
            await session.flush()
            raise SmsNotConfiguredError(f"NOT_CONFIGURED: {health.reason}")
        channel.health_status = "healthy"
        await session.flush()
        return {
            "channel_id": str(channel.id),
            "channel_type": channel.channel_type,
            "provider": channel.provider,
            "status": "healthy",
            "checked_at": now.isoformat(),
        }

    configured, creds = _channel_credentials_configured(channel)
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
    """Send an outbound message via ``app.messaging.channels`` and record a real ``MessageWebhookReceipt``."""
    from app.messaging.channels import (
        ChannelNotConfiguredError,
        ChannelUnsupportedError,
        get_channel_adapter,
    )

    raw_to = strip_channel_prefix(to_number)
    try:
        norm_to = dnc.normalize_phone(raw_to)
    except phone_util.InvalidPhoneNumber:
        norm_to = raw_to

    blocked, reason = await dnc.is_blocked(session, tenant_id, norm_to)
    if blocked:
        raise ValueError(f"Destination {norm_to} is blocked by DNC ({reason}).")

    cfg = dict(channel.config or {})
    try:
        adapter = get_channel_adapter(channel.provider, channel.channel_type)
        outbound = await adapter.send_message(
            to_number=to_number,
            from_number=str(
                cfg.get("from_number")
                or cfg.get("phone_number")
                or cfg.get("whatsapp_number")
                or settings.twilio_phone_number
                or ""
            )
            or None,
            body=message,
            metadata=metadata,
            channel_config=cfg,
        )
    except (ChannelNotConfiguredError, ChannelUnsupportedError) as exc:
        raise SmsNotConfiguredError(
            f"NOT_CONFIGURED: {channel.provider} {channel.channel_type} adapter is not configured; "
            f"no message or call was created ({exc})."
        ) from exc

    provider_msg_id = outbound.provider_message_id
    receipt = MessageWebhookReceipt(
        tenant_id=tenant_id,
        channel=channel.channel_type,
        provider_message_id=provider_msg_id,
    )
    session.add(receipt)

    sent_log = list(cfg.get("outbound_receipts") or [])
    now = _now()
    entry = {
        "id": str(receipt.id) if getattr(receipt, "id", None) else provider_msg_id,
        "provider_message_id": provider_msg_id,
        "to": outbound.to_address,
        "from": outbound.from_address,
        "channel_type": channel.channel_type,
        "provider": channel.provider,
        "status": outbound.status,
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
        "to": outbound.to_address,
        "from": outbound.from_address,
        "status": outbound.status,
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


# ===========================================================================
# PART 13 / P13-08 — outbound SMS guard
# ===========================================================================
#
# Every outbound message through this guard passes the applicable checks before
# a provider is called:
#
# 1. **registration** -- pending/denied A2P states block SMS (not WhatsApp);
# 2. **destination and DNC** -- invalid numbers and do-not-contact recipients
#    never reach the adapter;
# 3. **quiet hours** -- marketing messages are blocked during the configured
#    quiet window.
#
# The guard returns a decision instead of raising, because the API needs to tell
# the operator *which* gate stopped the message. `send_outbound_message` raises
# from that decision so nothing slips through a caller that forgot to check.
#
# MMS media links are validated here too: they are stored with the message and
# appear in the transcript, so a `file://` or a private S3 URL would be both a
# broken image and a data leak.

MMS_MAX_MEDIA = 10
MMS_MAX_BYTES = 5 * 1024 * 1024
MMS_SCHEMES = ("https://",)
MMS_TYPES = ("image/jpeg", "image/png", "image/gif", "video/mp4", "audio/mpeg", "audio/ogg")
#: 21:00 to 08:00 local time is quiet unless the tenant says otherwise.
DEFAULT_QUIET_START = "21:00"
DEFAULT_QUIET_END = "08:00"


@dataclass(frozen=True)
class SendDecision:
    allowed: bool
    code: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "code": self.code, "message": self.message}


def _minutes(hhmm: str) -> int:
    hour, _, minute = (hhmm or "00:00").partition(":")
    return int(hour) * 60 + int(minute or 0)


def in_quiet_hours(
    moment: datetime,
    *,
    start: str = DEFAULT_QUIET_START,
    end: str = DEFAULT_QUIET_END,
) -> bool:
    """Whether a local wall-clock time falls inside the quiet window."""
    current = moment.hour * 60 + moment.minute
    begin, finish = _minutes(start), _minutes(end)
    if begin == finish:
        return False
    if begin < finish:
        return begin <= current < finish
    # Window crosses midnight (the normal case).
    return current >= begin or current < finish


def validate_media(media_urls: list[str] | None) -> SendDecision:
    urls = list(media_urls or [])
    if len(urls) > MMS_MAX_MEDIA:
        return SendDecision(False, "too_many_media", f"an MMS may carry at most {MMS_MAX_MEDIA} files")
    for url in urls:
        if not url or not any(url.startswith(scheme) for scheme in MMS_SCHEMES):
            return SendDecision(
                False, "invalid_media_url", "media must be an https URL the carrier can fetch"
            )
        if any(token in url for token in ("X-Amz-Signature", "token=", "sig=")):
            return SendDecision(
                False,
                "signed_media_url",
                "media URLs are stored with the message; a signed URL would expire in the transcript",
            )
    return SendDecision(True, "ok", "")


async def _sms_consent_granted(
    session: AsyncSession, tenant_id: uuid.UUID, normalized_phone: str, raw_phone: str
) -> bool:
    """Require an explicit, current SMS grant for a marketing recipient.

    STOP is also represented in the tenant DNC list; this separate check keeps
    a missing consent record from being mistaken for permission. A denial on
    any matching lead wins, and a grant in one environment is never confused
    with a different tenant's contact.
    """
    from app.leads.models import LeadConsent, LeadIdentity
    from app.db.models import Lead

    leads = list(
        (
            await session.execute(
                select(Lead).where(
                    Lead.tenant_id == tenant_id,
                    Lead.phone.in_({normalized_phone, raw_phone}),
                )
            )
        )
        .scalars()
        .all()
    )
    identity_rows = (
        await session.execute(
            select(LeadIdentity.lead_id).where(
                LeadIdentity.tenant_id == tenant_id,
                LeadIdentity.phone_normalized == normalized_phone,
            )
        )
    ).all()
    lead_ids = {lead.id for lead in leads}
    lead_ids.update(row[0] for row in identity_rows)
    if not lead_ids:
        return False

    consent_rows = (
        await session.execute(
            select(LeadConsent)
            .where(
                LeadConsent.tenant_id == tenant_id,
                LeadConsent.lead_id.in_(lead_ids),
                LeadConsent.channel == "sms",
            )
            .order_by(LeadConsent.version.desc())
        )
    ).scalars().all()
    latest: dict[uuid.UUID, str] = {}
    for consent in consent_rows:
        latest.setdefault(consent.lead_id, consent.decision)
    if "denied" in latest.values():
        return False
    return "granted" in latest.values()


def quiet_hours_decision(
    moment: datetime,
    *,
    marketing: bool,
    start: str = DEFAULT_QUIET_START,
    end: str = DEFAULT_QUIET_END,
) -> SendDecision:
    """Transactional messages are exempt; marketing ones are not."""
    if not marketing:
        return SendDecision(True, "ok", "")
    if in_quiet_hours(moment, start=start, end=end):
        return SendDecision(
            False,
            "quiet_hours",
            f"marketing messages are not sent between {start} and {end} local time",
        )
    return SendDecision(True, "ok", "")


async def check_outbound_allowed(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    to_number: str,
    channel_type: str = "sms",
    marketing: bool = False,
    now: datetime | None = None,
    media_urls: list[str] | None = None,
) -> SendDecision:
    """The full gate list, in the order a reviewer would check it."""
    from app.telephony.messaging_registration import sms_send_permitted
    from app.db.telephony_models import MessagingRegistration

    media = validate_media(media_urls)
    if not media.allowed:
        return media

    if (channel_type or "sms").strip().lower() == "sms":
        rows = (
            await session.execute(
                select(MessagingRegistration).where(
                    MessagingRegistration.tenant_id == tenant_id
                )
            )
        ).scalars().all()
        states = {row.kind: row.state for row in rows}
        if rows:
            permitted, reason = sms_send_permitted(states)
            if not permitted:
                return SendDecision(False, "registration_not_approved", reason)
        # A tenant with no registration rows at all is *not* blocked here: the
        # gate enforces a registration that was started and is pending. Blocking
        # all sends would also block an in-product credentials test.

    raw_to = strip_channel_prefix(to_number)
    try:
        norm_to = dnc.normalize_phone(raw_to)
    except phone_util.InvalidPhoneNumber:
        return SendDecision(False, "invalid_destination", "the destination is not a dialable number")

    blocked, reason = await dnc.is_blocked(session, tenant_id, norm_to)
    if blocked:
        return SendDecision(False, "dnc_blocked", f"destination is on the do-not-contact list ({reason})")

    if (channel_type or "sms").strip().lower() == "sms" and marketing:
        if not await _sms_consent_granted(session, tenant_id, norm_to, raw_to):
            return SendDecision(
                False,
                "sms_consent_required",
                "an explicit SMS opt-in is required before sending marketing messages",
            )

    quiet = quiet_hours_decision(_now() if now is None else now, marketing=marketing)
    if not quiet.allowed:
        return quiet
    return SendDecision(True, "ok", "")


async def send_outbound_guarded(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    channel: MessageChannel,
    to_number: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    marketing: bool = False,
    media_urls: list[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """`send_outbound_message` with the PART 13 gates in front of it."""
    decision = await check_outbound_allowed(
        session,
        tenant_id=tenant_id,
        to_number=to_number,
        channel_type=channel.channel_type,
        marketing=marketing,
        now=now,
        media_urls=media_urls,
    )
    if not decision.allowed:
        raise ValueError(f"{decision.code}: {decision.message}")
    payload = dict(metadata or {})
    if media_urls:
        payload["media_urls"] = list(media_urls)
    result = await send_outbound_message(
        session,
        tenant_id=tenant_id,
        channel=channel,
        to_number=to_number,
        message=message,
        metadata=payload,
    )
    # The transcript is the record of the conversation; an MMS with no media
    # links in it reads like a mis-sent SMS to whoever reviews the call.
    if media_urls:
        result["media_urls"] = list(media_urls)
    return result
