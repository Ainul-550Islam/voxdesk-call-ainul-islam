"""
app/telephony/schemas.py
Pydantic v2 request/response schemas for Phone Numbers, SIP Connections,
Call Sessions, Outbound Calls, Transfers, DTMF, Media Gateway, Webhooks, and Health.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.telephony.enums import (
    CallDirection,
    MediaSessionState as MediaSessionState,
    PhoneNumberLifecycleStatus,
    ProviderEventType,
    SipConnectionStatus as SipConnectionStatus,
    SipTransportProtocol,
    TelephonyCallState,
    TelephonyHealthState,
    TelephonyProviderName,
    TransferFallbackAction,
    TransferLifecycleState as TransferLifecycleState,
    TransferMode,
)
from app.telephony.exceptions import PhoneNumberValidationError, SipConfigurationError
from app.telephony.phone import InvalidPhoneNumber, looks_like_endpoint, normalize

_SIP_URI_RE = re.compile(
    r"^sips?:[A-Za-z0-9._%+-]+(?:@[A-Za-z0-9.-]+)?(?::\d{1,5})?(?:;[A-Za-z0-9._=-]+)*$"
    r"|^sips?:[A-Za-z0-9.-]+(?::\d{1,5})?(?:;[A-Za-z0-9._=-]+)*$",
    re.IGNORECASE,
)


def coerce_e164(raw: str, *, field_name: str = "number") -> str:
    try:
        cleaned = normalize(raw)
        if looks_like_endpoint(cleaned):
            raise InvalidPhoneNumber("must be an E.164 phone number starting with '+'")
        return cleaned
    except InvalidPhoneNumber as exc:
        raise PhoneNumberValidationError(
            f"Invalid E.164 phone number for '{field_name}': {exc}",
            detail={"field": field_name, "value": raw},
        ) from exc


def validate_sip_uri(raw: str, *, field_name: str = "termination_uri") -> str:
    cleaned = (raw or "").strip()
    if not cleaned:
        raise SipConfigurationError(
            f"SIP URI '{field_name}' cannot be empty",
            detail={"field": field_name},
        )
    if not cleaned.lower().startswith(("sip:", "sips:")):
        cleaned = f"sip:{cleaned}"
    if not _SIP_URI_RE.match(cleaned) or ".." in cleaned or " " in cleaned:
        raise SipConfigurationError(
            f"Invalid SIP URI format for '{field_name}': {raw!r}",
            detail={"field": field_name, "value": raw},
        )
    return cleaned


class StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Phone Number Schemas
# ---------------------------------------------------------------------------


class PhoneNumberCreateRequest(StrictRequestModel):
    number: str = Field(..., min_length=4, max_length=32)
    provider: TelephonyProviderName = Field(default=TelephonyProviderName.TWILIO)
    provider_number_id: str | None = Field(default=None, max_length=96)
    sip_connection_id: UUID | None = None
    sip_enabled: bool = False
    inbound_enabled: bool = True
    outbound_enabled: bool = True
    inbound_agent_id: str | None = Field(default=None, max_length=64)
    inbound_agent_version: int | None = None
    outbound_agent_id: str | None = Field(default=None, max_length=64)
    environment_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("number")
    @classmethod
    def _validate_number(cls, v: str) -> str:
        return coerce_e164(v, field_name="number")


class PhoneNumberUpdateRequest(StrictRequestModel):
    provider: TelephonyProviderName | None = None
    provider_number_id: str | None = Field(default=None, max_length=96)
    sip_connection_id: UUID | None = None
    sip_enabled: bool | None = None
    inbound_enabled: bool | None = None
    outbound_enabled: bool | None = None
    inbound_agent_id: str | None = Field(default=None, max_length=64)
    inbound_agent_version: int | None = None
    outbound_agent_id: str | None = Field(default=None, max_length=64)
    status: PhoneNumberLifecycleStatus | None = None
    metadata: dict[str, Any] | None = None


class PhoneNumberBindAgentRequest(StrictRequestModel):
    inbound_agent_id: str | None = Field(default=None, max_length=64)
    inbound_agent_version: int | None = None
    outbound_agent_id: str | None = Field(default=None, max_length=64)
    inbound_enabled: bool | None = None
    outbound_enabled: bool | None = None


class PhoneNumberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    environment_id: UUID | None = None
    number: str
    e164_number: str
    provider: str
    provider_number_id: str | None = None
    sip_connection_id: UUID | None = None
    sip_enabled: bool
    inbound_enabled: bool
    outbound_enabled: bool
    inbound_agent_id: str | None = None
    inbound_agent_version: int | None = None
    outbound_agent_id: str | None = None
    status: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    last_health_check_at: datetime | None = None
    last_health_error: str | None = None
    created_at: datetime
    updated_at: datetime


class PhoneNumberListResponse(BaseModel):
    items: list[PhoneNumberResponse]
    total: int


# ---------------------------------------------------------------------------
# SIP Connection Schemas
# ---------------------------------------------------------------------------


class SipConnectionCreateRequest(StrictRequestModel):
    name: str = Field(default="Primary SIP Trunk", min_length=1, max_length=120)
    provider: TelephonyProviderName = Field(default=TelephonyProviderName.SIP)
    phone_number: str | None = Field(default=None, max_length=32)
    termination_uri: str = Field(..., min_length=4, max_length=255)
    origination_uri: str | None = Field(default=None, max_length=255)
    username: str | None = Field(default=None, max_length=128)
    password_secret: str | None = Field(default=None, max_length=256)
    credential_reference: str | None = Field(default=None, max_length=128)
    transport: SipTransportProtocol = Field(default=SipTransportProtocol.TLS)
    environment_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("phone_number")
    @classmethod
    def _validate_optional_phone(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return coerce_e164(v, field_name="phone_number")

    @field_validator("termination_uri")
    @classmethod
    def _validate_termination_uri(cls, v: str) -> str:
        return validate_sip_uri(v, field_name="termination_uri")

    @field_validator("origination_uri")
    @classmethod
    def _validate_origination_uri(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return validate_sip_uri(v, field_name="origination_uri")


class SipConnectionTestRequest(StrictRequestModel):
    termination_uri: str | None = Field(default=None, max_length=255)
    origination_uri: str | None = Field(default=None, max_length=255)
    transport: SipTransportProtocol | None = None
    simulate_unreachable: bool = False


class SipConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    environment_id: UUID | None = None
    name: str
    provider: str
    phone_number_e164: str | None = None
    termination_uri: str
    origination_uri: str | None = None
    username: str | None = None
    credential_reference: str | None = None
    has_credentials: bool = False
    transport: str
    status: str
    last_test_at: datetime | None = None
    last_error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class SipConnectionListResponse(BaseModel):
    items: list[SipConnectionResponse]
    total: int


# ---------------------------------------------------------------------------
# Call Session & Outbound Call Schemas
# ---------------------------------------------------------------------------


class OutboundCallCreateRequest(StrictRequestModel):
    to_number: str = Field(..., min_length=4, max_length=32)
    from_number: str | None = Field(default=None, max_length=32)
    phone_number_id: UUID | None = None
    agent_id: str | None = Field(default=None, max_length=64)
    agent_version_number: int | None = Field(
        default=None,
        ge=1,
        description="Optional immutable published/superseded AgentVersion pin.",
    )
    provider: TelephonyProviderName | None = None
    environment_id: UUID | None = None
    idempotency_key: str | None = Field(default=None, max_length=96)
    is_simulation: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("to_number")
    @classmethod
    def _validate_to_number(cls, v: str) -> str:
        return coerce_e164(v, field_name="to_number")

    @field_validator("from_number")
    @classmethod
    def _validate_from_number(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        return coerce_e164(v, field_name="from_number")


class CallHangupRequest(StrictRequestModel):
    reason: str = Field(default="operator_hangup", min_length=1, max_length=120)
    idempotency_key: str | None = Field(default=None, max_length=96)


class CallDtmfRequest(StrictRequestModel):
    digits: str = Field(..., min_length=1, max_length=32)
    source: str = Field(default="caller", max_length=32)
    idempotency_key: str | None = Field(default=None, max_length=96)


class CallTransferRequest(StrictRequestModel):
    mode: TransferMode = Field(default=TransferMode.COLD)
    target_destination: str | None = Field(default=None, max_length=128)
    target_agent_id: str | None = Field(default=None, max_length=64)
    whisper_message: str | None = Field(default=None, max_length=500)
    fallback_action: TransferFallbackAction = Field(
        default=TransferFallbackAction.RETURN_TO_AGENT
    )
    reason: str | None = Field(default="customer_requested_transfer", max_length=400)
    idempotency_key: str | None = Field(default=None, max_length=96)
    simulate_target_failure: bool = False
    context_overrides: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_transfer_target(self) -> "CallTransferRequest":
        if self.mode == TransferMode.AGENT_TO_AGENT:
            if not self.target_agent_id and not self.target_destination:
                raise ValueError(
                    "target_agent_id or target_destination is required for AGENT_TO_AGENT transfer"
                )
        else:
            if not self.target_destination and not self.target_agent_id:
                raise ValueError("target_destination is required for COLD or WARM transfer")
        return self


class CallTranscriptTurnSchema(BaseModel):
    turn_index: int
    role: str
    content: str
    timestamp: str
    agent_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CallRuntimeEventSchema(BaseModel):
    event_id: str
    event_type: str
    state: str
    timestamp: str
    detail: dict[str, Any] = Field(default_factory=dict)


class CallTransferResponse(BaseModel):
    id: UUID
    call_session_id: UUID
    organization_id: UUID
    transfer_mode: str
    source_agent_id: str | None = None
    target_agent_id: str | None = None
    target_destination: str
    whisper_message: str | None = None
    fallback_action: str
    status: str
    reason: str | None = None
    failure_reason: str | None = None
    context_snapshot: dict[str, Any] = Field(default_factory=dict)
    requested_at: datetime
    completed_at: datetime | None = None


class CallSessionResponse(BaseModel):
    id: UUID
    organization_id: UUID
    environment_id: UUID | None = None
    legacy_call_id: UUID | None = None
    phone_number_id: UUID | None = None
    agent_id: str | None = None
    agent_version_number: int | None = None
    provider: str
    provider_call_id: str
    direction: str
    from_number: str
    to_number: str
    status: str
    media_state: str
    transfer_state: str
    transfer_mode: str | None = None
    transfer_target: str | None = None
    transferred_to_agent_id: str | None = None
    parent_call_id: UUID | None = None
    is_simulation: bool
    execution_kind: str
    started_at: datetime | None = None
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: int
    billable_seconds: int
    usage_finalized: bool
    hangup_reason: str | None = None
    recording_reference: str | None = None
    transcript_reference: str | None = None
    transcript_turns: list[dict[str, Any]] = Field(default_factory=list)
    dtmf_buffer: str = ""
    dtmf_events: list[dict[str, Any]] = Field(default_factory=list)
    runtime_events: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class CallSessionListResponse(BaseModel):
    items: list[CallSessionResponse]
    total: int


class CallDtmfResponse(BaseModel):
    call_id: UUID
    accepted_digits: str
    dtmf_buffer: str
    matched_route: dict[str, Any] | None = None
    barge_in_triggered: bool = False
    status: str


# ---------------------------------------------------------------------------
# Provider Webhook Normalization & Health Schemas
# ---------------------------------------------------------------------------


class NormalizedProviderWebhookEvent(BaseModel):
    provider: TelephonyProviderName
    provider_event_id: str
    event_type: ProviderEventType
    provider_call_id: str
    from_number: str | None = None
    to_number: str | None = None
    direction: CallDirection = CallDirection.INBOUND
    call_state: TelephonyCallState | None = None
    dtmf_digits: str | None = None
    hangup_reason: str | None = None
    duration_seconds: int | None = None
    recording_url: str | None = None
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class WebhookProcessResult(BaseModel):
    accepted: bool
    duplicate: bool = False
    provider: str
    provider_event_id: str
    event_type: str
    call_session_id: UUID | None = None
    call_state: str | None = None
    detail: str = "processed"


class ProviderRuntimeStatus(BaseModel):
    provider: str
    configured: bool
    state: str
    has_credentials: bool
    webhook_verification_enabled: bool
    supported_capabilities: list[str] = Field(default_factory=list)
    message: str


class TelephonyHealthStatusResponse(BaseModel):
    state: TelephonyHealthState
    default_provider: str
    configured_phone_numbers: int
    ready_phone_numbers: int
    configured_sip_connections: int
    ready_sip_connections: int
    active_calls: int
    providers: list[ProviderRuntimeStatus]
    checked_at: datetime
