"""Transactional-outbox event storage (Batch 07).

One new table, ``outbox_events``, because nothing existing stores generic
business events written inside a business transaction:

* ``webhook_deliveries`` is the *per-subscription delivery ledger* (it stays
  exactly that, and the dispatcher keeps using it for duplicate-attempt
  protection);
* ``jobs`` is the *execution engine* (delivery runs as a durable job);
* ``outbox_events`` is the *event record*: what happened, to which
  aggregate, in which tenant/environment, and whether it has been durably
  delivered.

The ORM lives here (the pattern ``app.leads.models`` established), and
``app.db.models`` imports this module last so ``Base.metadata`` carries the
table for ``create_all`` and Alembic alike.

Payload rules enforced at write time (see ``validate_event_payload``, used
by the publisher): no credential-shaped keys anywhere in the structure, a
hard serialized-size bound, and no deep nesting. Events carry *references*
(ids, versions, small deltas) — transcripts, recordings and bulk rows stay
in their own stores, because an event is fanned out to every endpoint and
duplicated into every retry.
"""

from __future__ import annotations

import enum
import json
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.jobs.types import PAYLOAD_MAX_BYTES, JobPayloadRejected
from app.tenancy.isolation import ValidationFailed


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class OutboxEventStatus(str, enum.Enum):
    """Lifecycle of one outbox event.

    ``pending`` covers both first delivery and retry-waiting (``available_at``
    says when the next attempt may run); ``delivered`` and ``dead_letter``
    are terminal unless an operator replays; ``cancelled`` is the durable
    operator cancellation (a delivery job already in flight re-checks the
    status before it sends, so cancelling beats a late dispatch).
    """

    PENDING = "pending"
    DELIVERED = "delivered"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


#: Delivery attempt budget for one event before it dead-letters. Matches the
#: existing webhook retry ceiling (``app.webhooks.retry.MAX_ATTEMPTS``) so an
#: event and its per-subscription deliveries exhaust together.
MAX_DELIVERY_ATTEMPTS = 8

#: Operator replay budget for a dead-lettered event (mirrors the job and
#: webhook replay budgets).
MAX_EVENT_REPLAYS = 3


class OutboxPayloadRejected(ValidationFailed):
    """An event payload carried secrets, exceeded the bound, or was unusable."""

    def __init__(self, message: str = "Outbox payload rejected") -> None:
        super().__init__(message)
        self.code = "outbox_payload_rejected"


def validate_event_payload(payload: dict | None) -> dict:
    """Fail-closed payload gate for outbox events.

    Delegates the credential-key scan and size bound to the shared job
    payload validator (one rule set for jobs and outbox), then re-raises
    under the outbox error type so routes render a domain-accurate 422.
    """
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise OutboxPayloadRejected("outbox payload must be an object")
    try:
        from app.jobs.types import ensure_payload_safe

        ensure_payload_safe(payload)
    except JobPayloadRejected as exc:
        raise OutboxPayloadRejected(str(exc)) from exc
    return payload


def serialized_size(payload: dict | None) -> int:
    """Byte size the publisher bound-checks (exported for tests/metrics)."""
    return len(json.dumps(payload or {}, default=str, separators=(",", ":")).encode("utf-8"))


class OutboxEvent(Base):
    """One durable business event, written inside the business transaction.

    ``idempotency_key`` is unique per tenant, so a business transaction that
    retries (a retried API call, a replayed provider webhook) publishes the
    same logical event exactly one time — the second insert returns the
    existing row. Delivery is *at-least-once* on top of that: consumers
    deduplicate on ``id`` (sent as ``X-VoxDesk-Event``) and
    ``event_version``.
    """

    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_outbox_tenant_idempotency"),
        Index("ix_outbox_claim", "status", "available_at"),
        Index("ix_outbox_tenant_environment", "tenant_id", "environment_id"),
        Index("ix_outbox_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: ``NULL`` for tenant-wide events; otherwise the environment whose
    #: resources the event describes. Environment-scoped webhook
    #: subscriptions only receive events from their own environment.
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id", ondelete="RESTRICT"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=OutboxEventStatus.PENDING.value, nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=MAX_DELIVERY_ATTEMPTS, nullable=False)
    replay_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: When the next delivery attempt may be enqueued. ``pending`` rows with
    #: a future ``available_at`` are retry-waiting; the dispatcher ignores
    #: them until due.
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_category: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    last_error: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    #: Tenant-unique logical identity (``default_event_key`` or explicit).
    #: The unique constraint is what makes duplicate publication of one
    #: business fact race-safe across replicas.
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
