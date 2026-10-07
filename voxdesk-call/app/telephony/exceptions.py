"""
app/telephony/exceptions.py
Typed telephony, provider, SIP, media, transfer, and runtime exceptions mapped to HTTP error responses.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError


class TelephonyRuntimeError(AppError):
    status_code = 400
    default_code = "TELEPHONY_RUNTIME_ERROR"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        if status_code is not None:
            self.status_code = status_code
        super().__init__(
            message,
            code=code or self.default_code,
            detail=detail,
        )


class TelephonyConfigurationError(TelephonyRuntimeError):
    status_code = 422
    default_code = "TELEPHONY_NOT_CONFIGURED"


class PhoneNumberValidationError(TelephonyRuntimeError):
    status_code = 422
    default_code = "PHONE_NUMBER_VALIDATION_ERROR"


class ProviderAuthenticationError(TelephonyRuntimeError):
    status_code = 502
    default_code = "PROVIDER_AUTHENTICATION_ERROR"


class ProviderRequestError(TelephonyRuntimeError):
    status_code = 502
    default_code = "PROVIDER_REQUEST_ERROR"


class ProviderWebhookVerificationError(TelephonyRuntimeError):
    status_code = 401
    default_code = "PROVIDER_WEBHOOK_VERIFICATION_ERROR"


class CallStateTransitionError(TelephonyRuntimeError):
    status_code = 409
    default_code = "CALL_STATE_TRANSITION_ERROR"


class CallSessionNotFoundError(TelephonyRuntimeError):
    status_code = 404
    default_code = "CALL_SESSION_NOT_FOUND"


class PhoneNumberNotFoundError(TelephonyRuntimeError):
    status_code = 404
    default_code = "PHONE_NUMBER_NOT_FOUND"


class SipConfigurationError(TelephonyRuntimeError):
    status_code = 422
    default_code = "SIP_CONFIGURATION_ERROR"


class MediaSessionError(TelephonyRuntimeError):
    status_code = 422
    default_code = "MEDIA_SESSION_ERROR"


class DtmfError(TelephonyRuntimeError):
    status_code = 422
    default_code = "DTMF_ERROR"


class TransferError(TelephonyRuntimeError):
    status_code = 422
    default_code = "TRANSFER_ERROR"


class TelephonyTimeoutError(TelephonyRuntimeError):
    status_code = 504
    default_code = "TELEPHONY_TIMEOUT_ERROR"


class TelephonyAuthorizationError(TelephonyRuntimeError):
    status_code = 403
    default_code = "TELEPHONY_AUTHORIZATION_ERROR"
