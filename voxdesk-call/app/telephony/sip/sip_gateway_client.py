"""Async client for the VoxDesk SIP gateway control API.

The gateway (``services/sip-gateway``) owns the SIP signalling and the RTP
media. The application talks to it over a small HTTP control API:

    POST /v1/calls                 originate an outbound call
    POST /v1/calls/{id}/hangup     end a call
    POST /v1/calls/{id}/transfer   transfer (blind or attended, SIP REFER)
    POST /v1/calls/{id}/dtmf       send DTMF (RFC 2833)
    GET  /v1/calls/{id}            call status
    GET  /healthz, GET /readyz      liveness and readiness

Every request carries the shared gateway token as a bearer credential and
may carry an ``Idempotency-Key``. The current Go API does not deduplicate that
header, so mutating requests are never retried automatically: a timeout or 5xx
can leave the caller uncertain whether the operation completed, and replaying
an INVITE, DTMF, or transfer could repeat side effects. Only safe read requests
are retried with backoff.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.telephony.provider_errors import (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderConflictError,
    ProviderNotFoundError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
)

#: Retry budget for the gateway control API.
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.2, 0.5)


@dataclass(frozen=True)
class GatewayCall:
    """One call as the gateway reports it."""

    id: str
    status: str = "unknown"
    to_number: str = ""
    from_number: str = ""
    codec: str = ""
    detail: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "to_number": self.to_number,
            "from_number": self.from_number,
            "codec": self.codec,
            "detail": dict(self.detail or {}),
        }


class SipGatewayClient:
    """HTTP client for the SIP gateway control API."""

    # The ID-only call contract exists, but the current Go media/signalling
    # stack cannot satisfy tenant trunks' TLS+SRTP requirement. Keep this false
    # until secure transport is implemented and verified end to end.
    supports_tenant_trunks = False

    def __init__(
        self,
        *,
        base_url: str = "",
        token: str = "",
        timeout_seconds: float = 5.0,
        transport=None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.token = (token or "").strip()
        self.timeout_seconds = float(timeout_seconds or 5.0)
        self._transport = transport

    # ------------------------------------------------------------------
    def _require_configured(self) -> None:
        if not self.base_url:
            raise ProviderValidationError(
                "The SIP gateway URL is not configured", provider="sip"
            )
        if not self.token:
            raise ProviderValidationError(
                "The SIP gateway token is not configured", provider="sip"
            )

    def _headers(self, idempotency_key: str = "") -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key[:128]
        return headers

    # ------------------------------------------------------------------
    async def originate(
        self,
        *,
        to_number: str,
        from_number: str,
        call_id: str = "",
        codec: str = "PCMU",
        idempotency_key: str = "",
        tenant_id: str = "",
        trunk_id: str = "",
        environment_id: str | None = None,
    ) -> GatewayCall:
        """Originate an outbound call through the gateway."""
        self._require_configured()
        if not to_number or not from_number:
            raise ProviderValidationError(
                "Both a source and a destination number are required", provider="sip"
            )
        if bool(tenant_id) != bool(trunk_id):
            raise ProviderValidationError(
                "Tenant and SIP trunk identifiers must be supplied together", provider="sip"
            )
        if tenant_id and not self.supports_tenant_trunks:
            raise ProviderValidationError(
                "Tenant-scoped SIP trunk origination is disabled until the gateway provides verified TLS/SRTP support",
                provider="sip",
            )
        payload = {
            "to": to_number,
            "from": from_number,
            "codec": codec,
        }
        if call_id:
            payload["call_id"] = call_id
        if tenant_id:
            payload["tenant_id"] = tenant_id
            payload["trunk_id"] = trunk_id
            if environment_id:
                payload["environment_id"] = environment_id
        body = await self._request(
            "POST", "/v1/calls", payload, idempotency_key=idempotency_key
        )
        return _to_call(body)

    async def hangup(self, call_id: str, *, reason: str = "") -> dict[str, Any]:
        """End a live call."""
        self._require_configured()
        if not call_id:
            raise ProviderValidationError("Call id is required", provider="sip")
        payload = {"reason": reason} if reason else {}
        return await self._request("POST", f"/v1/calls/{call_id}/hangup", payload)

    async def transfer(
        self,
        call_id: str,
        destination: str,
        *,
        mode: str = "blind",
        attended_call_id: str = "",
    ) -> dict[str, Any]:
        """Transfer a live call (blind or attended, sent as SIP REFER)."""
        self._require_configured()
        if not call_id:
            raise ProviderValidationError("Call id is required", provider="sip")
        if not destination:
            raise ProviderValidationError(
                "A transfer destination is required", provider="sip"
            )
        if mode not in {"blind", "attended"}:
            raise ProviderValidationError(
                "Transfer mode must be 'blind' or 'attended'", provider="sip"
            )
        payload = {
            "destination": destination,
            "mode": mode,
        }
        if attended_call_id:
            payload["attended_call_id"] = attended_call_id
        return await self._request(
            "POST",
            f"/v1/calls/{call_id}/transfer",
            payload,
            idempotency_key=f"transfer:{call_id}:{destination}",
        )

    async def send_dtmf(self, call_id: str, digits: str) -> dict[str, Any]:
        """Send RFC 2833 DTMF tones into a live call."""
        self._require_configured()
        if not call_id:
            raise ProviderValidationError("Call id is required", provider="sip")
        if not digits or not digits.isdigit():
            raise ProviderValidationError(
                "DTMF digits must be numeric for the SIP gateway", provider="sip"
            )
        return await self._request(
            "POST",
            f"/v1/calls/{call_id}/dtmf",
            {"digits": digits},
            idempotency_key=f"dtmf:{call_id}:{digits}",
        )

    async def call_status(self, call_id: str) -> GatewayCall:
        """Read the state of one call."""
        self._require_configured()
        if not call_id:
            raise ProviderValidationError("Call id is required", provider="sip")
        body = await self._request("GET", f"/v1/calls/{call_id}", None)
        return _to_call(body)

    async def capture(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Capture RTP/RTCP packets on one active, tenant-scoped gateway call."""
        self._require_configured()
        if not isinstance(payload, dict):
            raise ProviderValidationError("Capture options must be an object", provider="sip")
        if str(payload.get("filter") or "").strip():
            raise ProviderValidationError(
                "PCAP capture filters are unsupported by the SIP gateway", provider="sip"
            )
        call_id = str(payload.get("call_id") or "").strip()
        tenant_id = str(payload.get("tenant_id") or "").strip()
        seconds = int(payload.get("duration_seconds") or 0)
        if not call_id or not tenant_id:
            raise ProviderValidationError(
                "A tenant id and call id are required for capture", provider="sip"
            )
        if not 1 <= seconds <= 300:
            raise ProviderValidationError(
                "Capture duration must be between 1 and 300 seconds", provider="sip"
            )
        timeout = max(self.timeout_seconds, float(seconds) + 15.0)
        return await self._request(
            "POST",
            f"/v1/calls/{quote(call_id, safe='')}/pcap",
            payload,
            timeout_seconds=timeout,
        )

    async def ready(self) -> dict[str, Any]:
        """Read the gateway readiness probe."""
        self._require_configured()
        return await self._request("GET", "/readyz", None, allow_status={200, 503})

    async def health(self) -> dict[str, Any]:
        """Read the gateway liveness probe."""
        self._require_configured()
        return await self._request("GET", "/healthz", None, allow_status={200, 503})

    # ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        payload: dict | None,
        *,
        idempotency_key: str = "",
        allow_status: set[int] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = self._headers(idempotency_key)
        last_error: Exception | None = None
        retry_safe = method.upper() in {"GET", "HEAD", "OPTIONS"}
        for attempt in range(_MAX_ATTEMPTS):
            try:
                status, body = await self._send(
                    method, url, headers, payload, timeout_seconds=timeout_seconds
                )
            except (ProviderTimeoutError, ProviderUnavailableError) as exc:
                last_error = exc
                if not retry_safe or attempt + 1 >= _MAX_ATTEMPTS:
                    raise
                await asyncio.sleep(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])
                continue
            if allow_status and status in allow_status:
                return body
            if status >= 500:
                last_error = _status_error(status, body)
                if not retry_safe or attempt + 1 >= _MAX_ATTEMPTS:
                    raise last_error
                await asyncio.sleep(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])
                continue
            if status < 200 or status >= 300:
                raise _status_error(status, body)
            return body
        raise ProviderUnavailableError(
            "The SIP gateway is unavailable", provider="sip"
        ) from last_error

    async def _send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict | None,
        *,
        timeout_seconds: float | None = None,
    ) -> tuple[int, dict[str, Any]]:
        if self._transport is not None:
            return await self._transport(method, url, headers=headers, json=payload)
        import httpx

        timeout = float(timeout_seconds or self.timeout_seconds)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "GET":
                    response = await client.get(url, headers=headers)
                else:
                    response = await client.post(url, headers=headers, json=payload or {})
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("The SIP gateway timed out", provider="sip") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(
                "The SIP gateway request failed", provider="sip"
            ) from exc
        try:
            parsed = response.json()
        except ValueError:
            parsed = {}
        return response.status_code, parsed if isinstance(parsed, dict) else {}


def _status_error(status: int, body: dict[str, Any]) -> Exception:
    """Map a gateway answer onto the provider error taxonomy.

    The control API answers with a small JSON document; the status decides
    the type, so the caller can tell "the credential is wrong" from "that
    call does not exist" without parsing the message.
    """
    detail = str(body.get("error") or "").strip()
    message = f"The SIP gateway answered status {status}"
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
    if status in {400, 422}:
        return ProviderValidationError(message, provider="sip")
    return ProviderUnavailableError(message, provider="sip")


def _to_call(body: dict[str, Any]) -> GatewayCall:
    return GatewayCall(
        id=str(body.get("id") or body.get("call_id") or ""),
        status=str(body.get("status") or "unknown"),
        to_number=str(body.get("to") or body.get("to_number") or ""),
        from_number=str(body.get("from") or body.get("from_number") or ""),
        codec=str(body.get("codec") or ""),
        detail=body if isinstance(body, dict) else {},
    )


def build_gateway_client(*, transport=None) -> SipGatewayClient:
    """Build a client from the Batch 02 carrier settings."""
    from app.carriers_v2.settings import get_carrier_v2_settings

    carrier = get_carrier_v2_settings()
    return SipGatewayClient(
        base_url=carrier.sip_gateway_url,
        token=carrier.sip_gateway_token,
        timeout_seconds=carrier.sip_gateway_timeout_seconds,
        transport=transport,
    )


__all__ = ["GatewayCall", "SipGatewayClient", "build_gateway_client"]
