"""Canonical Helpdesk models and abstract ``HelpdeskProviderV2`` contract."""

from __future__ import annotations

import abc
import enum
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from pydantic import BaseModel, ConfigDict, Field

from app.integrations_v2.core.pagination import PageBatch
from app.integrations_v2.core.provider_base import ProviderV2
from app.integrations_v2.core.sync_engine import ExternalSyncRecord


class TicketPriority(str, enum.Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TicketStatus(str, enum.Enum):
    NEW = "new"
    OPEN = "open"
    PENDING = "pending"
    RESOLVED = "resolved"
    CLOSED = "closed"


@dataclass(frozen=True)
class HelpdeskUser:
    external_id: str
    name: str
    email: str = ""
    phone: str = ""


@dataclass(frozen=True)
class HelpdeskTicket:
    external_id: str
    provider: str
    subject: str
    description: str = ""
    status: TicketStatus = TicketStatus.OPEN
    priority: TicketPriority = TicketPriority.NORMAL
    requester_id: str = ""
    requester_email: str = ""
    requester_phone: str = ""
    url: str = ""
    updated_at: str = ""
    tags: list[str] = field(default_factory=list)
    custom_fields: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "provider": self.provider,
            "subject": self.subject,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "requester_id": self.requester_id,
            "requester_email": self.requester_email,
            "requester_phone": self.requester_phone,
            "url": self.url,
            "updated_at": self.updated_at,
            "tags": list(self.tags),
            "custom_fields": dict(self.custom_fields),
        }


@dataclass(frozen=True)
class HelpdeskTicketComment:
    external_id: str
    ticket_id: str
    body: str
    public: bool = False


class AttachmentRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="text/plain", max_length=120)
    signed_url: str = Field(default="", max_length=2048)
    size_bytes: int = Field(default=0, ge=0)


class CallLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: str = Field(min_length=1, max_length=120)
    direction: str = Field(default="inbound", max_length=24)
    duration_seconds: int = Field(default=0, ge=0)
    caller_phone: str = Field(default="", max_length=32)
    caller_email: str = Field(default="", max_length=254)
    summary: str = Field(default="", max_length=6000)
    recording_url: str = Field(default="", max_length=2048)
    attachments: list[AttachmentRef] = Field(default_factory=list)


Ticket = HelpdeskTicket
TicketComment = HelpdeskTicketComment
Requester = HelpdeskUser
TicketRecord = HelpdeskTicket
TicketCommentRecord = HelpdeskTicketComment


@dataclass(frozen=True)
class TicketCreateInput:
    subject: str
    description: str
    priority: TicketPriority = TicketPriority.NORMAL
    status: TicketStatus = TicketStatus.OPEN
    requester_name: str = ""
    requester_email: str = ""
    requester_phone: str = ""
    tags: list[str] = field(default_factory=list)
    custom_fields: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""


@dataclass(frozen=True)
class TicketUpdateInput:
    subject: str | None = None
    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    comment_body: str | None = None
    comment_public: bool = False
    tags: list[str] | None = None
    custom_fields: dict[str, Any] | None = None


class HelpdeskProviderV2(ProviderV2, abc.ABC):
    """Unified async interface implemented by Zendesk, ServiceNow, Freshdesk, and Zoho Desk."""

    category = "helpdesk"

    @abc.abstractmethod
    async def find_or_create_requester(
        self, *, name: str, email: str = "", phone: str = ""
    ) -> HelpdeskUser:
        """Locate an existing requester by email/phone or create one."""

    @abc.abstractmethod
    async def create_ticket(self, data: TicketCreateInput) -> HelpdeskTicket:
        """Create a ticket/incident on the external helpdesk."""

    @abc.abstractmethod
    async def get_ticket(self, ticket_id: str) -> HelpdeskTicket:
        """Fetch a single ticket/incident by external ID."""

    @abc.abstractmethod
    async def update_ticket(
        self, ticket_id: str, data: TicketUpdateInput
    ) -> HelpdeskTicket:
        """Update status, priority, fields, or append a note on an existing ticket."""

    @abc.abstractmethod
    async def add_comment(
        self, ticket_id: str, *, body: str, public: bool = False
    ) -> HelpdeskTicketComment:
        """Append an internal note or public reply to an existing ticket."""

    @abc.abstractmethod
    async def search_tickets(
        self, *, email: str = "", phone: str = "", query: str = "", limit: int = 10
    ) -> list[HelpdeskTicket]:
        """Search tickets by caller phone/email or free-text query."""

    @abc.abstractmethod
    async def log_voice_call(self, payload: dict[str, Any]) -> str:
        """Record a completed voice call (duration, summary, signed recording URL) and return external ID."""

    @abc.abstractmethod
    def iter_updated_tickets(
        self, cursor: str | None = None
    ) -> AsyncIterator[PageBatch[ExternalSyncRecord]]:
        """Yield cursor-checkpointed pages of updated tickets for incremental sync."""
