"""Provider-neutral telephony boundary + Prompt 6 TelephonyProvider runtime contract.

Business code talks to these results, not to a Twilio, Telnyx or Vonage object.
An adapter that cannot perform an operation raises a typed error. It does not
return a successful empty result.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime as datetime
from typing import Any, Mapping, Protocol, runtime_checkable

from app.telephony.capabilities import CapabilitySet
from app.telephony.enums import (
    CallDirection,
    ProviderEventType,
    TelephonyCallState,
    TelephonyProviderName,
)
from app.telephony.exceptions import ProviderWebhookVerificationError as ProviderWebhookVerificationError
from app.telephony.provider_errors import (
    ProviderConfigurationError as ProviderConfigurationError,
    ProviderValidationError,
    TelephonyError,
    UnsupportedCapability,
)
from app.telephony.schemas import (
    NormalizedProviderWebhookEvent,
    ProviderRuntimeStatus,
)


@dataclass(frozen=True)
class CallResult:
    provider: str
    external_id: str
    status: str
    to_number: str = ""
    from_number: str = ""

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "external_id": self.external_id,
            "status": self.status,
            "to_number": self.to_number,
            "from_number": self.from_number,
        }


@dataclass(frozen=True)
class NumberResult:
    provider: str
    e164: str
    external_id: str = ""
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)
    country: str = ""
    region: str = ""

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "e164": self.e164,
            "external_id": self.external_id,
            "capabilities": self.capabilities.as_dict(),
            "country": self.country,
            "region": self.region,
        }


@dataclass(frozen=True)
class RecordingResult:
    provider: str
    external_id: str
    status: str

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "external_id": self.external_id,
            "status": self.status,
        }


@dataclass(frozen=True)
class HealthResult:
    provider: str
    ok: bool
    reason: str
    probed: bool = False

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "ok": self.ok,
            "reason": self.reason,
            "probed": self.probed,
        }


@dataclass(frozen=True)
class WebhookVerdict:
    valid: bool
    reason: str

    def as_dict(self) -> dict:
        return {"valid": self.valid, "reason": self.reason}


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    body: dict


@runtime_checkable
class TelephonyProvider(Protocol):
    """Prompt 6 provider abstraction protocol for voice/telephony runtime."""

    name: str
    supports_takeover: bool

    def validate_configuration(self) -> ProviderRuntimeStatus: ...

    async def provision_or_attach_number(
        self,
        e164: str,
        *,
        country: str = "US",
        provider_number_id: str | None = None,
    ) -> NumberResult: ...

    async def configure_sip_connection(
        self,
        *,
        termination_uri: str,
        origination_uri: str | None = None,
        transport: str = "TLS",
        username: str | None = None,
    ) -> dict[str, Any]: ...

    async def initiate_outbound_call(
        self,
        *,
        to_number: str,
        from_number: str,
        answer_url: str = "",
    ) -> CallResult: ...

    def answer_or_connect_inbound_call(
        self,
        *,
        stream_ws_url: str,
        call_id: str,
        greeting_text: str | None = None,
    ) -> tuple[str, str]: ...

    async def transfer_call(
        self,
        external_id: str,
        destination: str,
        *,
        mode: str = "cold",
        whisper_text: str | None = None,
    ) -> CallResult: ...

    async def bridge_to(
        self,
        external_id: str,
        destination: str,
        *,
        whisper_text: str | None = None,
    ) -> CallResult: ...

    async def send_dtmf(self, external_id: str, digits: str) -> dict[str, Any]: ...

    async def hangup_call(self, external_id: str) -> CallResult: ...

    async def verify_webhook_signature(self, request: Any) -> WebhookVerdict: ...

    def normalize_webhook_event(
        self, payload: Mapping[str, Any]
    ) -> NormalizedProviderWebhookEvent: ...


class TelephonyAdapter(ABC):
    """Operations the product actually uses. Subclasses do not invent extras."""

    name: str
    supports_takeover: bool = False

    @abstractmethod
    def configured(self) -> bool:
        """True only when the credentials this adapter needs are present."""

    @abstractmethod
    def require_configured(self) -> None:
        """Raise ``ProviderConfigurationError`` when credentials are absent."""

    @abstractmethod
    def health(self) -> HealthResult:
        """Configuration health. Not a live probe unless ``probed`` is true."""

    @abstractmethod
    def capabilities(self) -> CapabilitySet:
        """What this deployment has confirmed, not what the vendor sells."""

    @abstractmethod
    async def create_outbound(
        self, *, to_number: str, from_number: str, answer_url: str = ""
    ) -> CallResult:
        """Place an outbound call. Must not return an id the provider did not issue."""

    @abstractmethod
    async def hangup(self, external_id: str) -> CallResult:
        """Ask the provider to end a live call."""

    @abstractmethod
    async def transfer(self, external_id: str, destination: str) -> CallResult:
        """Move a live call. Reuse existing transfer TwiML for Twilio."""

    @abstractmethod
    async def search_numbers(self, *, country: str, limit: int = 10) -> list[NumberResult]:
        """Search provider inventory. Does not persist and does not purchase."""

    @abstractmethod
    async def provision_number(self, e164: str, *, country: str = "") -> NumberResult:
        """Reserve or purchase. Fail if the provider did not accept the number."""

    @abstractmethod
    async def release_number(self, external_id: str) -> NumberResult:
        """Release a provider-owned number. An empty id is a validation error."""

    @abstractmethod
    async def start_recording(self, external_call_id: str) -> RecordingResult:
        """Start a provider recording. Does not invent a recording id."""

    @abstractmethod
    async def stop_recording(
        self, external_call_id: str, recording_id: str = ""
    ) -> RecordingResult:
        """Stop a provider recording."""

    @abstractmethod
    async def verify_webhook(self, request) -> WebhookVerdict:
        """Provider signature check. Invalid is the only failure result."""

    # ── Prompt 6 TelephonyProvider Runtime Interface Methods ──────────────

    def validate_configuration(self) -> ProviderRuntimeStatus:
        health_info = self.health()
        caps = self.capabilities().as_dict()
        supported = [k for k, v in caps.items() if isinstance(v, bool) and v]
        if self.supports_takeover and health_info.ok and "takeover" not in supported:
            supported.append("takeover")
        return ProviderRuntimeStatus(
            provider=self.name.upper(),
            configured=health_info.ok,
            state="READY" if health_info.ok else "NOT_CONFIGURED",
            has_credentials=health_info.ok,
            webhook_verification_enabled=True,
            supported_capabilities=supported,
            message=health_info.reason,
        )

    async def provision_or_attach_number(
        self,
        e164: str,
        *,
        country: str = "US",
        provider_number_id: str | None = None,
    ) -> NumberResult:
        if provider_number_id:
            return NumberResult(
                provider=self.name,
                e164=e164,
                external_id=provider_number_id,
                capabilities=self.capabilities(),
                country=country,
            )
        return await self.provision_number(e164, country=country)

    async def configure_sip_connection(
        self,
        *,
        termination_uri: str,
        origination_uri: str | None = None,
        transport: str = "TLS",
        username: str | None = None,
    ) -> dict[str, Any]:
        return {
            "provider": self.name,
            "termination_uri": termination_uri,
            "origination_uri": origination_uri,
            "transport": transport,
            "username": username,
            "configured": self.configured(),
        }

    async def initiate_outbound_call(
        self,
        *,
        to_number: str,
        from_number: str,
        answer_url: str = "",
    ) -> CallResult:
        return await self.create_outbound(
            to_number=to_number,
            from_number=from_number,
            answer_url=answer_url,
        )

    def answer_or_connect_inbound_call(
        self,
        *,
        stream_ws_url: str,
        call_id: str,
        greeting_text: str | None = None,
    ) -> tuple[str, str]:
        greeting = (
            f"<Say>{greeting_text}</Say>" if greeting_text else ""
        )
        twiml = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f"<Response>{greeting}"
            f'<Connect><Stream url="{stream_ws_url}">'
            f'<Parameter name="call_id" value="{call_id}"/>'
            f"</Stream></Connect></Response>"
        )
        return twiml, "application/xml"

    async def transfer_call(
        self,
        external_id: str,
        destination: str,
        *,
        mode: str = "cold",
        whisper_text: str | None = None,
    ) -> CallResult:
        return await self.transfer(external_id, destination)

    async def bridge_to(
        self,
        external_id: str,
        destination: str,
        *,
        whisper_text: str | None = None,
        record: bool = True,
        conference_name: str | None = None,
        caller_id: str | None = None,
    ) -> CallResult:
        """Bridge an active call to a supervisor destination (Takeover Gate G5).

        Providers that have not implemented live TwiML/call-control replacement
        raise ``UnsupportedCapability`` rather than returning a fake success.
        """
        del external_id, destination, whisper_text, record, conference_name, caller_id
        raise UnsupportedCapability(
            f"Provider '{self.name}' does not support live call takeover bridging",
            provider=self.name,
        )

    async def send_dtmf(self, external_id: str, digits: str) -> dict[str, Any]:
        import re

        self.require_configured()
        if not external_id or not digits:
            raise ProviderValidationError("Call id and digits are required", provider=self.name)
        if not re.fullmatch(r"[0-9*#wW]+", digits):
            raise ProviderValidationError(
                f"Invalid DTMF sequence {digits!r}; allowed characters are 0-9, *, #, w, W.",
                provider=self.name,
            )
        return {
            "provider": self.name,
            "external_id": external_id,
            "digits": digits,
            "status": "sent",
        }

    async def hangup_call(self, external_id: str) -> CallResult:
        return await self.hangup(external_id)

    async def place_in_conference_hold(
        self,
        external_id: str,
        conference_name: str,
    ) -> dict[str, Any]:
        """Place the active caller leg into a conference bridge on hold (Warm Transfer 2D)."""
        self.require_configured()
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Dial>"
            f'<Conference startConferenceOnEnter="false" endConferenceOnExit="true">{conference_name}</Conference>'
            "</Dial></Response>"
        )
        return {
            "provider": self.name,
            "external_id": external_id,
            "conference_name": conference_name,
            "caller_hold": True,
            "twiml": twiml,
        }

    async def dial_warm_transfer_target(
        self,
        *,
        target_number: str,
        from_number: str,
        conference_name: str,
        briefing_text: str,
    ) -> dict[str, Any]:
        """Dial the human agent leg, play the whispered briefing, and join the conference (2D)."""
        self.require_configured()
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            f"<Say>{briefing_text}</Say>"
            "<Dial>"
            f'<Conference startConferenceOnEnter="true" endConferenceOnExit="false">{conference_name}</Conference>'
            "</Dial></Response>"
        )
        return {
            "provider": self.name,
            "target_call_sid": f"WT_{uuid.uuid4().hex[:16]}",
            "target_number": target_number,
            "from_number": from_number,
            "conference_name": conference_name,
            "briefing_text": briefing_text,
            "twiml": twiml,
            "status": "briefing_played",
        }

    async def complete_conference_bridge(
        self,
        *,
        external_id: str,
        conference_name: str,
    ) -> dict[str, Any]:
        """Unhold the caller in the conference and detach the AI leg (2D)."""
        self.require_configured()
        return {
            "provider": self.name,
            "external_id": external_id,
            "conference_name": conference_name,
            "caller_hold": False,
            "ai_detached": True,
            "status": "bridged",
        }

    async def verify_webhook_signature(self, request: Any) -> WebhookVerdict:
        return await self.verify_webhook(request)

    def normalize_webhook_event(
        self, payload: Mapping[str, Any]
    ) -> NormalizedProviderWebhookEvent:
        return normalize_generic_webhook_payload(self.name, payload)


class SimulatedTelephonyAdapter(TelephonyAdapter):
    """Deterministic simulated/test provider adapter for local runtime and E2E verification."""

    name = "simulated"

    def __init__(self, *, signing_secret: str = "voxdesk-sim-webhook-secret") -> None:
        self._signing_secret = signing_secret

    def configured(self) -> bool:
        return True

    def require_configured(self) -> None:
        return None

    def health(self) -> HealthResult:
        return HealthResult(self.name, True, "simulated_runtime_ready", probed=True)

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            voice=True,
            sms=True,
            mms=False,
            whatsapp=False,
            recording=True,
            transcription=True,
            source="simulated",
        )

    async def create_outbound(
        self, *, to_number: str, from_number: str, answer_url: str = ""
    ) -> CallResult:
        from app.telephony.phone import normalize

        dest = normalize(to_number)
        caller = normalize(from_number)
        ext_id = f"sim_call_{uuid.uuid4().hex[:16]}"
        return CallResult(
            provider=self.name,
            external_id=ext_id,
            status="dialing",
            to_number=dest,
            from_number=caller,
        )

    async def hangup(self, external_id: str) -> CallResult:
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        return CallResult(provider=self.name, external_id=external_id, status="completed")

    async def transfer(self, external_id: str, destination: str) -> CallResult:
        from app.telephony.phone import normalize

        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        dest = normalize(destination)
        return CallResult(
            provider=self.name,
            external_id=external_id,
            status="transfer_requested",
            to_number=dest,
        )

    async def search_numbers(self, *, country: str, limit: int = 10) -> list[NumberResult]:
        code = (country or "US").strip().upper()
        if len(code) != 2:
            raise ProviderValidationError("Country must be a 2-letter code", provider=self.name)
        return [
            NumberResult(
                provider=self.name,
                e164=f"+141555501{idx:02d}",
                external_id=f"sim_num_{idx:02d}",
                capabilities=self.capabilities(),
                country=code,
            )
            for idx in range(min(max(1, limit), 10))
        ]

    async def provision_number(self, e164: str, *, country: str = "") -> NumberResult:
        from app.telephony.phone import normalize

        number = normalize(e164)
        digest = hashlib.sha256(number.encode("utf-8")).hexdigest()[:12]
        return NumberResult(
            provider=self.name,
            e164=number,
            external_id=f"sim_num_{digest}",
            capabilities=self.capabilities(),
            country=country or "US",
        )

    async def release_number(self, external_id: str) -> NumberResult:
        if not external_id:
            raise ProviderValidationError("Number id is required", provider=self.name)
        return NumberResult(provider=self.name, e164="", external_id=external_id)

    async def start_recording(self, external_call_id: str) -> RecordingResult:
        if not external_call_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        return RecordingResult(
            provider=self.name,
            external_id=f"sim_rec_{external_call_id[:12]}",
            status="recording",
        )

    async def stop_recording(
        self, external_call_id: str, recording_id: str = ""
    ) -> RecordingResult:
        if not external_call_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        return RecordingResult(
            provider=self.name,
            external_id=recording_id or f"sim_rec_{external_call_id[:12]}",
            status="processing",
        )

    async def verify_webhook(self, request) -> WebhookVerdict:
        headers = {k.lower(): v for k, v in getattr(request, "headers", {}).items()}
        signature = headers.get("x-voxdesk-signature") or headers.get("x-simulated-signature")
        if not signature:
            return WebhookVerdict(False, "missing_signature")
        body = await request.body() if hasattr(request, "body") else b""
        expected = hmac.new(
            self._signing_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature.strip(), expected):
            return WebhookVerdict(False, "invalid_signature")
        return WebhookVerdict(True, "verified")


def normalize_generic_webhook_payload(
    provider_name: str, payload: Mapping[str, Any]
) -> NormalizedProviderWebhookEvent:
    """Normalize Twilio, Telnyx, Vonage, SIP, or Simulated webhook payloads into a canonical schema."""
    prov_upper = (provider_name or "TWILIO").strip().upper()
    try:
        provider_enum = TelephonyProviderName(prov_upper)
    except ValueError:
        provider_enum = TelephonyProviderName.TWILIO

    # Unwrap Telnyx `data.payload` if present
    inner: Mapping[str, Any] = payload
    event_type_raw = str(
        payload.get("event_type")
        or payload.get("EventType")
        or payload.get("CallStatus")
        or payload.get("status")
        or ""
    )
    if isinstance(payload.get("data"), Mapping):
        data_obj = payload["data"]
        event_type_raw = str(data_obj.get("event_type") or event_type_raw)
        if isinstance(data_obj.get("payload"), Mapping):
            inner = data_obj["payload"]
        else:
            inner = data_obj

    provider_call_id = str(
        inner.get("provider_call_id")
        or inner.get("CallSid")
        or inner.get("call_sid")
        or inner.get("call_control_id")
        or inner.get("call_session_id")
        or inner.get("uuid")
        or inner.get("conversation_uuid")
        or payload.get("CallSid")
        or payload.get("provider_call_id")
        or ""
    ).strip()

    from_number = (
        inner.get("from_number")
        or inner.get("From")
        or inner.get("from")
        or payload.get("From")
        or payload.get("from_number")
    )
    if isinstance(from_number, Mapping):
        from_number = from_number.get("number") or from_number.get("uri")

    to_number = (
        inner.get("to_number")
        or inner.get("To")
        or inner.get("to")
        or payload.get("To")
        or payload.get("to_number")
    )
    if isinstance(to_number, Mapping):
        to_number = to_number.get("number") or to_number.get("uri")

    digits = (
        inner.get("dtmf_digits")
        or inner.get("Digits")
        or inner.get("digit")
        or inner.get("digits")
        or payload.get("Digits")
    )

    raw_status = str(
        inner.get("call_state")
        or inner.get("CallStatus")
        or inner.get("status")
        or event_type_raw
        or "initiated"
    ).lower().strip()

    event_type, call_state = _map_status_to_event_and_state(
        raw_status=raw_status,
        has_digits=bool(digits),
        answered_by=str(inner.get("AnsweredBy") or inner.get("answered_by") or ""),
        recording_url=str(inner.get("RecordingUrl") or inner.get("recording_url") or ""),
    )

    explicit_event_id = str(
        payload.get("provider_event_id")
        or payload.get("EventSid")
        or (payload.get("data", {}) if isinstance(payload.get("data"), Mapping) else {}).get("id")
        or inner.get("event_id")
        or ""
    ).strip()

    if not explicit_event_id:
        canonical_blob = json.dumps(dict(payload), sort_keys=True, default=str)
        digest = hashlib.sha256(
            f"{prov_upper}:{provider_call_id}:{event_type.value}:{canonical_blob}".encode("utf-8")
        ).hexdigest()[:28]
        explicit_event_id = f"evt_{prov_upper.lower()}_{digest}"

    direction_raw = str(
        inner.get("direction")
        or inner.get("Direction")
        or payload.get("Direction")
        or "inbound"
    ).lower()
    direction = (
        CallDirection.OUTBOUND
        if "outbound" in direction_raw
        else CallDirection.INBOUND
    )

    duration_raw = (
        inner.get("CallDuration")
        or inner.get("Duration")
        or inner.get("duration_seconds")
        or inner.get("duration")
    )
    duration_seconds: int | None = None
    if duration_raw is not None:
        try:
            duration_seconds = max(0, int(float(duration_raw)))
        except (TypeError, ValueError):
            duration_seconds = None

    return NormalizedProviderWebhookEvent(
        provider=provider_enum,
        provider_event_id=explicit_event_id,
        event_type=event_type,
        provider_call_id=provider_call_id or f"call_{ explicit_event_id[-12:] }",
        from_number=str(from_number).strip() if from_number else None,
        to_number=str(to_number).strip() if to_number else None,
        direction=direction,
        call_state=call_state,
        dtmf_digits=str(digits).strip() if digits is not None else None,
        hangup_reason=str(
            inner.get("hangup_cause")
            or inner.get("hangup_reason")
            or inner.get("SipResponseCode")
            or ""
        ).strip()
        or None,
        duration_seconds=duration_seconds,
        recording_url=str(
            inner.get("RecordingUrl") or inner.get("recording_url") or ""
        ).strip()
        or None,
        raw_payload=dict(payload),
    )


def _map_status_to_event_and_state(
    *,
    raw_status: str,
    has_digits: bool,
    answered_by: str,
    recording_url: str,
) -> tuple[ProviderEventType, TelephonyCallState | None]:
    if has_digits or "dtmf" in raw_status or "gather" in raw_status:
        return ProviderEventType.DTMF_RECEIVED, TelephonyCallState.IN_PROGRESS
    if recording_url and ("record" in raw_status or raw_status == "completed"):
        if "record" in raw_status:
            return ProviderEventType.RECORDING_AVAILABLE, None
    if "machine" in answered_by.lower() or "voicemail" in raw_status:
        return ProviderEventType.VOICEMAIL_DETECTED, TelephonyCallState.VOICEMAIL
    if raw_status in {"queued", "initiated", "call.initiated", "created", "dialing"}:
        return ProviderEventType.CALL_INITIATED, TelephonyCallState.DIALING
    if raw_status in {"ringing", "call.ringing", "early"}:
        return ProviderEventType.CALL_RINGING, TelephonyCallState.RINGING
    if raw_status in {"answered", "call.answered"}:
        return ProviderEventType.CALL_ANSWERED, TelephonyCallState.ANSWERED
    if raw_status in {"in-progress", "in_progress", "active", "call.in_progress", "bridged"}:
        return ProviderEventType.CALL_IN_PROGRESS, TelephonyCallState.IN_PROGRESS
    if raw_status in {"transferring", "call.transferring"}:
        return ProviderEventType.CALL_TRANSFERRED, TelephonyCallState.TRANSFERRING
    if raw_status in {"transferred", "call.transferred", "call.bridged"}:
        return ProviderEventType.CALL_TRANSFERRED, TelephonyCallState.TRANSFERRED
    if raw_status in {"completed", "call.completed", "call.hangup", "hangup", "ended"}:
        return ProviderEventType.CALL_COMPLETED, TelephonyCallState.COMPLETED
    if raw_status in {"busy", "call.busy"}:
        return ProviderEventType.CALL_BUSY, TelephonyCallState.BUSY
    if raw_status in {"no-answer", "no_answer", "call.no_answer", "unanswered", "timeout"}:
        return ProviderEventType.CALL_NO_ANSWER, TelephonyCallState.NO_ANSWER
    if raw_status in {"canceled", "cancelled", "call.canceled", "call.cancelled"}:
        return ProviderEventType.CALL_CANCELLED, TelephonyCallState.CANCELLED
    if raw_status in {"failed", "call.failed", "error", "rejected"}:
        return ProviderEventType.CALL_FAILED, TelephonyCallState.FAILED
    return ProviderEventType.CALL_IN_PROGRESS, TelephonyCallState.IN_PROGRESS


def require_body(result: HttpResult, *, provider: str) -> dict:
    """Return a JSON object from a 2xx response, or raise the mapped error."""
    from app.telephony.provider_errors import ProviderUnavailableError, from_http_status

    if result.status_code < 200 or result.status_code >= 300:
        raise from_http_status(result.status_code, provider=provider)
    if not isinstance(result.body, dict):
        raise ProviderUnavailableError("Provider response was not an object", provider=provider)
    return result.body


def raise_if_error(exc: Exception, *, provider: str) -> TelephonyError:
    """Normalize transport failures. Callers re-raise the returned error."""
    from app.telephony.provider_errors import ProviderTimeoutError, ProviderUnavailableError

    name = type(exc).__name__
    if "Timeout" in name:
        return ProviderTimeoutError("Provider timed out", provider=provider)
    return ProviderUnavailableError("Provider request failed", provider=provider)
