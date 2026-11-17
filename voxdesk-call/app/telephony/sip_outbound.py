"""SIP outbound origination (PART 13, P13-01).

There is one SIP stack and it lives in `services/sip-gateway` (Kamailio +
rtpengine). This module is the *control plane* half: it turns a tenant's
`SipTrunk` row into the request the gateway needs, refuses a trunk that is not
safe to dial through, and turns the SIP final response back into one of the
disconnection reasons the traffic report understands.

The old code had two `SIP_OUTBOUND_NOT_IMPLEMENTED` branches. They are gone
because this module exists -- not because the branches were hidden.

Security notes, because they are the whole point of the trunk row:

* the password never appears here; `auth_secret_ref` is an opaque pointer,
  resolved by the authenticated internal runtime-config endpoint;
* the production `SipGatewayClient` still refuses tenant-trunk origination
  until the Go media/signalling stack can provide the trunk's required TLS and
  SRTP rather than silently downgrading to UDP/plain RTP;
* a trunk with SRTP disabled is refused for outbound dialling unless an
  explicit lab-only allowance is supplied, and the refusal names the field;
* the trunk host and digest credentials are selected by IDs in the gateway
  runtime contract, but the generic production call runtime does not yet select
  a `SipTrunk` row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.telephony.provider_errors import ProviderValidationError
from app.telephony.qos import DisconnectionReason, reason_from_sip_status

TRANSPORTS = ("udp", "tcp", "tls")
#: Codecs the gateway can bridge into the Pipecat path. G.711 is what a PSTN
#: trunk speaks; the bridge upsamples to 16 kHz PCM for the model.
CODECS = ("PCMU", "PCMA", "G722")
SIP_URI = re.compile(r"^sips?:(?P<user>[^@\s]+)@(?P<host>[A-Za-z0-9.\-]+)(?::(?P<port>\d{1,5}))?$")


class TrunkConfigError(ProviderValidationError):
    """A trunk that must not be dialled through, with the field named."""

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}", provider="sip")
        self.field = field


@dataclass(frozen=True)
class TrunkConfig:
    """The dialling-relevant half of a `SipTrunk` row."""

    host: str
    port: int = 5061
    transport: str = "tls"
    srtp_required: bool = True
    caller_id: str | None = None
    auth_username: str | None = None
    auth_secret_ref: str | None = None
    egress_address: str | None = None
    tenant_id: str | None = None
    trunk_id: str | None = None
    environment_id: str | None = None
    #: Set false only for a lab compose stack; the value is recorded on the
    #: request so a plaintext RTP call is visible in the audit trail.
    allow_plain_rtp: bool = False

    @classmethod
    def from_row(cls, row: Any, *, allow_plain_rtp: bool = False) -> "TrunkConfig":
        return cls(
            host=row.host,
            port=int(row.port),
            transport=str(row.transport or "tls").lower(),
            srtp_required=bool(row.srtp_required),
            caller_id=row.caller_id,
            auth_username=row.auth_username,
            auth_secret_ref=row.auth_secret_ref,
            egress_address=row.egress_address,
            tenant_id=str(getattr(row, "tenant_id", "") or "") or None,
            trunk_id=str(getattr(row, "id", "") or "") or None,
            environment_id=str(getattr(row, "environment_id", "") or "") or None,
            allow_plain_rtp=allow_plain_rtp,
        )

    def validate(self) -> None:
        if not self.host or " " in self.host:
            raise TrunkConfigError("host", "a trunk needs a host name")
        if not 1 <= int(self.port) <= 65535:
            raise TrunkConfigError("port", f"{self.port} is not a port")
        if self.transport not in TRANSPORTS:
            raise TrunkConfigError("transport", f"{self.transport!r} is not udp/tcp/tls")
        if self.transport == "tls" and not self.srtp_required:
            raise TrunkConfigError(
                "srtp_required", "signalling is TLS, so the media must be SRTP as well"
            )
        if not self.srtp_required and not self.allow_plain_rtp:
            raise TrunkConfigError(
                "srtp_required",
                "plain RTP is refused for outbound dialling; set allow_plain_rtp for a lab stack",
            )
        if not self.auth_username or not self.auth_username.strip():
            raise TrunkConfigError("auth_username", "the trunk has no digest-auth username")
        if not self.auth_secret_ref:
            raise TrunkConfigError("auth_secret_ref", "the trunk has no credential reference")
        if not str(self.auth_secret_ref).startswith("secret://"):
            raise TrunkConfigError(
                "auth_secret_ref", "credential must be an opaque secret:// reference, not plaintext"
            )
        if self.caller_id and not self.caller_id.startswith("+"):
            raise TrunkConfigError("caller_id", "caller ID must be E.164")

    def as_dict(self) -> dict[str, Any]:
        # The credential reference is metadata only; the runtime contract sends
        # tenant/trunk IDs and resolves the password in the API process.
        return {
            "tenant_id": self.tenant_id,
            "trunk_id": self.trunk_id,
            "environment_id": self.environment_id,
            "host": self.host,
            "port": self.port,
            "transport": self.transport,
            "srtp": self.srtp_required,
            "caller_id": self.caller_id,
            "auth_username": self.auth_username,
            "auth_secret_ref": self.auth_secret_ref,
            "egress_address": self.egress_address,
        }


@dataclass(frozen=True)
class OriginationResult:
    provider_call_id: str
    destination: str
    caller_id: str
    codec: str
    trunk: dict[str, Any]
    sip_status: int = 200
    reason: DisconnectionReason = DisconnectionReason.NORMAL
    raw: Mapping[str, Any] = field(default_factory=dict)

    @property
    def connected(self) -> bool:
        return self.sip_status < 300

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_call_id": self.provider_call_id,
            "destination": self.destination,
            "caller_id": self.caller_id,
            "codec": self.codec,
            "sip_status": self.sip_status,
            "reason": self.reason.value,
            "connected": self.connected,
            "trunk": dict(self.trunk),
        }


def parse_destination(destination: str) -> tuple[str, str]:
    """Classify a dial string as ``("e164"|"sip", value)``."""
    text = (destination or "").strip()
    if text.lower().startswith("sip:") or text.lower().startswith("sips:"):
        if not SIP_URI.match(text):
            raise TrunkConfigError("destination", f"{text!r} is not a SIP URI")
        return "sip", text
    if text.startswith("+") and text[1:].isdigit() and 8 <= len(text) <= 16:
        return "e164", text
    raise TrunkConfigError("destination", f"{text!r} is neither E.164 nor a SIP URI")


class SipOriginator:
    """Originate calls through a trunk via the gateway control API."""

    def __init__(self, gateway: Any, *, allow_plain_rtp: bool = False) -> None:
        # `gateway` is a SipGatewayClient (or any object with the same
        # `originate` coroutine); typing it loosely keeps the fake in the tests
        # from having to subclass a production class.
        self.gateway = gateway
        self.allow_plain_rtp = allow_plain_rtp

    async def originate(
        self,
        *,
        trunk: TrunkConfig,
        to_number: str,
        from_number: str | None = None,
        codec: str = "PCMU",
        call_id: str = "",
        idempotency_key: str = "",
    ) -> OriginationResult:
        trunk.validate()
        kind, destination = parse_destination(to_number)
        caller = (from_number or trunk.caller_id or "").strip()
        if not caller:
            raise TrunkConfigError("caller_id", "no caller ID on the request or the trunk")
        if kind == "e164" and not caller.startswith("+"):
            raise TrunkConfigError("caller_id", "caller ID must be E.164")
        if codec not in CODECS:
            raise TrunkConfigError("codec", f"{codec!r} is not bridged by the media bridge")

        if not trunk.tenant_id or not trunk.trunk_id:
            raise TrunkConfigError(
                "trunk_id", "a tenant-scoped trunk ID is required for runtime routing"
            )
        request = {
            "to": destination,
            "from": caller,
            "codec": codec,
            "trunk": trunk.as_dict(),
        }
        if call_id:
            request["call_id"] = call_id

        response = await self._call_gateway(request, idempotency_key=idempotency_key)
        status = int(response.get("sip_status", response.get("status_code", 200)) or 200)
        provider_call_id = str(
            response.get("call_id") or response.get("id") or response.get("provider_call_id") or ""
        )
        if status >= 300 and not provider_call_id:
            provider_call_id = ""
        return OriginationResult(
            provider_call_id=provider_call_id,
            destination=destination,
            caller_id=caller,
            codec=codec,
            trunk=trunk.as_dict(),
            sip_status=status,
            reason=reason_from_sip_status(status),
            raw=dict(response),
        )

    async def _call_gateway(self, request: dict[str, Any], *, idempotency_key: str = "") -> dict[str, Any]:
        """One place that knows how the gateway is called.

        Supports the three shapes that exist in this repo and in the lab: the
        production `SipGatewayClient.originate`, a plain callable (used by the
        SIPp smoke test) and a transport-level fake in unit tests.
        """
        originate = getattr(self.gateway, "originate", None)
        if originate is not None:
            if getattr(self.gateway, "supports_tenant_trunks", True) is False:
                raise TrunkConfigError(
                    "gateway",
                    "the configured SIP gateway does not support tenant-scoped trunk routing",
                )
            trunk = request["trunk"]
            call = await originate(
                to_number=request["to"],
                from_number=request["from"],
                call_id=request.get("call_id", ""),
                codec=request["codec"],
                tenant_id=trunk["tenant_id"],
                trunk_id=trunk["trunk_id"],
                environment_id=trunk.get("environment_id"),
                idempotency_key=idempotency_key,
            )
            return call.as_dict() if hasattr(call, "as_dict") else dict(call)
        if callable(self.gateway):
            result = self.gateway(request)
            if hasattr(result, "__await__"):
                result = await result
            return dict(result)
        raise TrunkConfigError("gateway", "no gateway client is configured")

    async def ping(self, trunk: TrunkConfig) -> dict[str, Any]:
        """Check the gateway control API; this does not send SIP OPTIONS to the trunk."""
        trunk.validate()
        probe = getattr(self.gateway, "health", None)
        if probe is None:
            return {
                "ok": False,
                "host": trunk.host,
                "trunk_reached": False,
                "probe_scope": "gateway_health_only",
                "reason": "gateway does not expose a health endpoint",
            }
        health = await probe()
        ok = bool(getattr(health, "healthy", health.get("ok") if isinstance(health, dict) else False))
        return {
            "ok": ok,
            "host": trunk.host,
            "trunk_reached": False,
            "probe_scope": "gateway_health_only",
            "detail": health if isinstance(health, dict) else str(health),
        }


def describe_failure(result: OriginationResult) -> str:
    """One sentence for the call log when origination did not connect."""
    if result.connected:
        return "Call connected"
    return {
        DisconnectionReason.INVALID_DESTINATION: "The destination number does not exist",
        DisconnectionReason.USER_BUSY: "The destination was busy",
        DisconnectionReason.USER_DECLINED: "The destination declined the call",
        DisconnectionReason.TELEPHONY_PROVIDER_PERMISSION_DENIED: (
            "The trunk credentials or caller ID were rejected"
        ),
        DisconnectionReason.SIP_ROUTING_ERROR: "The gateway could not route the call",
        DisconnectionReason.MARKED_AS_SPAM: "The call was marked as spam",
    }.get(result.reason, "The carrier could not complete the call")
