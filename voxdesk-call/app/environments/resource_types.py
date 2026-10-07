"""Canonical names for business resources that carry an environment id.

The set is closed. Billing, SSO, API keys, service accounts and provider
credentials are intentionally absent.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class EnvironmentResourceType(str, enum.Enum):
    CALL = "call"
    LEAD = "lead"
    APPOINTMENT = "appointment"
    KNOWLEDGE_DOCUMENT = "knowledge_document"
    AUTOMATION = "automation"
    NOTIFICATION = "notification"
    INBOX = "inbox"
    USAGE_EVENT = "usage_event"


@dataclass(frozen=True)
class ResourceTypeSpec:
    type: EnvironmentResourceType
    model_name: str
    table_name: str
    tenant_scoped: bool
    environment_scoped: bool
    supports_create: bool
    supports_update: bool
    supports_archive: bool
    legacy_default_environment_allowed: bool
    id_kind: str
    read_permission: str
    write_permission: str


RESOURCE_TYPES: dict[EnvironmentResourceType, ResourceTypeSpec] = {
    EnvironmentResourceType.CALL: ResourceTypeSpec(
        EnvironmentResourceType.CALL, "Call", "calls", True, True,
        False, True, False, True, "uuid", "call:read", "call:read",
    ),
    EnvironmentResourceType.LEAD: ResourceTypeSpec(
        EnvironmentResourceType.LEAD, "Lead", "leads", True, True,
        True, True, True, True, "uuid", "lead:read", "lead:create",
    ),
    EnvironmentResourceType.APPOINTMENT: ResourceTypeSpec(
        EnvironmentResourceType.APPOINTMENT, "Appointment", "appointments", True, True,
        False, True, False, True, "uuid", "appointment:read", "appointment:write",
    ),
    EnvironmentResourceType.KNOWLEDGE_DOCUMENT: ResourceTypeSpec(
        EnvironmentResourceType.KNOWLEDGE_DOCUMENT, "KnowledgeDocument", "knowledge_documents",
        True, True, False, True, True, True, "uuid", "knowledge:read", "knowledge:write",
    ),
    EnvironmentResourceType.AUTOMATION: ResourceTypeSpec(
        EnvironmentResourceType.AUTOMATION, "Automation", "automations", True, True,
        False, True, False, True, "string", "tenant:read", "tenant:update",
    ),
    EnvironmentResourceType.NOTIFICATION: ResourceTypeSpec(
        EnvironmentResourceType.NOTIFICATION, "NotificationRow", "notifications", True, True,
        False, False, False, True, "string", "tenant:read", "tenant:update",
    ),
    EnvironmentResourceType.INBOX: ResourceTypeSpec(
        EnvironmentResourceType.INBOX, "InboxThreadState", "inbox_thread_states", True, True,
        False, True, False, True, "uuid", "call:read", "call:read",
    ),
    EnvironmentResourceType.USAGE_EVENT: ResourceTypeSpec(
        EnvironmentResourceType.USAGE_EVENT, "UsageEvent", "usage_events", True, True,
        False, False, False, True, "uuid", "analytics:read", "billing:read",
    ),
}

EXCLUDED_CONTROL_PLANE = frozenset({
    "subscription",
    "billing_plan",
    "billing_customer",
    "payment",
    "sso_connection",
    "api_key",
    "service_account",
    "crm_integration",
    "calendar_integration",
})


def spec_for(name: str) -> ResourceTypeSpec:
    key = (name or "").strip().lower().replace("-", "_")
    try:
        return RESOURCE_TYPES[EnvironmentResourceType(key)]
    except ValueError as exc:
        raise KeyError(key) from exc


def all_types() -> tuple[ResourceTypeSpec, ...]:
    return tuple(RESOURCE_TYPES.values())
