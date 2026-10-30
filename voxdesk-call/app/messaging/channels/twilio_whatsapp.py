"""Twilio WhatsApp Business channel adapter (Capability 11).

Formats ``whatsapp:+E164`` addresses, supports freeform session messages and
Content API templates (``ContentSid`` / ``ContentVariables``), and validates
``X-Twilio-Signature`` HMAC-SHA1 webhook signatures.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from fastapi import Request

from app.core.config import settings
from app.messaging.channels.base import (
    ChannelAdapter,
    ChannelDeliveryError,
    ChannelNotConfiguredError,
    classify_compliance_keyword,
    normalize_e164,
)
from app.schemas.messaging import InboundMessageEvent, OutboundMessageResult


def format_whatsapp_address(address: str) -> str:
    e164 = normalize_e164(address)
    return f"whatsapp:{e164}"


class TwilioWhatsAppAdapter(ChannelAdapter):
    """Twilio WhatsApp Business messaging adapter."""

    provider = "twilio"
    channel_type = "whatsapp"
    supports_templates = True
    supports_media = True
    webhook_signature_scheme = "twilio-hmac-sha1"

    def __init__(self, *, transport: Callable[..., Any] | None = None) -> None:
        self._transport = transport

    def _resolve_creds(self, channel_config: Mapping[str, Any] | None = None) -> tuple[str, str, str]:
        cfg = dict(channel_config or {})
        sid = str(cfg.get("account_sid") or settings.twilio_account_sid or "").strip()
        token = str(cfg.get("auth_token") or settings.twilio_auth_token or "").strip()
        wa_sender = str(
            cfg.get("whatsapp_number")
            or cfg.get("from_number")
            or settings.twilio_whatsapp_number
            or ""
        ).strip()
        return sid, token, wa_sender

    def missing_credentials(self, channel_config: Mapping[str, Any] | None = None) -> list[str]:
        sid, token, wa_sender = self._resolve_creds(channel_config)
        missing: list[str] = []
        if not sid:
            missing.append("TWILIO_ACCOUNT_SID")
        if not token:
            missing.append("TWILIO_AUTH_TOKEN")
        if not wa_sender:
            missing.append("TWILIO_WHATSAPP_NUMBER")
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
        del metadata
        self.require_configured(channel_config)
        sid, token, cfg_sender = self._resolve_creds(channel_config)
        to_wa = format_whatsapp_address(to_number)
        from_wa = format_whatsapp_address(from_number or cfg_sender)

        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        form: dict[str, Any] = {
            "To": to_wa,
            "From": from_wa,
        }
        if template_sid:
            form["ContentSid"] = template_sid
            if template_variables:
                form["ContentVariables"] = json.dumps(dict(template_variables), separators=(",", ":"))
        else:
            form["Body"] = body
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
            raise ChannelDeliveryError(f"Twilio WhatsApp send failed with HTTP {status_code}")
        msg_id = str(resp_body.get("sid") or "").strip()
        if not msg_id:
            raise ChannelDeliveryError("Twilio WhatsApp response contained no message SID.")
        return OutboundMessageResult(
            provider=self.provider,
            channel_type=self.channel_type,
            provider_message_id=msg_id,
            status=str(resp_body.get("status") or "queued"),
            to_address=to_wa,
            from_address=from_wa,
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
        cfg = dict(channel_config or {})
        token = str(cfg.get("auth_token") or settings.twilio_auth_token or "").strip()
        if not token:
            raise ChannelNotConfiguredError(
                "TWILIO_AUTH_TOKEN is NOT_CONFIGURED for WhatsApp webhook signature verification.",
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
            raise ValueError("Missing MessageSid in Twilio WhatsApp webhook.")
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
            from_address=format_whatsapp_address(from_e164),
            to_address=format_whatsapp_address(to_e164),
            from_e164=from_e164,
            to_e164=to_e164,
            body=body,
            media_urls=media,
            keyword=kw,
        )
