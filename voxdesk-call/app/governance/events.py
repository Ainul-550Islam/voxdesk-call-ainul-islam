"""Typed internal governance event envelopes."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.auth.identity.events import scrub

from .enums import EvidenceEventType


@dataclass(frozen=True)
class GovernanceEvent:
    event_type: str
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    environment_id: uuid.UUID | None
    actor_type: str
    actor_id: uuid.UUID | None
    subject_type: str | None
    subject_id: str | None
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    occurred_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    def safe_payload(self) -> dict[str, Any]:
        return {
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "payload": scrub(self.payload),
            "occurred_at": self.occurred_at,
        }


def build_event(
    event_type: str | EvidenceEventType,
    *,
    tenant_id: uuid.UUID,
    organization_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    actor_type: str = "user",
    actor_id: uuid.UUID | None = None,
    subject_type: str | None = None,
    subject_id: str | None = None,
    payload: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> GovernanceEvent:
    return GovernanceEvent(
        event_type=event_type.value if isinstance(event_type, EvidenceEventType) else event_type,
        tenant_id=tenant_id,
        organization_id=organization_id,
        environment_id=environment_id,
        actor_type=actor_type,
        actor_id=actor_id,
        subject_type=subject_type,
        subject_id=subject_id,
        payload=payload or {},
        correlation_id=correlation_id,
    )
