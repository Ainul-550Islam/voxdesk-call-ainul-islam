"""SIP outbound call origination adapter backed by the SIP gateway control API (Capability 7).

Wraps ``POST /v1/calls`` on the Kamailio + RTPEngine SIP media gateway with:
- Bearer token authentication + HMAC-SHA256 request signing (``X-VoxDesk-Signature``),
- ``Idempotency-Key`` propagation so gateway retries never place duplicate INVITEs,
- bounded exponential retry on 5xx / network timeouts and zero retry on 4xx client errors,
- clean ``ProviderConfigurationError`` (``NOT_CONFIGURED``) when gateway URL or token is missing.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from typing import Any, Callable

from app.carriers_v2.settings import get_carrier_v2_settings
from app.telephony.capabilities import CapabilitySet
from app.telephony.phone import normalize
from app.telephony.provider_errors import (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderConfigurationError,
    ProviderConflictError,
    ProviderNotFoundError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
    UnsupportedCapability,
)
from app.telephony.providers.base import (
    CallResult,
    HealthResult,
    NumberResult,
    RecordingResult,
    TelephonyAdapter,
    WebhookVerdict,
)

_DEFAULT_BACKOFF_SECONDS = (0.0, 0.2, 0.6)
_MAX_ATTEMPTS = 3


def _map_gateway_error(status: int, body: dict[str, Any]) -> Exception:
    detail = str(body.get("error") or body.get("message") or "").strip()
    message = f"SIP gateway answered HTTP {status}"
    if detail:
        message = f"{message}: {detail[:160]}"
    if status == 401:
        return ProviderAuthenticationError(message, provider="sip")
    if status == 403:
        return ProviderAuthorizationError(message, provider="sip")
    if status == 404:
        return ProviderNotFoundError(message, provider="sip")
    if status == 409:
        return ProviderConflictError(message, provider="sip")
    if status == 429:
        return ProviderRateLimitedError(message, provider="sip")
    if 400 <= status < 500:
        return ProviderValidationError(message, provider="sip")
    return ProviderUnavailableError(message, provider="sip")


class SipOutboundAdapter(TelephonyAdapter):
    """Real SIP trunk outbound call adapter talking to the SIP gateway ``/v1/calls`` API."""

    name = "sip"
    supports_takeover = True

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        hmac_secret: str | None = None,
        timeout_seconds: float | None = None,
        backoff_seconds: tuple[float, ...] = _DEFAULT_BACKOFF_SECONDS,
        transport: Callable[..., Any] | None = None,
    ) -> None:
        carrier = get_carrier_v2_settings()
        self.base_url = (
            base_url if base_url is not None else (carrier.sip_gateway_url or "")
        ).strip().rstrip("/")
        self.token = (
            token if token is not None else (carrier.sip_gateway_token or "")
        ).strip()
        self.hmac_secret = (
            hmac_secret if hmac_secret is not None else self.token
        ).strip()
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else (carrier.sip_gateway_timeout_seconds or 5.0)
        )
        self._backoff_seconds = backoff_seconds
        self._transport = transport

    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    def require_configured(self) -> None:
        if not self.configured():
            missing = []
            if not self.base_url:
                missing.append("sip_gateway_url")
            if not self.token:
                missing.append("sip_gateway_token")
            raise ProviderConfigurationError(
                f"SIP outbound gateway is NOT_CONFIGURED (missing {', '.join(missing)})",
                provider=self.name,
            )

    def health(self) -> HealthResult:
        if not self.configured():
            return HealthResult(
                provider=self.name,
                ok=False,
                reason="NOT_CONFIGURED: sip_gateway_url and sip_gateway_token are required",
                probed=False,
            )
        return HealthResult(
            provider=self.name,
            ok=True,
            reason="SIP gateway credentials present",
            probed=False,
        )

    def capabilities(self) -> CapabilitySet:
        ready = self.configured()
        return CapabilitySet(
            voice=ready,
            sms=False,
            mms=False,
            recording=False,
            source="provider_config" if ready else "unconfirmed",
        )

    def _build_signed_headers(
        self,
        *,
        method: str,
        path: str,
        body_json: str,
        idempotency_key: str = "",
    ) -> dict[str, str]:
        timestamp = str(int(time.time()))
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-VoxDesk-Timestamp": timestamp,
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if self.hmac_secret:
            signing_input = f"{timestamp}.{method.upper()}.{path}.{body_json}".encode("utf-8")
            digest = hmac.new(
                self.hmac_secret.encode("utf-8"),
                signing_input,
                hashlib.sha256,
            ).hexdigest()
            headers["X-VoxDesk-Signature"] = f"sha256={digest}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        self.require_configured()
        url = f"{self.base_url}{path}"
        body_dict = payload if payload is not None else {}
        body_json = (
            json.dumps(body_dict, separators=(",", ":"), sort_keys=True)
            if payload is not None
            else ""
        )
        headers = self._build_signed_headers(
            method=method,
            path=path,
            body_json=body_json,
            idempotency_key=idempotency_key,
        )

        last_exc: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                status_code, parsed = await self._send(method, url, headers, payload)
            except (ProviderTimeoutError, ProviderUnavailableError) as exc:
                last_exc = exc
                if attempt + 1 >= _MAX_ATTEMPTS:
                    raise
                delay = self._backoff_seconds[min(attempt, len(self._backoff_seconds) - 1)]
                if delay > 0:
                    await asyncio.sleep(delay)
                continue

            if status_code >= 500:
                last_exc = _map_gateway_error(status_code, parsed)
                if attempt + 1 < _MAX_ATTEMPTS:
                    delay = self._backoff_seconds[min(attempt, len(self._backoff_seconds) - 1)]
                    if delay > 0:
                        await asyncio.sleep(delay)
                    continue
                raise last_exc

            if status_code < 200 or status_code >= 300:
                # 4xx errors are deterministic caller/configuration errors; never retry.
                raise _map_gateway_error(status_code, parsed)

            return parsed

        raise ProviderUnavailableError(
            "SIP gateway unavailable after retries", provider=self.name
        ) from last_exc

    async def _send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        if self._transport is not None:
            return await self._transport(method, url, headers=headers, json=payload)

        import httpx

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                if method.upper() == "GET":
                    resp = await client.get(url, headers=headers)
                else:
                    resp = await client.request(
                        method.upper(), url, headers=headers, json=payload or {}
                    )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("SIP gateway request timed out", provider=self.name) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("SIP gateway transport failed", provider=self.name) from exc

        try:
            data = resp.json()
        except ValueError:
            data = {}
        return resp.status_code, data if isinstance(data, dict) else {}

    async def create_outbound(
        self,
        *,
        to_number: str,
        from_number: str,
        answer_url: str = "",
        idempotency_key: str = "",
        headers: dict[str, str] | None = None,
        codec: str = "PCMU",
    ) -> CallResult:
        self.require_configured()
        dest = normalize(to_number)
        caller = normalize(from_number)
        effective_idem = (idempotency_key or "").strip() or f"sip-out-{uuid.uuid4().hex}"
        payload: dict[str, Any] = {
            "to": dest,
            "from": caller,
            "codec": codec,
        }
        if answer_url:
            payload["media_ws_url"] = answer_url
        if headers:
            payload["sip_headers"] = dict(headers)

        body = await self._request(
            "POST",
            "/v1/calls",
            payload,
            idempotency_key=effective_idem,
        )
        external_id = str(body.get("id") or body.get("call_id") or "").strip()
        if not external_id:
            raise ProviderUnavailableError(
                "SIP gateway did not return a confirmed call identifier",
                provider=self.name,
            )
        return CallResult(
            provider=self.name,
            external_id=external_id,
            status=str(body.get("status") or "dialing"),
            to_number=dest,
            from_number=caller,
        )

    async def hangup(self, external_id: str) -> CallResult:
        self.require_configured()
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        body = await self._request(
            "POST",
            f"/v1/calls/{external_id}/hangup",
            {"reason": "normal_clearing"},
            idempotency_key=f"hangup:{external_id}",
        )
        return CallResult(
            provider=self.name,
            external_id=external_id,
            status=str(body.get("status") or "completed"),
        )

    async def transfer(self, external_id: str, destination: str) -> CallResult:
        self.require_configured()
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        target = normalize(destination)
        body = await self._request(
            "POST",
            f"/v1/calls/{external_id}/transfer",
            {"destination": target, "mode": "blind"},
            idempotency_key=f"transfer:{external_id}:{target}",
        )
        return CallResult(
            provider=self.name,
            external_id=external_id,
            status=str(body.get("status") or "transfer_requested"),
            to_number=target,
        )

    async def send_dtmf(self, external_id: str, digits: str) -> dict[str, Any]:
        self.require_configured()
        if not external_id or not digits:
            raise ProviderValidationError("Call id and digits are required", provider=self.name)
        body = await self._request(
            "POST",
            f"/v1/calls/{external_id}/dtmf",
            {"digits": digits},
            idempotency_key=f"dtmf:{external_id}:{digits}",
        )
        return {
            "provider": self.name,
            "external_id": external_id,
            "digits": digits,
            "status": str(body.get("status") or "sent"),
        }

    async def search_numbers(self, *, country: str, limit: int = 10) -> list[NumberResult]:
        del country, limit
        raise UnsupportedCapability(
            "SIP gateways use BYOC trunk numbers rather than carrier inventory search.",
            provider=self.name,
        )

    async def provision_number(self, e164: str, *, country: str = "") -> NumberResult:
        del e164, country
        raise UnsupportedCapability(
            "SIP gateways attach existing trunk DIDs rather than purchasing numbers.",
            provider=self.name,
        )

    async def release_number(self, external_id: str) -> NumberResult:
        del external_id
        raise UnsupportedCapability(
            "SIP gateways do not release carrier-owned DIDs.",
            provider=self.name,
        )

    async def start_recording(self, external_call_id: str) -> RecordingResult:
        del external_call_id
        raise UnsupportedCapability(
            "SIP trunk recording is managed by RTPEngine media policy.",
            provider=self.name,
        )

    async def stop_recording(
        self, external_call_id: str, recording_id: str = ""
    ) -> RecordingResult:
        del external_call_id, recording_id
        raise UnsupportedCapability(
            "SIP trunk recording is managed by RTPEngine media policy.",
            provider=self.name,
        )

    async def verify_webhook(self, request: Any) -> WebhookVerdict:
        if not self.hmac_secret:
            return WebhookVerdict(valid=False, reason="NOT_CONFIGURED")
        sig_header = ""
        ts_header = ""
        if hasattr(request, "headers"):
            sig_header = str(request.headers.get("X-VoxDesk-Signature") or "")
            ts_header = str(request.headers.get("X-VoxDesk-Timestamp") or "")
        if not sig_header.startswith("sha256=") or not ts_header:
            return WebhookVerdict(valid=False, reason="missing_signature")
        raw_body = await request.body() if hasattr(request, "body") else b""
        path = getattr(getattr(request, "url", None), "path", "/v1/events")
        method = str(getattr(request, "method", "POST")).upper()
        signing_input = f"{ts_header}.{method}.{path}.".encode("utf-8") + raw_body
        expected = "sha256=" + hmac.new(
            self.hmac_secret.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).hexdigest()
        ok = hmac.compare_digest(sig_header, expected)
        return WebhookVerdict(valid=ok, reason="verified" if ok else "signature_mismatch")


__all__ = ["SipOutboundAdapter"]
