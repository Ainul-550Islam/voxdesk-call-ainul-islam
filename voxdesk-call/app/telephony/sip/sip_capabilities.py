"""SIP media capability resolution.

The SIP media path exists only when the gateway is *actually* ready: the
control API must answer ``/readyz`` with 200 and the shared token must be
accepted. Anything else -- no URL, a placeholder token, a gateway that is
down, a 401 -- resolves to "not supported", and the caller answers 501
rather than pretending a trunk exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.telephony.provider_errors import (
    ProviderTimeoutError,
    ProviderUnavailableError,
)

#: Reason codes. They are stable so an operator can read them in a log.
REASON_NOT_CONFIGURED = "sip_gateway_not_configured"
REASON_TOKEN_INVALID = "sip_gateway_token_invalid"
REASON_GATEWAY_UNREACHABLE = "sip_gateway_unreachable"
REASON_GATEWAY_NOT_READY = "sip_gateway_not_ready"
REASON_READY = "sip_gateway_ready"


@dataclass(frozen=True)
class SipGatewayCapability:
    """The honest state of the SIP media path."""

    ready: bool
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def supports_ai_media_stream(self) -> bool:
        return bool(self.ready)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "reason": self.reason,
            "supports_ai_media_stream": self.supports_ai_media_stream,
            "detail": dict(self.detail),
        }


def _carrier_settings():
    from app.carriers_v2.settings import get_carrier_v2_settings

    return get_carrier_v2_settings()


async def probe_sip_gateway(
    *,
    base_url: str = "",
    token: str = "",
    timeout_seconds: float = 2.0,
    transport=None,
) -> SipGatewayCapability:
    """Ask the gateway whether it is ready to carry media."""
    from app.carriers_v2.settings import looks_like_placeholder

    url = (base_url or "").rstrip("/")
    if not url:
        return SipGatewayCapability(False, REASON_NOT_CONFIGURED, {"base_url": ""})
    if not token or looks_like_placeholder(token):
        return SipGatewayCapability(False, REASON_TOKEN_INVALID, {"base_url": url})

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        status, body = await _send("GET", f"{url}/readyz", headers, transport, timeout_seconds)
    except (ProviderTimeoutError, ProviderUnavailableError) as exc:
        return SipGatewayCapability(
            False, REASON_GATEWAY_UNREACHABLE, {"base_url": url, "error": str(exc)[:160]}
        )
    if status == 401 or status == 403:
        return SipGatewayCapability(False, REASON_TOKEN_INVALID, {"base_url": url, "status": status})
    if status != 200:
        return SipGatewayCapability(
            False, REASON_GATEWAY_NOT_READY, {"base_url": url, "status": status}
        )
    ready = bool(body.get("ready", True)) if isinstance(body, dict) else True
    if not ready:
        return SipGatewayCapability(
            False, REASON_GATEWAY_NOT_READY, {"base_url": url, "status": status}
        )
    return SipGatewayCapability(
        True, REASON_READY, {"base_url": url, "status": status, "reported": body}
    )


async def resolve_sip_capability(*, transport=None) -> SipGatewayCapability:
    """Resolve the capability using the configured gateway."""
    carrier = _carrier_settings()
    return await probe_sip_gateway(
        base_url=carrier.sip_gateway_url,
        token=carrier.sip_gateway_token,
        timeout_seconds=carrier.sip_gateway_ready_timeout_seconds,
        transport=transport,
    )


def sip_media_capabilities() -> dict[str, dict]:
    """The static SIP row of the carrier capability table.

    This is the *configuration* view (is the gateway even pointed at?).
    The live readiness is resolved by :func:`resolve_sip_capability`.
    """
    carrier = _carrier_settings()
    configured = carrier.sip_gateway_ready()
    return {
        "sip": {
            "supported": configured,
            "serializer": "SIPBridgeFrameSerializer" if configured else None,
            "encodings": ["PCMU", "PCMA"] if configured else [],
            "sample_rates": [8000] if configured else [],
            "reason": (
                "The SIP gateway is configured."
                if configured
                else "The SIP gateway is not configured (SIP_GATEWAY_URL / SIP_GATEWAY_TOKEN)."
            ),
        }
    }


async def _send(
    method: str,
    url: str,
    headers: dict[str, str],
    transport,
    timeout_seconds: float,
) -> tuple[int, dict[str, Any]]:
    if transport is not None:
        return await transport(method, url, headers=headers, json=None)
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url, headers=headers)
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
    if not isinstance(parsed, dict):
        parsed = {}
    return response.status_code, parsed


__all__ = [
    "REASON_GATEWAY_NOT_READY",
    "REASON_GATEWAY_UNREACHABLE",
    "REASON_NOT_CONFIGURED",
    "REASON_READY",
    "REASON_TOKEN_INVALID",
    "SipGatewayCapability",
    "probe_sip_gateway",
    "resolve_sip_capability",
    "sip_media_capabilities",
]
