"""
app/telephony/__init__.py
Public exports for Prompt 6 — Telephony / Voice Runtime.
Uses lazy attribute resolution for DB-backed runtime services to prevent circular imports.
"""

from __future__ import annotations

from app.telephony.enums import (
    ACTIVE_CALL_STATES,
    TERMINAL_CALL_STATES,
    CallDirection,
    ExecutionEnvironmentKind,
    InternalTelephonyEventType,
    MediaSessionState,
    PhoneNumberLifecycleStatus,
    ProviderEventType,
    SipConnectionStatus,
    SipTransportProtocol,
    TelephonyCallState,
    TelephonyHealthState,
    TelephonyProviderName,
    TransferFallbackAction,
    TransferLifecycleState,
    TransferMode,
)
from app.telephony.exceptions import (
    CallSessionNotFoundError,
    CallStateTransitionError,
    DtmfError,
    MediaSessionError,
    PhoneNumberNotFoundError,
    PhoneNumberValidationError,
    ProviderAuthenticationError,
    ProviderRequestError,
    ProviderWebhookVerificationError,
    SipConfigurationError,
    TelephonyAuthorizationError,
    TelephonyConfigurationError,
    TelephonyRuntimeError,
    TelephonyTimeoutError,
    TransferError,
)

__all__ = [
    "ACTIVE_CALL_STATES",
    "TERMINAL_CALL_STATES",
    "CallDirection",
    "CallSessionManager",
    "CallSessionNotFoundError",
    "CallStateTransitionError",
    "CallTransferService",
    "DtmfError",
    "ExecutionEnvironmentKind",
    "InternalTelephonyEventType",
    "MediaGatewayManager",
    "MediaSessionError",
    "MediaSessionState",
    "PhoneNumberLifecycleStatus",
    "PhoneNumberNotFoundError",
    "PhoneNumberService",
    "PhoneNumberValidationError",
    "ProviderAuthenticationError",
    "ProviderEventType",
    "ProviderRequestError",
    "ProviderWebhookVerificationError",
    "RealtimeVoiceSessionOrchestrator",
    "SipConfigurationError",
    "SipConnectionService",
    "SipConnectionStatus",
    "SipTransportProtocol",
    "TelephonyAuthorizationError",
    "TelephonyCallState",
    "TelephonyConfigurationError",
    "TelephonyHealthState",
    "TelephonyProviderName",
    "TelephonyRuntimeError",
    "TelephonyRuntimeService",
    "TelephonyTimeoutError",
    "TelephonyWebhookProcessor",
    "TransferError",
    "TransferFallbackAction",
    "TransferLifecycleState",
    "TransferMode",
    "media_gateway_manager",
]


def __getattr__(name: str):
    if name == "PhoneNumberService":
        from app.telephony.phone_numbers import PhoneNumberService

        return PhoneNumberService
    if name == "SipConnectionService":
        from app.telephony.sip import SipConnectionService

        return SipConnectionService
    if name == "CallSessionManager":
        from app.telephony.call_session import CallSessionManager

        return CallSessionManager
    if name == "CallTransferService":
        from app.telephony.transfer import CallTransferService

        return CallTransferService
    if name in {"MediaGatewayManager", "media_gateway_manager"}:
        from app.telephony import media_gateway

        return getattr(media_gateway, name)
    if name == "RealtimeVoiceSessionOrchestrator":
        from app.telephony.realtime import RealtimeVoiceSessionOrchestrator

        return RealtimeVoiceSessionOrchestrator
    if name == "TelephonyWebhookProcessor":
        from app.telephony.webhooks import TelephonyWebhookProcessor

        return TelephonyWebhookProcessor
    if name == "TelephonyRuntimeService":
        from app.telephony.runtime import TelephonyRuntimeService

        return TelephonyRuntimeService
    raise AttributeError(f"module 'app.telephony' has no attribute {name!r}")
