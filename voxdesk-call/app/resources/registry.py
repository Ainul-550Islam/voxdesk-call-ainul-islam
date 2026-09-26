"""Static map from a resource type to the ORM class that stores it.

A request cannot name a model. The only keys are the eight registered types.
"""

from __future__ import annotations

from app.db.models import (
    Appointment,
    Automation,
    Call,
    InboxThreadState,
    KnowledgeDocument,
    Lead,
    NotificationRow,
    UsageEvent,
)
from app.environments.resource_types import (
    EXCLUDED_CONTROL_PLANE,
    EnvironmentResourceType,
    ResourceTypeSpec,
    all_types,
)

_MODELS = {
    EnvironmentResourceType.CALL: Call,
    EnvironmentResourceType.LEAD: Lead,
    EnvironmentResourceType.APPOINTMENT: Appointment,
    EnvironmentResourceType.KNOWLEDGE_DOCUMENT: KnowledgeDocument,
    EnvironmentResourceType.AUTOMATION: Automation,
    EnvironmentResourceType.NOTIFICATION: NotificationRow,
    EnvironmentResourceType.INBOX: InboxThreadState,
    EnvironmentResourceType.USAGE_EVENT: UsageEvent,
}


def model_for(resource_type: EnvironmentResourceType):
    try:
        return _MODELS[resource_type]
    except KeyError as exc:
        raise KeyError("unsupported resource") from exc


def spec_for(resource_type: EnvironmentResourceType) -> ResourceTypeSpec:
    for spec in all_types():
        if spec.type is resource_type:
            return spec
    raise KeyError("unsupported resource")


def registered_names() -> frozenset[str]:
    return frozenset(item.value for item in _MODELS)


def is_excluded(name: str) -> bool:
    return (name or "").strip().lower() in EXCLUDED_CONTROL_PLANE
