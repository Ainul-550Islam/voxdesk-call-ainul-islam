"""
app/db/carrier_s2s_models.py
Durable ORM models for Batch 02 (live carrier media + speech-to-speech):

- S2SSessionRecord (`s2s_sessions`) -- one speech-to-speech session: which
  provider and model served it, how long it ran, what it consumed and how
  it ended.
- CarrierConnectionCheck (`carrier_connection_checks`) -- the recorded
  result of a carrier credential/connectivity probe, with the idempotency
  digest of the request that caused it.

Both tables are tenant-scoped. A row is never visible outside its tenant:
every read goes through the tenant predicate, never through the id alone.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class S2SSessionRecord(Base):
    """One speech-to-speech (realtime) session and what it consumed."""

    __tablename__ = "s2s_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("environments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: The call this session served, when it served one.
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("calls.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: ``openai_realtime`` or ``gemini_live``.
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: ``active`` | ``completed`` | ``failed`` | ``failed_over``.
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    #: Why the session ended, when it did not complete normally.
    failover_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The failure message of a session that ended badly.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_audio_s: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    output_audio_s: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Micro-USD. ``cost_known`` is false when no price is configured, so an
    #: unpriced session is never billed as if it were free.
    cost_micro_usd: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_known: Mapped[bool] = mapped_column(nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_s2s_sessions_tenant_status", "tenant_id", "status"),
        Index("ix_s2s_sessions_tenant_started", "tenant_id", "started_at"),
    )

    @property
    def organization_id(self) -> uuid.UUID:
        return self.tenant_id


class CarrierConnectionCheck(Base):
    """The recorded outcome of a carrier credential / connectivity probe."""

    __tablename__ = "carrier_connection_checks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("environments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: ``vonage`` | ``sip`` | ``s2s``.
    carrier: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    #: ``ok`` | ``failed`` | ``not_configured``.
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    #: True when the carrier answered and the credentials were accepted.
    ok: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)
    #: Machine-readable reason code, e.g. ``not_configured``, ``token_invalid``.
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: SHA-256 digest of the client supplied idempotency key.
    idempotency_key_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "carrier",
            "idempotency_key_digest",
            name="uq_carrier_connection_checks_tenant_carrier_key",
        ),
        Index("ix_carrier_connection_checks_tenant_checked", "tenant_id", "checked_at"),
    )

    @property
    def organization_id(self) -> uuid.UUID:
        return self.tenant_id


__all__ = ["CarrierConnectionCheck", "S2SSessionRecord"]
