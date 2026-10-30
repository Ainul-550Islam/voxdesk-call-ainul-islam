"""Vonage Messages v1 SMS channel adapter with HMAC-JWT & HMAC-SHA256 webhook verification (Capability 11)."""
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Callable, Mapping

import jwt
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

VONAGE_MESSAGES_URL = "https://api.nexmo.com/v1/messages"


def verify_vonage_webhook_signature(
    *,
    signature_secret: str,
    authorization_header: str | None = None,
    raw_body: bytes = b"",
    params: Mapping[str, Any] | None = None,
) -> bool:
    """Verify Vonage Messages v1 Bearer HS256 JWT (with ``payload_hash``) or legacy ``sig`` HMAC-SHA256."""
    secret = (signature_secret or "").strip()
    if not secret:
        return False

    auth = (authorization_header or "").strip()
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        try:
            claims = jwt.decode(
                token,
                secret,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        except jwt.PyJWTError:
            return False
        expected_hash = claims.get("payload_hash")
        if expected_hash is not None:
            actual_hash = hashlib.sha256(raw_body).hexdigest()
            if not hmac.compare_digest(str(expected_hash).lower(), actual_hash.lower()):
                return False
        return True

    if params and params.get("sig"):
        provided_sig = str(params["sig"]).strip().lower()
        canonical_parts: list[str] = []
        for key in sorted(params.keys()):
            if key == "sig":
                continue
            val = str(params[key]).replace("&", "_").replace("=", "_")
            canonical_parts.append(f"&{key}={val}")
        signing_str = "".join(canonical_parts).encode("utf-8")
        hmac_hex = hmac.new(secret.encode("utf-8"), signing_str, hashlib.sha256).hexdigest().lower()
        return hmac.compare_digest(provided_sig, hmac_hex)

    return False


class VonageSmsAdapter(ChannelAdapter):
    """Vonage Messages API v1 SMS channel adapter."""

    provider = "vonage"
    channel_type = "sms"
    supports_templates = False
    supports_media = False
    webhook_signature_scheme = "vonage-jwt-hs256-or-hmac"

    def __init__(self, *, transport: Callable[..., Any] | None = None) -> None:
        self._transport = transport

    def _resolve_creds(
        self, channel_config: Mapping[str, Any] | None = None
    ) -> tuple[str, str, str, str]:
        cfg = dict(channel_config or {})
        api_key = str(cfg.get("api_key") or settings.vonage_api_key or "").strip()
        api_secret = str(cfg.get("api_secret") or settings.vonage_api_secret or "").strip()
        sig_secret = str(
            cfg.get("signature_secret") or settings.vonage_signature_secret or ""
        ).strip()
        from_num = str(
            cfg.get("from_number")
            or getattr(settings, "vonage_from_number", "")
            or ""
        ).strip()
        return api_key, api_secret, sig_secret, from_num

    def missing_credentials(self, channel_config: Mapping[str, Any] | None = None) -> list[str]:
        api_key, api_secret, _, _ = self._resolve_creds(channel_config)
        missing: list[str] = []
        if not api_key:
            missing.append("VONAGE_API_KEY")
        if not api_secret:
            missing.append("VONAGE_API_SECRET")
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
        del media_urls, template_sid, template_variables, metadata
        self.require_configured(channel_config)
        api_key, api_secret, _, cfg_sender = self._resolve_creds(channel_config)
        sender_raw = (from_number or cfg_sender).strip()
        if not sender_raw:
            raise ChannelNotConfiguredError(
                "Vonage SMS from_number is NOT_CONFIGURED; no message or call was created.",
                provider=self.provider,
                channel_type=self.channel_type,
                missing=["VONAGE_FROM_NUMBER"],
            )
        dest = normalize_e164(to_number)
        sender = normalize_e164(sender_raw)

        payload = {
            "channel": "sms",
            "message_type": "text",
            "to": dest.lstrip("+"),
            "from": sender.lstrip("+"),
            "text": body,
        }
        if self._transport is not None:
            status_code, resp_body = await self._transport(
                "POST",
                VONAGE_MESSAGES_URL,
                auth=(api_key, api_secret),
                json=payload,
            )
        else:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    VONAGE_MESSAGES_URL,
                    auth=(api_key, api_secret),
                    json=payload,
                    headers={"Accept": "application/json"},
                )
                status_code = resp.status_code
                resp_body = resp.json() if resp.content else {}

        if status_code >= 400:
            raise ChannelDeliveryError(f"Vonage SMS send failed with HTTP {status_code}")

        msg_uuid = str(
            resp_body.get("message_uuid")
            or resp_body.get("message-id")
            or (
                resp_body["messages"][0].get("message-id")
                if isinstance(resp_body.get("messages"), list) and resp_body["messages"]
                else ""
            )
        ).strip()
        if not msg_uuid:
            raise ChannelDeliveryError("Vonage SMS response contained no message_uuid.")
        return OutboundMessageResult(
            provider=self.provider,
            channel_type=self.channel_type,
            provider_message_id=msg_uuid,
            status="submitted",
            to_address=dest,
            from_address=sender,
            raw_status="submitted",
        )

    async def verify_webhook(
        self,
        request: Request,
        *,
        raw_body: bytes | None = None,
        channel_config: Mapping[str, Any] | None = None,
    ) -> bool:
        _, _, sig_secret, _ = self._resolve_creds(channel_config)
        if not sig_secret:
            raise ChannelNotConfiguredError(
                "VONAGE_SIGNATURE_SECRET is NOT_CONFIGURED for webhook verification.",
                provider=self.provider,
                channel_type=self.channel_type,
                missing=["VONAGE_SIGNATURE_SECRET"],
            )
        body_bytes = raw_body if raw_body is not None else await request.body()
        auth_header = request.headers.get("Authorization")
        query_params: dict[str, Any] = dict(request.query_params)
        if not auth_header and body_bytes:
            import json

            try:
                parsed = json.loads(body_bytes.decode("utf-8"))
                if isinstance(parsed, dict):
                    query_params.update(parsed)
            except ValueError:
                pass
        return verify_vonage_webhook_signature(
            signature_secret=sig_secret,
            authorization_header=auth_header,
            raw_body=body_bytes,
            params=query_params,
        )

    def parse_inbound(self, payload: Mapping[str, Any]) -> InboundMessageEvent:
        msg_id = str(
            payload.get("message_uuid")
            or payload.get("messageId")
            or payload.get("message-id")
            or ""
        ).strip()
        if not msg_id:
            raise ValueError("Missing message_uuid in Vonage inbound webhook.")
        from_raw = str(payload.get("from") or payload.get("msisdn") or "").strip()
        to_raw = str(payload.get("to") or "").strip()
        body = str(payload.get("text") or payload.get("body") or "")
        from_e164 = normalize_e164(from_raw)
        to_e164 = normalize_e164(to_raw)
        kw, _ = classify_compliance_keyword(body)
        return InboundMessageEvent(
            provider=self.provider,
            channel_type=self.channel_type,
            provider_message_id=msg_id,
            from_address=from_e164,
            to_address=to_e164,
            from_e164=from_e164,
            to_e164=to_e164,
            body=body,
            keyword=kw,
        )
