"""Abstract base contract, capability matrix, and honest verification status for Integrations v2."""

from __future__ import annotations

import abc
import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class AuthKind(str, enum.Enum):
    OAUTH2 = "oauth2"
    API_KEY = "api_key"
    OAUTH2_OR_API_KEY = "oauth2_or_api_key"
    SERVER_TO_SERVER_OAUTH = "s2s_oauth"


class Capability(str, enum.Enum):
    CONTACTS_READ = "contacts_read"
    CONTACTS_WRITE = "contacts_write"
    TICKETS_READ = "tickets_read"
    TICKETS_WRITE = "tickets_write"
    TICKET_COMMENT = "ticket_comment"
    CALL_LOG_WRITE = "call_log_write"
    MEETINGS_WRITE = "meetings_write"
    MESSAGES_WRITE = "messages_write"
    WEBHOOKS_INBOUND = "webhooks_inbound"
    INCREMENTAL_SYNC = "incremental_sync"
    LEAD_READ = "lead_read"
    LEAD_WRITE = "lead_write"
    DEAL_READ = "deal_read"
    DEAL_WRITE = "deal_write"
    SLASH_COMMAND = "slash_command"
    INTERACTIVE_ACTIONS = "interactive_actions"
    MID_CALL_TOOLS = "mid_call_tools"

    # Aliases keeping singular/legacy capability references working cleanly
    CONTACT_READ = "contacts_read"
    CONTACT_WRITE = "contacts_write"
    TICKET_READ = "tickets_read"
    TICKET_WRITE = "tickets_write"
    VOICE_CALL_LOG = "call_log_write"
    CALL_ACTIVITY_WRITE = "call_log_write"
    CHANNEL_MESSAGE = "messages_write"
    MEETING_CREATE = "meetings_write"
    WEBHOOK_INBOUND = "webhooks_inbound"


class VerificationStatus(str, enum.Enum):
    """Honest verification status reported in the integration matrix."""

    NOT_CONFIGURED = "NOT_CONFIGURED"
    PROTOCOL_TESTED = "PROTOCOL_TESTED"
    LIVE_VERIFIED = "LIVE_VERIFIED"


@dataclass(frozen=True)
class HealthStatus:
    ok: bool
    provider: str
    latency_ms: float
    account_id: str = ""
    account_name: str = ""
    detail: str = ""
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "latency_ms": round(self.latency_ms, 2),
            "account_id": self.account_id,
            "account_name": self.account_name,
            "detail": self.detail,
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass
class ConnectionRuntimeContext:
    """Decrypted, in-memory-only credentials + metadata for one provider call."""

    tenant_id: str
    connection_id: str
    provider: str
    auth_kind: str = "oauth2"
    instance_url: str = ""
    region: str = "us"
    external_account_id: str = "default"
    scopes: tuple[str, ...] = ()
    refresh_token: str = field(default="", repr=False)
    webhook_secret: str = field(default="", repr=False)
    credentials: dict[str, Any] = field(default_factory=dict, repr=False)
    config: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"ConnectionRuntimeContext(tenant_id={self.tenant_id!r}, "
            f"connection_id={self.connection_id!r}, provider={self.provider!r})"
        )


class ProviderV2(abc.ABC):
    """Base protocol implemented by all 11 Integrations v2 vendor adapters."""

    slug: str = ""
    display_name: str = ""
    category: str = ""
    auth_kind: AuthKind = AuthKind.OAUTH2
    capabilities: frozenset[Capability] = frozenset()
    default_scopes: tuple[str, ...] = ()
    docs_url: str = ""
    required_config_fields: tuple[str, ...] = ()

    def __init__(self, context: ConnectionRuntimeContext, http_client: Any = None) -> None:
        self.context = context
        self._http_client = http_client

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def declared_capabilities(self) -> set[Capability]:
        return set(self.capabilities)

    async def connect(self) -> HealthStatus:
        """Verify credentials on initial connect via ``health()``."""
        return await self.health()

    async def refresh(self) -> dict[str, Any]:
        """Return active credential metadata (OAuth2 token refresh is coordinated by ``core.oauth2``)."""
        return {"provider": self.slug, "connection_id": self.context.connection_id}

    async def disconnect(self) -> bool:
        """Clear in-memory runtime credentials upon disconnect."""
        self.context.credentials.clear()
        return True

    @abc.abstractmethod
    async def health(self) -> HealthStatus:
        """Perform an authenticated read against the vendor identity/account endpoint."""

    @classmethod
    def descriptor(cls, *, env_configured: bool, verification: VerificationStatus) -> dict[str, Any]:
        return {
            "provider": cls.slug,
            "display_name": cls.display_name,
            "category": cls.category,
            "auth_kind": cls.auth_kind.value,
            "capabilities": sorted({cap.value for cap in cls.capabilities}),
            "default_scopes": list(cls.default_scopes),
            "required_config_fields": list(cls.required_config_fields),
            "env_configured": env_configured,
            "verification_status": verification.value,
            "docs_url": cls.docs_url,
        }
