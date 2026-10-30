"""Telnyx v2 Messaging channel adapter with Ed25519 webhook verification (Capability 11)."""
from __future__ import annotations

import base64
import time
from typing import Any, Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
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

TELNYX_MESSAGES_URL = "https://api.telnyx.com/v2/messages"
MAX_WEBHOOK_DRIFT_SECONDS = 300


def _decode_public_key_bytes(raw_key: str) -> bytes:
    cleaned = raw_key.strip()
    try:
        decoded = base64.b64decode(cleaned, validate=True)
        if len(decoded) == 32:
            return decoded
    except Exception:
        pass
    try:
        decoded_hex = bytes.fromhex(cleaned)
        if len(decoded_hex) == 32:
            return decoded_hex
    except ValueError:
        pass
    raise ValueError("Telnyx Ed25519 public key must be 32 bytes (base64 or hex).")


def verify_telnyx_ed25519_signature(
    *,
    public_key: str,
    signature_b64: str,
    timestamp_str: str,
    raw_body: bytes,
    max_drift_seconds: int = MAX_WEBHOOK_DRIFT_SECONDS,
) -> bool:
    if not signature_b64 or not timestamp_str:
        return False
    try:
        ts_val = int(timestamp_str.strip())
    except ValueError:
        return False
    if max_drift_seconds > 0 and abs(int(time.time()) - ts_val) > max_drift_seconds:
        return False
    try:
        pub_bytes = _decode_public_key_bytes(public_key)
        sig_bytes = base64.b64decode(signature_b64.strip(), validate=True)
        key = Ed25519PublicKey.from_public_bytes(pub_bytes)
        signed_payload = f"{timestamp_str.strip()}|".encode("utf-8") + raw_body
        key.verify(sig_bytes, signed_payload)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


class TelnyxMessagingAdapter(ChannelAdapter):
    """Telnyx Programmable Messaging v2 SMS/MMS adapter."""

    provider = "telnyx"
    channel_type = "sms"
    supports_templates = False
    supports_media = True
    webhook_signature_scheme = "telnyx-ed25519"

    def __init__(self, *, transport: Callable[..., Any] | None = None) -> None:
        self._transport = transport

    def _resolve_creds(
        self, channel_config: Mapping[str, Any] | None = None
    ) -> tuple[str, str, str, str]:
        cfg = dict(channel_config or {})
        api_key = str(cfg.get("api_key") or settings.telnyx_api_key or "").strip()
        pub_key = str(cfg.get("public_key") or settings.telnyx_public_key or "").strip()
        profile_id = str(
            cfg.get("messaging_profile_id")
            or getattr(settings, "telnyx_messaging_profile_id", "")
            or ""
        ).strip()
        from_num = str(
            cfg.get("from_number")
            or getattr(settings, "telnyx_from_number", "")
            or ""
        ).strip()
        return api_key, pub_key, profile_id, from_num

    def missing_credentials(self, channel_config: Mapping[str, Any] | None = None) -> list[str]:
        api_key, _, profile_id, from_num = self._resolve_creds(channel_config)
        missing: list[str] = []
        if not api_key:
            missing.append("TELNYX_API_KEY")
        if not profile_id and not from_num:
            missing.append("TELNYX_MESSAGING_PROFILE_ID_OR_FROM_NUMBER")
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
        api_key, _, profile_id, cfg_sender = self._resolve_creds(channel_config)
        if not api_key:
            self.require_configured(channel_config)
        effective_sender = (from_number or cfg_sender).strip()
        if not effective_sender and not profile_id:
            self.require_configured(channel_config)

        dest = normalize_e164(to_number)
        sender = normalize_e164(effective_sender) if effective_sender else ""
        payload: dict[str, Any] = {
            "to": dest,
            "text": body,
        }
        if sender:
            payload["from"] = sender
        if profile_id:
            payload["messaging_profile_id"] = profile_id
        if media_urls:
            payload["media_urls"] = list(media_urls)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._transport is not None:
            status_code, resp_body = await self._transport(
                "POST", TELNYX_MESSAGES_URL, headers=headers, json=payload
            )
        else:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(TELNYX_MESSAGES_URL, headers=headers, json=payload)
                status_code = resp.status_code
                resp_body = resp.json() if resp.content else {}

        if status_code >= 400:
            raise ChannelDeliveryError(f"Telnyx messaging send failed with HTTP {status_code}")

        data = resp_body.get("data") if isinstance(resp_body, dict) else {}
        if not isinstance(data, dict):
            data = {}
        msg_id = str(data.get("id") or resp_body.get("id") or "").strip()
        if not msg_id:
            raise ChannelDeliveryError("Telnyx messaging response contained no message id.")
        to_entries = data.get("to")
        raw_status = "queued"
        if isinstance(to_entries, list) and to_entries and isinstance(to_entries[0], dict):
            raw_status = str(to_entries[0].get("status") or "queued")
        return OutboundMessageResult(
            provider=self.provider,
            channel_type=self.channel_type,
            provider_message_id=msg_id,
            status=raw_status,
            to_address=dest,
            from_address=sender or profile_id,
            raw_status=raw_status,
        )

    async def verify_webhook(
        self,
        request: Request,
        *,
        raw_body: bytes | None = None,
        channel_config: Mapping[str, Any] | None = None,
    ) -> bool:
        _, pub_key, _, _ = self._resolve_creds(channel_config)
        if not pub_key:
            raise ChannelNotConfiguredError(
                "TELNYX_PUBLIC_KEY is NOT_CONFIGURED for Ed25519 webhook verification.",
                provider=self.provider,
                channel_type=self.channel_type,
                missing=["TELNYX_PUBLIC_KEY"],
            )
        sig = str(request.headers.get("telnyx-signature-ed25519") or "").strip()
        ts = str(request.headers.get("telnyx-timestamp") or "").strip()
        body_bytes = raw_body if raw_body is not None else await request.body()
        return verify_telnyx_ed25519_signature(
            public_key=pub_key,
            signature_b64=sig,
            timestamp_str=ts,
            raw_body=body_bytes,
        )

    def parse_inbound(self, payload: Mapping[str, Any]) -> InboundMessageEvent:
        raw_data = payload.get("data")
        data: Mapping[str, Any] = raw_data if isinstance(raw_data, Mapping) else payload
        event_type = str(data.get("event_type") or "message.received")
        raw_inner = data.get("payload")
        inner: Mapping[str, Any] = raw_inner if isinstance(raw_inner, Mapping) else data

        msg_id = str(inner.get("id") or data.get("id") or "").strip()
        if not msg_id:
            raise ValueError("Missing message id in Telnyx inbound webhook.")

        from_field = inner.get("from")
        if isinstance(from_field, Mapping):
            from_raw = str(from_field.get("phone_number") or "")
        else:
            from_raw = str(from_field or "")

        to_field = inner.get("to")
        if isinstance(to_field, list) and to_field:
            first_to = to_field[0]
            to_raw = (
                str(first_to.get("phone_number") or "")
                if isinstance(first_to, Mapping)
                else str(first_to)
            )
        elif isinstance(to_field, Mapping):
            to_raw = str(to_field.get("phone_number") or "")
        else:
            to_raw = str(to_field or "")

        body = str(inner.get("text") or inner.get("body") or "")
        from_e164 = normalize_e164(from_raw)
        to_e164 = normalize_e164(to_raw)
        kw, _ = classify_compliance_keyword(body)

        media: list[str] = []
        for item in inner.get("media") or []:
            if isinstance(item, Mapping) and item.get("url"):
                media.append(str(item["url"]))

        return InboundMessageEvent(
            provider=self.provider,
            channel_type=self.channel_type,
            provider_message_id=msg_id,
            from_address=from_e164,
            to_address=to_e164,
            from_e164=from_e164,
            to_e164=to_e164,
            body=body,
            media_urls=media,
            keyword=kw,
            raw_event_type=event_type,
        )
