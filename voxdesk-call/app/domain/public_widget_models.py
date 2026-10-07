"""Prompt 5: Public Key, Origin Policy, and Embeddable Web Widget domain models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.public_site_models import PublicDomainError as PublicDomainError

__all_errors__ = ["PublicDomainError"]


class PublicWidgetKeyStatus(str, Enum):
    ACTIVE = "active"
    ROTATED = "rotated"
    REVOKED = "revoked"
    EXPIRED = "expired"


class PublicWidgetCapability(str, Enum):
    WIDGET_CONFIG_READ = "widget_config_read"
    WIDGET_SESSION_CREATE = "widget_session_create"
    WIDGET_CHAT_SEND = "widget_chat_send"
    WIDGET_VOICE_START = "widget_voice_start"
    WIDGET_SESSION_END = "widget_session_end"


DEFAULT_PUBLIC_WIDGET_CAPABILITIES: tuple[str, ...] = (
    PublicWidgetCapability.WIDGET_CONFIG_READ.value,
    PublicWidgetCapability.WIDGET_SESSION_CREATE.value,
    PublicWidgetCapability.WIDGET_CHAT_SEND.value,
    PublicWidgetCapability.WIDGET_VOICE_START.value,
    PublicWidgetCapability.WIDGET_SESSION_END.value,
)

FORBIDDEN_PUBLIC_KEY_CAPABILITIES: frozenset[str] = frozenset(
    {
        "agent_edit",
        "agent_publish",
        "agent_delete",
        "tenant_admin",
        "tenant_update",
        "billing_admin",
        "billing_write",
        "raw_tool_execution",
        "connector_secret_access",
        "full_call_history_export",
        "conductor_apply",
        "user_manage",
        "api_key_manage",
        "*",
    }
)


class PublicWidgetSessionMode(str, Enum):
    CHAT = "chat"
    VOICE = "voice"


class PublicWidgetSessionStatus(str, Enum):
    READY = "ready"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    COMPLETED = "completed"
    EXPIRED = "expired"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"
    FORBIDDEN_ORIGIN = "forbidden_origin"
    INVALID_PUBLIC_KEY = "invalid_public_key"


class PublicWidgetAppearanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="Talk with our AI Assistant", max_length=120)
    subtitle: str = Field(
        default="Ask a question or start a live voice session", max_length=200
    )
    greeting: str = Field(
        default="Hello! How can I help you today?", max_length=500
    )
    placeholder: str = Field(default="Type your message...", max_length=160)
    primary_color: str = Field(default="#2563eb", max_length=32)
    position: str = Field(default="bottom-right", max_length=32)
    enable_chat: bool = True
    enable_voice: bool = True
    show_branding: bool = True


class PublicWidgetKeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., min_length=1, max_length=120)
    agent_kind: str = Field(default="voice", pattern="^(voice|chat)$")
    name: str = Field(..., min_length=2, max_length=200)
    environment_name: str = Field(default="production", min_length=2, max_length=64)
    allowed_origins: list[str] = Field(default_factory=list, max_length=50)
    allowed_capabilities: list[str] = Field(
        default_factory=lambda: list(DEFAULT_PUBLIC_WIDGET_CAPABILITIES)
    )
    rate_limit_per_minute: int = Field(default=30, ge=1, le=600)
    session_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    require_published_agent: bool = True
    expires_in_days: int | None = Field(default=None, ge=1, le=365)
    widget_config: PublicWidgetAppearanceConfig = Field(
        default_factory=PublicWidgetAppearanceConfig
    )


class PublicWidgetKeyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=2, max_length=200)
    allowed_origins: list[str] | None = Field(default=None, max_length=50)
    allowed_capabilities: list[str] | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=600)
    session_ttl_seconds: int | None = Field(default=None, ge=60, le=3600)
    require_published_agent: bool | None = None
    widget_config: PublicWidgetAppearanceConfig | None = None


class PublicWidgetKeyRotateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="routine_rotation", max_length=255)
    allowed_origins: list[str] | None = Field(default=None, max_length=50)
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class PublicWidgetKeyRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="manual_revocation", max_length=255)


class PublicWidgetKeyRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    environment_id: str | None = None
    environment_name: str
    agent_id: str
    agent_kind: str
    name: str
    key_prefix: str
    status: PublicWidgetKeyStatus
    allowed_origins: list[str] = Field(default_factory=list)
    allowed_capabilities: list[str] = Field(default_factory=list)
    rate_limit_per_minute: int
    session_ttl_seconds: int
    require_published_agent: bool
    widget_config: PublicWidgetAppearanceConfig
    rotated_from_key_id: str | None = None
    rotated_to_key_id: str | None = None
    created_by: str | None = None
    revoked_by: str | None = None
    revoke_reason: str | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    rotated_at: datetime | None = None
    last_used_at: datetime | None = None
    last_used_origin: str | None = None
    created_at: datetime
    updated_at: datetime
    embed_snippet: str


class PublicWidgetKeyCreatedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: PublicWidgetKeyRead
    public_key: str
    warning: str = (
        "Copy this public key now. For security, only its prefix and SHA-256 hash are stored."
    )


class PublicWidgetBootstrapConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_key_prefix: str
    agent_id: str
    agent_name: str
    agent_kind: str
    published_version_number: int
    environment_name: str
    allowed_capabilities: list[str] = Field(default_factory=list)
    appearance: PublicWidgetAppearanceConfig
    voice_transport_configured: bool
    voice_transport_status: str
    voice_transport_message: str | None = None
    session_ttl_seconds: int


class PublicWidgetSessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_key: str = Field(..., min_length=12, max_length=256)
    mode: PublicWidgetSessionMode = PublicWidgetSessionMode.CHAT
    visitor_id: str | None = Field(default=None, max_length=120)
    origin: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PublicWidgetTurnRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str
    timestamp: str
    turn_index: int


class PublicWidgetSessionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    status: PublicWidgetSessionStatus
    mode: PublicWidgetSessionMode
    agent_id: str
    agent_name: str
    agent_kind: str
    agent_version_number: int
    transport: str
    session_token: str | None = None
    expires_at: datetime
    turns_count: int = 0
    max_turns: int = 30
    transcript: list[PublicWidgetTurnRecord] = Field(default_factory=list)
    appearance: PublicWidgetAppearanceConfig
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    ended_at: datetime | None = None


class PublicWidgetMessageSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(..., min_length=1, max_length=2000)
    dynamic_variables: dict[str, Any] = Field(default_factory=dict)


class PublicWidgetMessageSendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    status: PublicWidgetSessionStatus
    turn_index: int
    user_turn: PublicWidgetTurnRecord
    assistant_turn: PublicWidgetTurnRecord
    turns_count: int
    max_turns: int
    remaining_turns: int
    transcript: list[PublicWidgetTurnRecord] = Field(default_factory=list)


class PublicWidgetEndSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="visitor_ended", max_length=120)


class OriginValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: bool
    normalized_origin: str | None = None
    matched_origin: str | None = None
    reason: str
    error_code: str | None = None
