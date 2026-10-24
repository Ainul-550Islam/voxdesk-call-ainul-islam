"""JSON-Schema + Pydantic argument contracts for mid-call voice agent integration tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.auth.permissions import Permission


class LookupContactArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=32)

    @model_validator(mode="after")
    def _require_identifier(self) -> "LookupContactArgs":
        if not (self.email.strip() or self.phone.strip()):
            raise ValueError("Provide at least one of 'email' or 'phone'")
        return self


class UpsertContactArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=32)
    company: str = Field(default="", max_length=160)


class CrmLogCallArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(default="Voice Call", min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    duration_seconds: int = Field(default=60, ge=0, le=86400)
    direction: Literal["inbound", "outbound"] = "inbound"
    disposition: str = Field(default="completed", max_length=80)
    external_contact_id: str = Field(default="", max_length=160)


class CreateDealArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    amount: float = Field(default=0.0, ge=0.0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    stage: str = Field(default="", max_length=80)
    contact_id: str = Field(default="", max_length=120)


class CreateTicketArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: str = Field(min_length=1, max_length=240)
    description: str = Field(min_length=1, max_length=4000)
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    requester_name: str = Field(default="", max_length=160)
    requester_email: str = Field(default="", max_length=254)
    requester_phone: str = Field(default="", max_length=32)


class LookupTicketsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=32)
    query: str = Field(default="", max_length=200)
    limit: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def _require_filter(self) -> "LookupTicketsArgs":
        if not (self.email.strip() or self.phone.strip() or self.query.strip()):
            raise ValueError("Provide at least one of 'email', 'phone', or 'query'")
        return self


class AddTicketNoteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_id: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=4000)
    public: bool = False


class NotifyChannelArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel_id: str = Field(min_length=1, max_length=160)
    team_id: str = Field(default="", max_length=160)
    title: str = Field(default="Live Voice Escalation", max_length=150)
    message: str = Field(min_length=1, max_length=2800)
    caller_phone: str = Field(default="", max_length=32)


class ScheduleMeetingArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str = Field(min_length=1, max_length=200)
    start_time_iso: str = Field(default="", max_length=64)
    duration_minutes: int = Field(default=30, ge=5, le=240)
    lead_id: str = Field(default="", max_length=120)


@dataclass(frozen=True)
class AgentToolSpec:
    name: str
    canonical_action: str
    category: str
    description: str
    args_model: type[BaseModel]
    required_permission: Permission
    mutating: bool = False

    def to_mcp_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "mutating": self.mutating,
            "inputSchema": self.args_model.model_json_schema(),
        }


PRIMARY_TOOL_SPECS: dict[str, AgentToolSpec] = {
    "helpdesk_create_ticket": AgentToolSpec(
        name="helpdesk_create_ticket",
        canonical_action="helpdesk_create_ticket",
        category="helpdesk",
        description="Open a support ticket or ITSM incident on the tenant's configured helpdesk.",
        args_model=CreateTicketArgs,
        required_permission=Permission.INTEGRATION_WRITE,
        mutating=True,
    ),
    "helpdesk_lookup_customer": AgentToolSpec(
        name="helpdesk_lookup_customer",
        canonical_action="helpdesk_lookup_customer",
        category="helpdesk",
        description="Search open or recent helpdesk tickets and customer history by phone or email.",
        args_model=LookupTicketsArgs,
        required_permission=Permission.INTEGRATION_READ,
        mutating=False,
    ),
    "helpdesk_add_comment": AgentToolSpec(
        name="helpdesk_add_comment",
        canonical_action="helpdesk_add_comment",
        category="helpdesk",
        description="Append an internal note or public reply to an existing helpdesk ticket.",
        args_model=AddTicketNoteArgs,
        required_permission=Permission.INTEGRATION_WRITE,
        mutating=True,
    ),
    "crm_lookup_contact": AgentToolSpec(
        name="crm_lookup_contact",
        canonical_action="crm_lookup_contact",
        category="crm",
        description="Look up a CRM contact by caller email or phone number during a live call.",
        args_model=LookupContactArgs,
        required_permission=Permission.INTEGRATION_READ,
        mutating=False,
    ),
    "crm_log_call": AgentToolSpec(
        name="crm_log_call",
        canonical_action="crm_log_call",
        category="crm",
        description="Log a voice call outcome and summary against a CRM contact.",
        args_model=CrmLogCallArgs,
        required_permission=Permission.INTEGRATION_WRITE,
        mutating=True,
    ),
    "meeting_schedule": AgentToolSpec(
        name="meeting_schedule",
        canonical_action="meeting_schedule",
        category="collab",
        description="Schedule a Zoom or Microsoft Teams video meeting and return the join link.",
        args_model=ScheduleMeetingArgs,
        required_permission=Permission.INTEGRATION_WRITE,
        mutating=True,
    ),
    "notify_channel": AgentToolSpec(
        name="notify_channel",
        canonical_action="notify_channel",
        category="collab",
        description="Post an urgent escalation card or message to a Slack or Microsoft Teams channel.",
        args_model=NotifyChannelArgs,
        required_permission=Permission.INTEGRATION_WRITE,
        mutating=True,
    ),
}

DOTTED_TOOL_ALIASES: dict[str, AgentToolSpec] = {
    "helpdesk.create_ticket": AgentToolSpec(
        name="helpdesk.create_ticket",
        canonical_action="helpdesk_create_ticket",
        category="helpdesk",
        description=PRIMARY_TOOL_SPECS["helpdesk_create_ticket"].description,
        args_model=CreateTicketArgs,
        required_permission=Permission.INTEGRATION_WRITE,
        mutating=True,
    ),
    "helpdesk.lookup_tickets": AgentToolSpec(
        name="helpdesk.lookup_tickets",
        canonical_action="helpdesk_lookup_customer",
        category="helpdesk",
        description=PRIMARY_TOOL_SPECS["helpdesk_lookup_customer"].description,
        args_model=LookupTicketsArgs,
        required_permission=Permission.INTEGRATION_READ,
        mutating=False,
    ),
    "helpdesk.add_ticket_note": AgentToolSpec(
        name="helpdesk.add_ticket_note",
        canonical_action="helpdesk_add_comment",
        category="helpdesk",
        description=PRIMARY_TOOL_SPECS["helpdesk_add_comment"].description,
        args_model=AddTicketNoteArgs,
        required_permission=Permission.INTEGRATION_WRITE,
        mutating=True,
    ),
    "crm.lookup_contact": AgentToolSpec(
        name="crm.lookup_contact",
        canonical_action="crm_lookup_contact",
        category="crm",
        description=PRIMARY_TOOL_SPECS["crm_lookup_contact"].description,
        args_model=LookupContactArgs,
        required_permission=Permission.INTEGRATION_READ,
        mutating=False,
    ),
    "crm.upsert_contact": AgentToolSpec(
        name="crm.upsert_contact",
        canonical_action="crm_upsert_contact",
        category="crm",
        description="Create or update a CRM contact record for the active caller.",
        args_model=UpsertContactArgs,
        required_permission=Permission.INTEGRATION_WRITE,
        mutating=True,
    ),
    "crm.create_deal": AgentToolSpec(
        name="crm.create_deal",
        canonical_action="crm_create_deal",
        category="crm",
        description="Create a CRM deal or sales opportunity associated with a caller.",
        args_model=CreateDealArgs,
        required_permission=Permission.INTEGRATION_WRITE,
        mutating=True,
    ),
    "collab.schedule_meeting": AgentToolSpec(
        name="collab.schedule_meeting",
        canonical_action="meeting_schedule",
        category="collab",
        description=PRIMARY_TOOL_SPECS["meeting_schedule"].description,
        args_model=ScheduleMeetingArgs,
        required_permission=Permission.INTEGRATION_WRITE,
        mutating=True,
    ),
    "collab.notify_channel": AgentToolSpec(
        name="collab.notify_channel",
        canonical_action="notify_channel",
        category="collab",
        description=PRIMARY_TOOL_SPECS["notify_channel"].description,
        args_model=NotifyChannelArgs,
        required_permission=Permission.INTEGRATION_WRITE,
        mutating=True,
    ),
}

AGENT_TOOL_SPECS: dict[str, AgentToolSpec] = {
    **PRIMARY_TOOL_SPECS,
    **DOTTED_TOOL_ALIASES,
}


def resolve_tool_spec(name: str) -> AgentToolSpec | None:
    return AGENT_TOOL_SPECS.get(name)


def list_agent_tool_specs() -> list[dict[str, Any]]:
    return [spec.to_mcp_tool() for spec in PRIMARY_TOOL_SPECS.values()]
