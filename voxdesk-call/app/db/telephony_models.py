"""
app/db/telephony_models.py
Durable SQLAlchemy ORM models for Prompt 6 Telephony / Voice Runtime:
- TelephonySipConnection (`telephony_sip_connections`)
- TelephonyPhoneNumber (`telephony_phone_numbers`)
- TelephonyCallSession (`telephony_call_sessions`)
- TelephonyProviderEvent (`telephony_provider_events`)
- TelephonyTransferRecord (`telephony_transfer_records`)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.telephony.enums import (
    CallDirection,
    MediaSessionState,
    PhoneNumberLifecycleStatus,
    SipConnectionStatus,
    SipTransportProtocol,
    TelephonyCallState,
    TelephonyProviderName,
    TransferFallbackAction,
    TransferLifecycleState,
    TransferMode,
)


class TelephonySipConnection(Base):
    """Durable SIP trunk / carrier connection configuration. Never stores plaintext passwords."""

    __tablename__ = "telephony_sip_connections"

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
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="Primary SIP Trunk")
    provider: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TelephonyProviderName.SIP.value,
    )
    phone_number_e164: Mapped[str | None] = mapped_column(String(32), nullable=True)
    termination_uri: Mapped[str] = mapped_column(String(255), nullable=False)
    origination_uri: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Stores a SHA-256 reference / KMS secret pointer, NEVER plaintext password
    credential_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transport: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=SipTransportProtocol.TLS.value,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=SipConnectionStatus.NOT_CONFIGURED.value,
        index=True,
    )
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(400), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        Index("ix_telephony_sip_connections_tenant_status", "tenant_id", "status"),
    )

    @property
    def organization_id(self) -> uuid.UUID:
        return self.tenant_id


class TelephonyPhoneNumber(Base):
    """Durable E.164 phone number record with inbound/outbound agent bindings and SIP linkage."""

    __tablename__ = "telephony_phone_numbers"

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
    number: Mapped[str] = mapped_column(String(32), nullable=False)
    e164_number: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TelephonyProviderName.TWILIO.value,
    )
    provider_number_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    sip_connection_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("telephony_sip_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    sip_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    inbound_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    outbound_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    inbound_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    inbound_agent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outbound_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=PhoneNumberLifecycleStatus.NOT_CONFIGURED.value,
        index=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_health_error: Mapped[str | None] = mapped_column(String(400), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "e164_number",
            name="uq_telephony_phone_numbers_tenant_e164",
        ),
        Index(
            "ix_telephony_phone_numbers_tenant_status",
            "tenant_id",
            "status",
        ),
    )

    @property
    def organization_id(self) -> uuid.UUID:
        return self.tenant_id


class TelephonyCallSession(Base):
    """Durable telephony call session coordinated with the existing `Call` table."""

    __tablename__ = "telephony_call_sessions"

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
    legacy_call_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("calls.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    phone_number_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("telephony_phone_numbers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    agent_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    variant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    provider: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TelephonyProviderName.TWILIO.value,
    )
    provider_call_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=CallDirection.INBOUND.value,
    )
    from_number: Mapped[str] = mapped_column(String(32), nullable=False)
    to_number: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TelephonyCallState.CREATED.value,
        index=True,
    )
    media_state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=MediaSessionState.IDLE.value,
    )
    transfer_state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TransferLifecycleState.NONE.value,
    )
    transfer_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    transfer_target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transferred_to_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_call_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    is_simulation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    execution_kind: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="PRODUCTION",
    )

    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billable_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    usage_finalized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hangup_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    recording_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    transcript_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)

    transcript_turns: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    dtmf_buffer: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    dtmf_events: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    runtime_events: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "provider_call_id",
            name="uq_telephony_call_sessions_tenant_provider_call",
        ),
        Index(
            "ix_telephony_call_sessions_tenant_status",
            "tenant_id",
            "status",
        ),
    )

    @property
    def organization_id(self) -> uuid.UUID:
        return self.tenant_id


class TelephonyProviderEvent(Base):
    """Durable provider webhook event ledger enforcing idempotency and replay protection."""

    __tablename__ = "telephony_provider_events"

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
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider_event_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("telephony_call_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider_call_id: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    processing_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="received"
    )
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_reason: Mapped[str | None] = mapped_column(String(400), nullable=True)
    normalized_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "provider_event_id",
            name="uq_telephony_provider_events_tenant_provider_event",
        ),
    )

    @property
    def organization_id(self) -> uuid.UUID:
        return self.tenant_id


class TelephonyTransferRecord(Base):
    """Durable cold, warm, and agent-to-agent transfer record with context preservation."""

    __tablename__ = "telephony_transfer_records"

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
    call_session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("telephony_call_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(96), nullable=False)
    transfer_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TransferMode.COLD.value,
    )
    source_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_destination: Mapped[str] = mapped_column(String(128), nullable=False)
    whisper_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    fallback_action: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TransferFallbackAction.RETURN_TO_AGENT.value,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=TransferLifecycleState.REQUESTED.value,
    )
    reason: Mapped[str | None] = mapped_column(String(400), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(400), nullable=True)
    context_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "call_session_id",
            "idempotency_key",
            name="uq_telephony_transfer_records_idempotency",
        ),
    )

    @property
    def organization_id(self) -> uuid.UUID:
        return self.tenant_id


class TelephonyUsageLedger(Base):
    """Authoritative, idempotent voice call usage ledger for Prompt 6 Telephony Runtime."""

    __tablename__ = "telephony_usage_ledger"

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
    call_session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("telephony_call_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    event_stage: Mapped[str] = mapped_column(
        String(32), nullable=False, default="CALL_FINALIZED"
    )
    direction: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CallDirection.INBOUND.value
    )
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TelephonyProviderName.TWILIO.value
    )
    is_simulation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    execution_kind: Mapped[str] = mapped_column(
        String(24), nullable=False, default="PRODUCTION"
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billable_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    billable_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_telephony_usage_ledger_idempotency",
        ),
    )

    @property
    def organization_id(self) -> uuid.UUID:
        return self.tenant_id



class PostCallStepRun(Base):
    """Durable, scoped checkpoints for the canonical Call post-processing job.

    No raw transcript is duplicated here. Transcript checkpoints carry turn
    references and a digest; analysis checkpoints contain actual model output.
    """

    __tablename__ = "post_call_step_runs"
    __table_args__ = (
        UniqueConstraint("call_id", "step", "pipeline_version", name="uq_post_call_step_version"),
        ForeignKeyConstraint(["tenant_id", "environment_id"],
                             ["environments.tenant_id", "environments.id"],
                             name="fk_post_call_step_environment"),
        CheckConstraint("attempts >= 0 AND pipeline_version >= 1", name="ck_post_call_step_counts"),
        CheckConstraint("status IN ('running','completed','failed','blocked','not_configured','unsupported')",
                        name="ck_post_call_step_status"),
        Index("ix_post_call_step_scope", "tenant_id", "environment_id", "call_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    pipeline_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    step: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    output: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    telemetry: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CallLatencyStat(Base):
    """Per-call voice pipeline latency percentile summary and turn stat (Sub-Phase 2A)."""

    __tablename__ = "call_latency_stats"
    __table_args__ = (
        Index("ix_call_latency_stats_tenant_call", "tenant_id", "call_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("calls.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    turn_idx: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stt_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_ttfb_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tts_ttfb_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    e2e_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    stt_ttfb_p50_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    stt_ttfb_p95_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_ttfb_p50_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_ttfb_p95_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tts_ttfb_p50_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tts_ttfb_p95_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    e2e_p50_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    e2e_p95_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    e2e_p99_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    e2e_max_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    interrupted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    interruptions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "call_id": str(self.call_id),
            "tenant_id": str(self.tenant_id),
            "turn_idx": int(self.turn_idx or 0),
            "turns": int(self.turns or 0),
            "stt_ms": self.stt_ms,
            "llm_ttfb_ms": self.llm_ttfb_ms,
            "tts_ttfb_ms": self.tts_ttfb_ms,
            "e2e_ms": self.e2e_ms,
            "stt_ttfb_p50_ms": self.stt_ttfb_p50_ms,
            "stt_ttfb_p95_ms": self.stt_ttfb_p95_ms,
            "llm_ttfb_p50_ms": self.llm_ttfb_p50_ms,
            "llm_ttfb_p95_ms": self.llm_ttfb_p95_ms,
            "tts_ttfb_p50_ms": self.tts_ttfb_p50_ms,
            "tts_ttfb_p95_ms": self.tts_ttfb_p95_ms,
            "e2e_p50_ms": self.e2e_p50_ms,
            "e2e_p95_ms": self.e2e_p95_ms,
            "e2e_p99_ms": self.e2e_p99_ms,
            "e2e_max_ms": self.e2e_max_ms,
            "interrupted": bool(self.interrupted),
            "interruptions": int(self.interruptions or 0),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

