"""Base messaging ``ChannelAdapter`` contract, Twilio SMS adapter, and TCPA/CTIA keyword handlers."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Callable, Mapping

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.schemas.messaging import (
    ChannelHealthStatus,
    InboundMessageEvent,
    KeywordDisposition,
    OutboundMessageResult,
)
from app.telephony import dnc

_E164_CLEAN = re.compile(r"[^\d+]")

STOP_KEYWORDS = frozenset({"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"})
START_KEYWORDS = frozenset({"START", "YES", "UNSTOP"})
HELP_KEYWORDS = frozenset({"HELP", "INFO"})


class ChannelAdapterError(Exception):
    """Base error for messaging channel adapters."""


class ChannelUnsupportedError(ChannelAdapterError):
    """Requested channel_type or provider pair is not implemented."""

    def __init__(self, message: str, *, channel_type: str = "", provider: str = "") -> None:
        super().__init__(message)
        self.channel_type = channel_type
        self.provider = provider


class ChannelNotConfiguredError(ChannelAdapterError):
    """Required provider credentials or sender configuration are missing."""

    def __init__(self, message: str, *, provider: str = "", channel_type: str = "", missing: list[str] | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.channel_type = channel_type
        self.missing = list(missing or [])


class ChannelSignatureError(ChannelAdapterError):
    """Inbound provider webhook failed cryptographic verification."""


class ChannelDeliveryError(ChannelAdapterError):
    """Upstream provider rejected or failed outbound delivery."""


def normalize_e164(address: str) -> str:
    raw = (address or "").strip()
    if raw.lower().startswith("whatsapp:"):
        raw = raw[9:].strip()
    cleaned = _E164_CLEAN.sub("", raw)
    if not cleaned:
        raise ValueError("Phone number is required.")
    if not cleaned.startswith("+"):
        if len(cleaned) == 10 and cleaned.isdigit():
            cleaned = f"+1{cleaned}"
        else:
            cleaned = f"+{cleaned}"
    digits = cleaned[1:]
    if not digits.isdigit() or not (7 <= len(digits) <= 15):
        raise ValueError(f"Invalid E.164 phone address: {address!r}")
    return cleaned


def classify_compliance_keyword(body: str) -> tuple[KeywordDisposition, str | None]:
    """Classify single-word TCPA/CTIA compliance keywords (STOP / START / HELP)."""
    token = (body or "").strip().upper()
    if token in STOP_KEYWORDS:
        return (
            KeywordDisposition.STOP,
            "You have been unsubscribed and will receive no further messages. Reply START to resubscribe.",
        )
    if token in START_KEYWORDS:
        return (
            KeywordDisposition.START,
            "You have been resubscribed to messages. Reply STOP at any time to opt out.",
        )
    if token in HELP_KEYWORDS:
        return (
            KeywordDisposition.HELP,
            "VoxDesk automated messaging assistant. Reply STOP to unsubscribe or START to resubscribe.",
        )
    return KeywordDisposition.NONE, None


async def apply_inbound_keyword_dnc(
    session: AsyncSession,
    *,
    tenant_id: Any,
    phone_e164: str,
    body: str,
    source: str = "inbound_keyword",
) -> tuple[KeywordDisposition, str | None]:
    """Apply STOP / START / HELP compliance state transitions against the tenant DNC list."""
    disposition, reply = classify_compliance_keyword(body)
    if disposition == KeywordDisposition.STOP and tenant_id is not None:
        await dnc.add_dnc(
            session,
            tenant_id=tenant_id,
            phone=phone_e164,
            reason="keyword_stop",
            source=source,
        )
    elif disposition == KeywordDisposition.START and tenant_id is not None:
        await dnc.remove_dnc(
            session,
            tenant_id=tenant_id,
            phone=phone_e164,
        )
    return disposition, reply


class ChannelAdapter(ABC):
    """Provider-specific messaging transport contract."""

    provider: str
    channel_type: str
    supports_templates: bool = False
    supports_media: bool = False
    webhook_signature_scheme: str = "hmac"

    def __init__(self, *, transport: Callable[..., Any] | None = None) -> None:
        self._transport = transport

    @abstractmethod
    def missing_credentials(self, channel_config: Mapping[str, Any] | None = None) -> list[str]:
        """Return names of missing credentials required to operate this channel."""

    def configured(self, channel_config: Mapping[str, Any] | None = None) -> bool:
        return len(self.missing_credentials(channel_config)) == 0

    def require_configured(self, channel_config: Mapping[str, Any] | None = None) -> None:
        missing = self.missing_credentials(channel_config)
        if missing:
            raise ChannelNotConfiguredError(
                f"{self.provider} {self.channel_type} is NOT_CONFIGURED (missing {', '.join(missing)}); "
                "no message or call was created.",
                provider=self.provider,
                channel_type=self.channel_type,
                missing=missing,
            )

    def health_check(self, channel_config: Mapping[str, Any] | None = None) -> ChannelHealthStatus:
        missing = self.missing_credentials(channel_config)
        ok = len(missing) == 0
        return ChannelHealthStatus(
            provider=self.provider,
            channel_type=self.channel_type,
            configured=ok,
            status="healthy" if ok else "NOT_CONFIGURED",
            reason=(
                f"{self.provider} {self.channel_type} credentials verified"
                if ok
                else f"Missing {', '.join(missing)}"
            ),
            missing_credentials=missing,
        )

    @abstractmethod
    async def send_message(
        self,
        *,
        to_number: str,
        from_number: str | None = None,
        body: str,
        media_urls: list[str] | None = None,
        template_sid: str | None = None,
        template_variables: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        channel_config: Mapping[str, Any] | None = None,
    ) -> OutboundMessageResult:
        """Send one outbound message via the provider's live API."""

    @abstractmethod
    async def verify_webhook(
        self,
        request: Request,
        *,
        raw_body: bytes | None = None,
        channel_config: Mapping[str, Any] | None = None,
    ) -> bool:
        """Verify provider webhook cryptographic signature."""

    @abstractmethod
    def parse_inbound(self, payload: Mapping[str, Any]) -> InboundMessageEvent:
        """Normalize an inbound provider webhook payload into ``InboundMessageEvent``."""


class TwilioSmsChannelAdapter(ChannelAdapter):
    """Twilio programmable SMS channel adapter."""

    provider = "twilio"
    channel_type = "sms"
    supports_templates = False
    supports_media = True
    webhook_signature_scheme = "twilio-hmac-sha1"

    def __init__(self, *, transport: Callable[..., Any] | None = None) -> None:
        self._transport = transport

    def _resolve_creds(self, channel_config: Mapping[str, Any] | None = None) -> tuple[str, str, str]:
        cfg = dict(channel_config or {})
        sid = str(cfg.get("account_sid") or settings.twilio_account_sid or "").strip()
        token = str(cfg.get("auth_token") or settings.twilio_auth_token or "").strip()
        sender = str(
            cfg.get("from_number")
            or cfg.get("phone_number")
            or getattr(settings, "twilio_phone_number", "")
            or ""
        ).strip()
        return sid, token, sender

    def missing_credentials(self, channel_config: Mapping[str, Any] | None = None) -> list[str]:
        sid, token, _ = self._resolve_creds(channel_config)
        missing: list[str] = []
        if not sid:
            missing.append("TWILIO_ACCOUNT_SID")
        if not token:
            missing.append("TWILIO_AUTH_TOKEN")
        return missing

    async def send_message(
        self,
        *,
        to_number: str,
        from_number: str | None = None,
        body: str,
        media_urls: list[str] | None = None,
        template_sid: str | None = None,
        template_variables: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        channel_config: Mapping[str, Any] | None = None,
    ) -> OutboundMessageResult:
        del template_sid, template_variables, metadata
        self.require_configured(channel_config)
        sid, token, cfg_sender = self._resolve_creds(channel_config)
        dest = normalize_e164(to_number)
        sender_raw = from_number or cfg_sender
        if not sender_raw:
            raise ChannelNotConfiguredError(
                "Twilio SMS sender phone number is NOT_CONFIGURED; no message or call was created.",
                provider=self.provider,
                channel_type=self.channel_type,
                missing=["from_number"],
            )
        sender = normalize_e164(sender_raw)
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        form: dict[str, Any] = {"To": dest, "From": sender, "Body": body}
        if media_urls:
            form["MediaUrl"] = media_urls[0]

        if self._transport is not None:
            status_code, resp_body = await self._transport("POST", url, auth=(sid, token), data=form)
        else:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, auth=(sid, token), data=form)
                status_code = resp.status_code
                resp_body = resp.json() if resp.content else {}

        if status_code >= 400:
            raise ChannelDeliveryError(f"Twilio SMS send failed with HTTP {status_code}")
        msg_id = str(resp_body.get("sid") or "").strip()
        if not msg_id:
            raise ChannelDeliveryError("Twilio SMS response contained no message SID.")
        return OutboundMessageResult(
            provider=self.provider,
            channel_type=self.channel_type,
            provider_message_id=msg_id,
            status=str(resp_body.get("status") or "queued"),
            to_address=dest,
            from_address=sender,
            raw_status=str(resp_body.get("status") or "queued"),
        )

    async def verify_webhook(
        self,
        request: Request,
        *,
        raw_body: bytes | None = None,
        channel_config: Mapping[str, Any] | None = None,
    ) -> bool:
        del raw_body
        _, token, _ = self._resolve_creds(channel_config)
        if not token:
            raise ChannelNotConfiguredError(
                "TWILIO_AUTH_TOKEN is NOT_CONFIGURED for webhook signature verification.",
                provider=self.provider,
                channel_type=self.channel_type,
                missing=["TWILIO_AUTH_TOKEN"],
            )
        signature = str(request.headers.get("X-Twilio-Signature") or "").strip()
        if not signature:
            return False
        from twilio.request_validator import RequestValidator

        form_data = await request.form()
        params = {str(k): str(v) for k, v in form_data.items()}
        return bool(RequestValidator(token).validate(str(request.url), params, signature))

    def parse_inbound(self, payload: Mapping[str, Any]) -> InboundMessageEvent:
        from_raw = str(payload.get("From") or payload.get("from") or "").strip()
        to_raw = str(payload.get("To") or payload.get("to") or "").strip()
        body = str(payload.get("Body") or payload.get("body") or "")
        msg_sid = str(payload.get("MessageSid") or payload.get("SmsSid") or payload.get("id") or "").strip()
        if not msg_sid:
            raise ValueError("Missing MessageSid in Twilio SMS webhook.")
        from_e164 = normalize_e164(from_raw)
        to_e164 = normalize_e164(to_raw)
        kw, _ = classify_compliance_keyword(body)
        media: list[str] = []
        num_media = int(payload.get("NumMedia") or 0)
        for idx in range(min(num_media, 10)):
            m_url = payload.get(f"MediaUrl{idx}")
            if m_url:
                media.append(str(m_url))
        return InboundMessageEvent(
            provider=self.provider,
            channel_type=self.channel_type,
            provider_message_id=msg_sid,
            from_address=from_e164,
            to_address=to_e164,
            from_e164=from_e164,
            to_e164=to_e164,
            body=body,
            media_urls=media,
            keyword=kw,
        )


async def resolve_tenant_for_destination(
    session: AsyncSession,
    *,
    to_e164: str,
    channel_type: str,
    provider: str,
) -> Any:
    """Find the owning tenant for an inbound message by ``MessageChannel`` or ``Tenant.twilio_number``."""
    from app.db.enterprise_models import MessageChannel
    from app.db.models import Tenant

    channels = list(
        (
            await session.scalars(
                select(MessageChannel).where(
                    MessageChannel.channel_type == channel_type,
                    MessageChannel.provider == provider,
                    MessageChannel.is_active.is_(True),
                )
            )
        ).all()
    )
    for ch in channels:
        cfg = ch.config if isinstance(ch.config, dict) else {}
        cand = str(cfg.get("from_number") or cfg.get("phone_number") or cfg.get("whatsapp_number") or "").strip()
        if cand:
            try:
                if normalize_e164(cand) == to_e164:
                    return ch.tenant_id
            except ValueError:
                continue
    tenant = await session.scalar(select(Tenant).where(Tenant.twilio_number == to_e164))
    if tenant is not None:
        return tenant.id
    return channels[0].tenant_id if len(channels) == 1 else None
