"""
app/telephony/enums.py
Canonical provider, direction, call-state, transfer-state, SIP, and media-state enums
for Prompt 6 Telephony / Voice Runtime.
"""

from __future__ import annotations

from enum import Enum


class _CaseInsensitiveStrEnum(str, Enum):
    @classmethod
    def _missing_(cls, value: object):
        if isinstance(value, str):
            upper = value.strip().upper()
            for member in cls:
                if member.value.upper() == upper or member.name.upper() == upper:
                    return member
        return None


class TelephonyProviderName(_CaseInsensitiveStrEnum):
    TWILIO = "TWILIO"
    TELNYX = "TELNYX"
    VONAGE = "VONAGE"
    SIP = "SIP"
    SIMULATED = "SIMULATED"
    TEST_SIMULATION = "SIMULATED"


class CallDirection(_CaseInsensitiveStrEnum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class TelephonyCallState(_CaseInsensitiveStrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONFIGURED = "CONFIGURED"
    PROVISIONING = "PROVISIONING"
    READY = "READY"
    CREATED = "CREATED"
    DIALING = "DIALING"
    RINGING = "RINGING"
    ANSWERED = "ANSWERED"
    IN_PROGRESS = "IN_PROGRESS"
    TRANSFERRING = "TRANSFERRING"
    TRANSFERRED = "TRANSFERRED"
    ENDING = "ENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BUSY = "BUSY"
    NO_ANSWER = "NO_ANSWER"
    VOICEMAIL = "VOICEMAIL"


TERMINAL_CALL_STATES: frozenset[TelephonyCallState] = frozenset(
    {
        TelephonyCallState.COMPLETED,
        TelephonyCallState.FAILED,
        TelephonyCallState.CANCELLED,
        TelephonyCallState.BUSY,
        TelephonyCallState.NO_ANSWER,
        TelephonyCallState.VOICEMAIL,
    }
)

ACTIVE_CALL_STATES: frozenset[TelephonyCallState] = frozenset(
    {
        TelephonyCallState.CREATED,
        TelephonyCallState.DIALING,
        TelephonyCallState.RINGING,
        TelephonyCallState.ANSWERED,
        TelephonyCallState.IN_PROGRESS,
        TelephonyCallState.TRANSFERRING,
        TelephonyCallState.TRANSFERRED,
        TelephonyCallState.ENDING,
    }
)


class PhoneNumberLifecycleStatus(_CaseInsensitiveStrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONFIGURED = "CONFIGURED"
    PROVISIONING = "PROVISIONING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    DISCONNECTED = "DISCONNECTED"
    SUSPENDED = "SUSPENDED"
    FAILED = "FAILED"


class SipConnectionStatus(_CaseInsensitiveStrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONFIGURED = "CONFIGURED"
    PROVISIONING = "PROVISIONING"
    TESTING = "TESTING"
    READY = "READY"
    FAILED = "FAILED"
    DISABLED = "DISABLED"


class SipTransportProtocol(_CaseInsensitiveStrEnum):
    UDP = "UDP"
    TCP = "TCP"
    TLS = "TLS"
    WSS = "WSS"


class TransferMode(_CaseInsensitiveStrEnum):
    COLD = "COLD"
    WARM = "WARM"
    AGENT_TO_AGENT = "AGENT_TO_AGENT"
    AGENT = "AGENT_TO_AGENT"


class TransferLifecycleState(_CaseInsensitiveStrEnum):
    NONE = "NONE"
    REQUESTED = "REQUESTED"
    STARTING = "STARTING"
    RINGING_TARGET = "RINGING_TARGET"
    WHISPERING = "WHISPERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    FALLBACK_RETURNED = "FALLBACK_RETURNED"
    FALLBACK_RETRIED = "FALLBACK_RETRIED"
    FALLBACK_HUNGUP = "FALLBACK_HUNGUP"


class TransferFallbackAction(_CaseInsensitiveStrEnum):
    RETURN_TO_AGENT = "RETURN_TO_AGENT"
    RETRY = "RETRY"
    HANGUP = "HANGUP"


class MediaSessionState(_CaseInsensitiveStrEnum):
    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    STREAMING = "STREAMING"
    SPEAKING = "SPEAKING"
    LISTENING = "LISTENING"
    INTERRUPTED = "INTERRUPTED"
    FLUSHING = "FLUSHING"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"


class ProviderEventType(_CaseInsensitiveStrEnum):
    CALL_INITIATED = "call.initiated"
    CALL_RINGING = "call.ringing"
    CALL_ANSWERED = "call.answered"
    CALL_IN_PROGRESS = "call.in_progress"
    CALL_TRANSFERRED = "call.transferred"
    CALL_COMPLETED = "call.completed"
    CALL_BUSY = "call.busy"
    CALL_NO_ANSWER = "call.no_answer"
    CALL_FAILED = "call.failed"
    CALL_CANCELLED = "call.cancelled"
    DTMF_RECEIVED = "dtmf.received"
    MEDIA_CONNECTED = "media.connected"
    MEDIA_DISCONNECTED = "media.disconnected"
    RECORDING_AVAILABLE = "recording.available"
    VOICEMAIL_DETECTED = "voicemail.detected"


class InternalTelephonyEventType(_CaseInsensitiveStrEnum):
    CALL_CREATED = "CALL_CREATED"
    CALL_RINGING = "CALL_RINGING"
    CALL_ANSWERED = "CALL_ANSWERED"
    CALL_STARTED = "CALL_STARTED"
    CALL_MEDIA_STARTED = "CALL_MEDIA_STARTED"
    CALL_DTMF = "CALL_DTMF"
    CALL_TRANSFER_REQUESTED = "CALL_TRANSFER_REQUESTED"
    CALL_TRANSFER_STARTED = "CALL_TRANSFER_STARTED"
    CALL_TRANSFER_COMPLETED = "CALL_TRANSFER_COMPLETED"
    CALL_ENDED = "CALL_ENDED"
    CALL_FAILED = "CALL_FAILED"
    CALL_VOICEMAIL = "CALL_VOICEMAIL"


class TelephonyHealthState(_CaseInsensitiveStrEnum):
    READY = "READY"
    CONFIGURED = "CONFIGURED"
    DEGRADED = "DEGRADED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    FAILED = "FAILED"


class ExecutionEnvironmentKind(_CaseInsensitiveStrEnum):
    PRODUCTION = "PRODUCTION"
    TEST = "TEST"
    SIMULATION = "SIMULATION"
    FIXTURE = "FIXTURE"
