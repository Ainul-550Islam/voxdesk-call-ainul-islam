"""Pydantic schemas for multi-channel messaging adapters and routes (Capability 11)."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class MessagingChannelType(str, Enum):
    SMS = "sms"
    WHATSAPP = "whatsapp"


class MessagingProviderName(str, Enum):
    TWILIO = "twilio"
    TELNYX = "telnyx"
    VONAGE = "vonage"


class KeywordDisposition(str, Enum):
    STOP = "STOP"
    START = "START"
    HELP = "HELP"
    NONE = "NONE"


class OutboundMessagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_type: str = Field(..., min_length=1, max_length=32)
    provider: Optional[str] = Field(default=None, max_length=32)
    to: str = Field(..., min_length=3, max_length=200)
    from_number: Optional[str] = Field(default=None, max_length=200)
    message: str = Field(..., min_length=1, max_length=4000)
    media_urls: list[str] = Field(default_factory=list)
    template_sid: Optional[str] = Field(default=None, max_length=128)
    template_variables: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class OutboundMessageResult(BaseModel):
    provider: str
    channel_type: str
    provider_message_id: str
    status: str
    to_address: str
    from_address: str
    raw_status: Optional[str] = None


class InboundMessageEvent(BaseModel):
    provider: str
    channel_type: str
    provider_message_id: str
    from_address: str
    to_address: str
    from_e164: str
    to_e164: str
    body: str
    media_urls: list[str] = Field(default_factory=list)
    keyword: KeywordDisposition = KeywordDisposition.NONE
    raw_event_type: str = "message.received"


class ChannelHealthStatus(BaseModel):
    provider: str
    channel_type: str
    configured: bool
    status: str
    reason: str
    missing_credentials: list[str] = Field(default_factory=list)


class ChannelAdapterInfo(BaseModel):
    provider: str
    channel_type: str
    configured: bool
    supports_templates: bool = False
    supports_media: bool = False
    webhook_signature_scheme: str
