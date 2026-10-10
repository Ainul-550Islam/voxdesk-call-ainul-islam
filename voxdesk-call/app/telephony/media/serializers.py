"""Multi-Carrier Media Frame Serializer Factory (Sub-Phase 2F).

Bridges carrier-specific bidirectional WebSocket media streams into Pipecat frames:
- `twilio`: `pipecat.serializers.twilio.TwilioFrameSerializer`
- `telnyx`: `pipecat.serializers.telnyx.TelnyxFrameSerializer`
- `plivo`:  `pipecat.serializers.plivo.PlivoFrameSerializer`
- `sip`:    `SIPBridgeFrameSerializer` (Kamailio/RTPEngine JSON/PCMU WebSocket media bridge)
- `vonage`: Honest `UnsupportedCapability` (Vonage voice media streaming is disabled; `voice=False`).
"""

from __future__ import annotations

import base64
import json
from typing import Any

from pipecat.frames.frames import (
    AudioRawFrame,
    Frame,
    InputAudioRawFrame,
    InputDTMFFrame,
    KeypadEntry,
    OutputAudioRawFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer, FrameSerializerType
from pipecat.serializers.plivo import PlivoFrameSerializer
from pipecat.serializers.telnyx import TelnyxFrameSerializer
from pipecat.serializers.twilio import TwilioFrameSerializer

from app.core.config import settings
from app.telephony.provider_errors import ProviderValidationError, UnsupportedCapability


class SIPBridgeFrameSerializer(FrameSerializer):
    """Frame serializer for the Kamailio + RTPEngine SIP-to-WebSocket Media Bridge (ADR-001)."""

    def __init__(
        self,
        *,
        stream_sid: str,
        call_sid: str = "",
        sample_rate: int = 8000,
    ) -> None:
        super().__init__()
        self.stream_sid = stream_sid
        self.call_sid = call_sid
        self.sample_rate = sample_rate

    @property
    def type(self) -> FrameSerializerType:
        return FrameSerializerType.TEXT

    async def serialize(self, frame: Frame) -> str | bytes | None:
        if isinstance(frame, (OutputAudioRawFrame, AudioRawFrame)):
            b64 = base64.b64encode(frame.audio).decode("ascii")
            return json.dumps(
                {
                    "event": "media",
                    "stream_id": self.stream_sid,
                    "call_id": self.call_sid,
                    "media": {
                        "payload": b64,
                        "sample_rate": getattr(frame, "sample_rate", self.sample_rate),
                    },
                }
            )
        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes):
            return InputAudioRawFrame(
                audio=data,
                sample_rate=self.sample_rate,
                num_channels=1,
            )
        try:
            msg = json.loads(data)
        except ValueError:
            return None
        event = str(msg.get("event") or "").lower()
        if event == "media":
            payload_b64 = (msg.get("media") or {}).get("payload") or msg.get("payload")
            if not payload_b64:
                return None
            raw_bytes = base64.b64decode(payload_b64)
            return InputAudioRawFrame(
                audio=raw_bytes,
                sample_rate=int((msg.get("media") or {}).get("sample_rate") or self.sample_rate),
                num_channels=1,
            )
        if event == "dtmf":
            digit = str((msg.get("dtmf") or {}).get("digit") or msg.get("digit") or "").strip()
            if digit:
                try:
                    return InputDTMFFrame(button=KeypadEntry(digit[0]))
                except ValueError:
                    return None
        return None


def supported_media_serializers() -> dict[str, dict[str, Any]]:
    return {
        "twilio": {
            "supported": True,
            "serializer": "TwilioFrameSerializer",
            "encodings": ["audio/x-mulaw"],
            "sample_rates": [8000],
        },
        "telnyx": {
            "supported": True,
            "serializer": "TelnyxFrameSerializer",
            "encodings": ["PCMU", "PCMA", "G722"],
            "sample_rates": [8000],
        },
        "plivo": {
            "supported": True,
            "serializer": "PlivoFrameSerializer",
            "encodings": ["audio/x-mulaw", "audio/x-l16"],
            "sample_rates": [8000, 16000],
        },
        "sip": {
            "supported": True,
            "serializer": "SIPBridgeFrameSerializer",
            "encodings": ["PCMU", "L16"],
            "sample_rates": [8000, 16000],
        },
        "vonage": {
            "supported": False,
            "serializer": None,
            "reason": "Vonage voice media streaming is not enabled (CapabilitySet.voice=False).",
        },
    }


def build_serializer(
    provider: str,
    *,
    stream_sid: str,
    call_sid: str = "",
    inbound_encoding: str = "PCMU",
    outbound_encoding: str = "PCMU",
    sample_rate: int = 8000,
    api_key: str | None = None,
    account_sid: str | None = None,
    auth_token: str | None = None,
    auto_hang_up: bool = False,
) -> FrameSerializer:
    """Instantiate the Pipecat `FrameSerializer` for `provider` (`twilio`, `telnyx`, `plivo`, `sip`)."""
    prov = (provider or "").strip().lower()
    if not stream_sid:
        raise ProviderValidationError("stream_sid is required to build a media serializer", provider=prov or "unknown")

    if prov == "twilio":
        resolved_sid = account_sid if account_sid is not None else settings.twilio_account_sid
        resolved_token = auth_token if auth_token is not None else settings.twilio_auth_token
        can_hangup = bool(auto_hang_up and call_sid and resolved_sid and resolved_token)
        ser = TwilioFrameSerializer(
            stream_sid=stream_sid,
            call_sid=call_sid or None,
            account_sid=resolved_sid if can_hangup else None,
            auth_token=resolved_token if can_hangup else None,
            params=TwilioFrameSerializer.InputParams(
                twilio_sample_rate=sample_rate,
                sample_rate=sample_rate,
                auto_hang_up=can_hangup,
            ),
        )
        ser._sample_rate = sample_rate
        return ser

    if prov == "telnyx":
        resolved_key = api_key if api_key is not None else getattr(settings, "telnyx_api_key", None)
        can_hangup = bool(auto_hang_up and call_sid and resolved_key)
        ser = TelnyxFrameSerializer(
            stream_id=stream_sid,
            outbound_encoding=outbound_encoding,
            inbound_encoding=inbound_encoding,
            call_control_id=call_sid or None,
            api_key=resolved_key if can_hangup else None,
            params=TelnyxFrameSerializer.InputParams(
                telnyx_sample_rate=sample_rate,
                sample_rate=sample_rate,
                outbound_encoding=outbound_encoding,
                inbound_encoding=inbound_encoding,
                auto_hang_up=can_hangup,
            ),
        )
        ser._sample_rate = sample_rate
        return ser

    if prov == "plivo":
        can_hangup = bool(auto_hang_up and call_sid and account_sid and auth_token)
        ser = PlivoFrameSerializer(
            stream_id=stream_sid,
            call_id=call_sid or None,
            auth_id=account_sid if can_hangup else None,
            auth_token=auth_token if can_hangup else None,
            params=PlivoFrameSerializer.InputParams(
                plivo_sample_rate=sample_rate,
                sample_rate=sample_rate,
                auto_hang_up=can_hangup,
            ),
        )
        ser._sample_rate = sample_rate
        return ser

    if prov == "sip":
        return SIPBridgeFrameSerializer(
            stream_sid=stream_sid,
            call_sid=call_sid,
            sample_rate=sample_rate,
        )

    if prov == "vonage":
        raise UnsupportedCapability(
            "Vonage media streaming is not supported in this runtime (CapabilitySet.voice=False).",
            provider="vonage",
        )

    raise ProviderValidationError(
        f"Unsupported media serializer provider: {provider!r}",
        provider=prov or "unknown",
    )
