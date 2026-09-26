"""Durable provider callback envelope.

The idempotency key is a hash of provider, event type, external id and event
id. A second delivery returns the existing row and does not apply the effect
again. Payload fields that name a secret, a transcript or a signed URL are
dropped before the row is stored.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Integer, String, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.tenancy.isolation import NotFound

_DROPPED = frozenset(
    {
        "authorization",
        "api_key",
        "auth_token",
        "secret",
        "password",
        "token",
        "signature",
        "recording_url",
        "transcript",
        "private_key",
        "credential",
    }
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TelephonyCallbackEvent(Base):
    __tablename__ = "telephony_callback_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('received', 'processed', 'dead_letter', 'replayed', 'unmatched', 'retry_scheduled')",
            name="ck_telephony_callback_events_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    event_id: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), default="received", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    replay_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_class: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    safe_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "provider": self.provider,
            "event_type": self.event_type,
            "external_id": self.external_id,
            "event_id": self.event_id,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "replay_count": self.replay_count,
            "error_class": self.error_class,
            "outcome": self.outcome,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
            "job_id": str(self.job_id) if self.job_id else None,
        }


def idempotency_key(provider: str, event_type: str, external_id: str, event_id: str) -> str:
    material = "|".join([provider, event_type, external_id, event_id])
    return hashlib.sha256(material.encode()).hexdigest()


def scrub(raw: dict | None) -> dict:
    cleaned = {}
    for key, value in (raw or {}).items():
        name = str(key).lower()
        if name in _DROPPED or any(
            part in name for part in ("secret", "token", "authorization", "password")
        ):
            continue
        if isinstance(value, str) and ("bearer " in value.lower() or value.startswith("sk-")):
            continue
        if isinstance(value, dict):
            cleaned[str(key)[:64]] = scrub(value)
        elif isinstance(value, str):
            cleaned[str(key)[:64]] = value[:200]
        elif isinstance(value, (int, float, bool)) or value is None:
            cleaned[str(key)[:64]] = value
    return cleaned


async def accept(session, envelope: dict, apply) -> dict:
    """Insert the event and apply once. A duplicate key does not call ``apply``."""
    provider = str(envelope.get("provider") or "").strip().lower()
    event_type = str(envelope.get("event_type") or "").strip().lower()
    external_id = str(envelope.get("external_id") or "")[:80]
    event_id = str(envelope.get("event_id") or "")[:80]
    if not provider or not event_type or not event_id:
        from app.telephony.provider_errors import ProviderValidationError

        raise ProviderValidationError("Callback identity is incomplete", provider=provider)
    key = idempotency_key(provider, event_type, external_id, event_id)
    existing = await by_key(session, key)
    if existing is not None:
        return {"duplicate": True, "side_effect": False, "event": existing, "outcome": "duplicate"}
    tenant_raw = envelope.get("tenant_id")
    tenant_id = uuid.UUID(str(tenant_raw)) if tenant_raw else None
    observed = envelope.get("observed_at")
    event = TelephonyCallbackEvent(
        tenant_id=tenant_id,
        provider=provider,
        event_type=event_type,
        external_id=external_id,
        event_id=event_id,
        idempotency_key=key,
        provider_observed_at=observed if isinstance(observed, datetime) else None,
        safe_metadata=_operational(envelope),
        status="received",
    )
    try:
        async with session.begin_nested():
            session.add(event)
            await session.flush()
    except IntegrityError:
        existing = await by_key(session, key)
        return {"duplicate": True, "side_effect": False, "event": existing, "outcome": "duplicate"}
    outcome, changed = await apply(session, event, envelope)
    event.outcome = outcome[:64]
    event.status = "unmatched" if outcome == "missing" else "processed"
    event.processed_at = _now()
    await session.flush()
    return {"duplicate": False, "side_effect": changed, "event": event, "outcome": outcome}


async def by_key(session, key: str) -> TelephonyCallbackEvent | None:
    return (
        await session.execute(
            select(TelephonyCallbackEvent).where(TelephonyCallbackEvent.idempotency_key == key)
        )
    ).scalar_one_or_none()


async def get_owned(session, tenant_id: uuid.UUID, event_id: uuid.UUID) -> TelephonyCallbackEvent:
    row = await session.get(TelephonyCallbackEvent, event_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


def _operational(envelope: dict) -> dict:
    """Keep status fields the replay path needs. Drop secrets first."""
    cleaned = scrub(envelope.get("metadata"))
    for key in ("status", "state", "duration_seconds", "external_recording_id"):
        if key in envelope and envelope[key] is not None and key not in cleaned:
            value = envelope[key]
            if isinstance(value, str):
                cleaned[key] = value[:80]
            elif isinstance(value, (int, float)):
                cleaned[key] = value
    return cleaned
