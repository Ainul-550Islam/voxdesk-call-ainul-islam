"""Batch 04 persistence. All rows are tenant scoped and additive to Batch 03.

Secrets, raw access tokens, full destination numbers and unredacted assist
transcripts are deliberately absent. Provider IDs are references, not proof of
carrier success until the corresponding acknowledged transition is recorded.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Uuid as UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.contact_center.models import AgentSkill, Skill
from app.db.models import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _id(value: uuid.UUID | None) -> str | None:
    return str(value) if value else None


class ScopeColumns:
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False, index=True
    )


class DialerCampaignState(ScopeColumns, Base):
    __tablename__ = "cc2_dialer_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "campaign_id", name="uq_cc2_dialer_policy_campaign"),
        CheckConstraint("mode IN ('predictive','progressive','preview','ai_agent_blast')", name="ck_cc2_dialer_mode"),
        CheckConstraint("status IN ('draft','running','paused','completed','stopped')", name="ck_cc2_dialer_status"),
        CheckConstraint("pacing_ratio > 0", name="ck_cc2_dialer_ratio"),
        CheckConstraint("abandonment_cap > 0 AND abandonment_cap <= 1", name="ck_cc2_dialer_cap"),
        CheckConstraint("max_inflight >= 1", name="ck_cc2_dialer_inflight"),
        Index("ix_cc2_dialer_policy_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    caller_id_number: Mapped[str] = mapped_column(String(32), nullable=False)
    pacing_ratio: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False, default=Decimal("1"))
    abandonment_cap: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False, default=Decimal("0.03"))
    max_inflight: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    calling_windows: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    holiday_dates: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    safe_harbor_message: Mapped[str] = mapped_column(Text, nullable=False)
    consent_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    jurisdiction: Mapped[str] = mapped_column(String(32), nullable=False, default="US")
    dnc_scrubbed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dnc_scrub_version: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    prepared_lead_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": _id(self.id), "campaign_id": _id(self.campaign_id), "mode": self.mode,
            "status": self.status, "caller_id_number": self.caller_id_number,
            "pacing_ratio": str(self.pacing_ratio), "abandonment_cap": str(self.abandonment_cap),
            "max_inflight": self.max_inflight, "calling_windows": list(self.calling_windows or []),
            "holiday_dates": list(self.holiday_dates or []), "consent_required": self.consent_required,
            "jurisdiction": self.jurisdiction, "dnc_scrubbed_at": _iso(self.dnc_scrubbed_at),
            "dnc_scrub_version": self.dnc_scrub_version, "prepared_lead_count": self.prepared_lead_count,
            "started_at": _iso(self.started_at), "stopped_at": _iso(self.stopped_at), "version": self.version,
        }


# Internal service name retained as an alias; the public persistence contract
# calls this row a campaign state because it owns both policy and runtime state.
DialerPolicy = DialerCampaignState


class DialerContact(ScopeColumns, Base):
    __tablename__ = "cc2_dialer_contacts"
    __table_args__ = (
        UniqueConstraint("policy_id", "lead_id", name="uq_cc2_dialer_contact_lead"),
        CheckConstraint(
            "state IN ('pending','eligible','blocked','preview_assigned','dialing','retry_scheduled','completed','skipped')",
            name="ck_cc2_dialer_contact_state",
        ),
        Index("ix_cc2_dialer_contact_due", "tenant_id", "policy_id", "state", "next_attempt_at"),
        Index("ix_cc2_dialer_contact_agent", "tenant_id", "assigned_agent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_dialer_policies.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    block_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    recipient_timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    dnc_scrubbed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dnc_scrub_version: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    scrub_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    consent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    consent_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    preview_lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_attempt_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    def as_dict(self, *, include_private: bool = False) -> dict[str, Any]:
        del include_private
        return {
            "id": _id(self.id), "policy_id": _id(self.policy_id), "lead_id": _id(self.lead_id),
            "state": self.state, "block_reason": self.block_reason,
            "recipient_timezone": self.recipient_timezone, "dnc_scrubbed_at": _iso(self.dnc_scrubbed_at),
            "consent_version": self.consent_version, "assigned_agent_id": _id(self.assigned_agent_id),
            "preview_lease_until": _iso(self.preview_lease_until),
            "active_attempt_id": _id(self.active_attempt_id), "next_attempt_at": _iso(self.next_attempt_at),
            "version": self.version,
        }


class DialerAttempt(ScopeColumns, Base):
    __tablename__ = "cc2_dialer_attempts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_cc2_dialer_attempt_key"),
        UniqueConstraint("provider_call_id", name="uq_cc2_dialer_attempt_provider"),
        CheckConstraint("mode IN ('predictive','progressive','preview','ai_agent_blast')", name="ck_cc2_dialer_attempt_mode"),
        CheckConstraint(
            "outcome IN ('initiated','ringing','answered','machine','waiting_for_agent','connected','abandoned','failed','completed','cancelled')",
            name="ck_cc2_dialer_attempt_outcome",
        ),
        Index("ix_cc2_dialer_attempt_policy", "tenant_id", "policy_id", "created_at"),
        Index("ix_cc2_dialer_attempt_abandon", "tenant_id", "policy_id", "answered_at", "abandoned_at"),
        Index("ix_cc2_dialer_attempt_contact", "tenant_id", "contact_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_dialer_policies.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_dialer_contacts.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="SET NULL"), nullable=True
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False, default="initiated")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_call_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    agent_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    answered_by: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    agent_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    abandoned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    abandonment_counted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    safe_harbor_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    safe_harbor_played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": _id(self.id), "policy_id": _id(self.policy_id), "contact_id": _id(self.contact_id),
            "lead_id": _id(self.lead_id), "call_id": _id(self.call_id), "mode": self.mode,
            "outcome": self.outcome, "provider_call_id": self.provider_call_id,
            "agent_user_id": _id(self.agent_user_id), "answered_by": self.answered_by,
            "answered_at": _iso(self.answered_at), "agent_connected_at": _iso(self.agent_connected_at),
            "abandoned_at": _iso(self.abandoned_at), "abandonment_counted": self.abandonment_counted,
            "safe_harbor_requested_at": _iso(self.safe_harbor_requested_at),
            "safe_harbor_played_at": _iso(self.safe_harbor_played_at), "ended_at": _iso(self.ended_at),
            "duration_seconds": self.duration_seconds, "failure_category": self.failure_category,
        }


class DialerJurisdictionOverride(ScopeColumns, Base):
    __tablename__ = "cc2_dialer_overrides"
    __table_args__ = (
        UniqueConstraint("tenant_id", "policy_id", "jurisdiction", "created_at", name="uq_cc2_dialer_override_version"),
        CheckConstraint("abandonment_cap > 0.03 AND abandonment_cap <= 0.10", name="ck_cc2_dialer_override_cap"),
        Index("ix_cc2_dialer_override_lookup", "tenant_id", "policy_id", "jurisdiction", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_dialer_policies.id", ondelete="CASCADE"), nullable=False
    )
    jurisdiction: Mapped[str] = mapped_column(String(32), nullable=False)
    abandonment_cap: Mapped[Decimal] = mapped_column(Numeric(6, 5), nullable=False)
    legal_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    approved_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class SoftphoneSession(ScopeColumns, Base):
    __tablename__ = "cc2_softphone_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "registration_key", name="uq_cc2_softphone_registration"),
        UniqueConstraint("client_identity", name="uq_cc2_softphone_identity"),
        UniqueConstraint("active_device_key", name="uq_cc2_softphone_active_device"),
        CheckConstraint("status IN ('active','expired','revoked')", name="ck_cc2_softphone_session_status"),
        Index("ix_cc2_softphone_agent", "tenant_id", "user_id", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_agent_devices.id", ondelete="SET NULL"), nullable=True
    )
    active_device_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    registration_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    client_identity: Mapped[str] = mapped_column(String(80), nullable=False)
    device_label: Mapped[str] = mapped_column(String(100), nullable=False)
    ws_ticket_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": _id(self.id), "user_id": _id(self.user_id), "environment_id": _id(self.environment_id),
            "device_id": _id(self.device_id), "client_identity": self.client_identity, "device_label": self.device_label,
            "status": self.status, "last_heartbeat_at": _iso(self.last_heartbeat_at),
            "expires_at": _iso(self.expires_at), "created_at": _iso(self.created_at),
        }


class SoftphoneCall(ScopeColumns, Base):
    __tablename__ = "cc2_softphone_calls"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_cc2_softphone_call_key"),
        UniqueConstraint("conference_name", name="uq_cc2_softphone_conference"),
        CheckConstraint(
            "status IN ('pending','dialing','waiting_for_agent','in_progress','held','completed','failed','cancelled','transferred')",
            name="ck_cc2_softphone_call_status",
        ),
        CheckConstraint("origin IN ('agent_outbound','acd','dialer','callback')", name="ck_cc2_softphone_origin"),
        Index("ix_cc2_softphone_agent_calls", "tenant_id", "agent_user_id", "status", "created_at"),
        Index("ix_cc2_softphone_call_record", "tenant_id", "call_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="SET NULL"), nullable=True
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_softphone_sessions.id", ondelete="SET NULL"), nullable=True
    )
    agent_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    conference_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_conference_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    dialer_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_dialer_attempts.id", ondelete="SET NULL"), nullable=True
    )
    callback_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_callbacks.id", ondelete="SET NULL"), nullable=True
    )
    acd_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc_queue_entries.id", ondelete="SET NULL"), nullable=True
    )
    muted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    on_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    agent_join_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": _id(self.id), "call_id": _id(self.call_id), "session_id": _id(self.session_id),
            "agent_user_id": _id(self.agent_user_id), "origin": self.origin, "status": self.status,
            "conference_name": self.conference_name, "dialer_attempt_id": _id(self.dialer_attempt_id),
            "callback_id": _id(self.callback_id), "acd_entry_id": _id(self.acd_entry_id),
            "muted": self.muted, "on_hold": self.on_hold,
            "agent_join_deadline": _iso(self.agent_join_deadline), "answered_at": _iso(self.answered_at),
            "ended_at": _iso(self.ended_at), "failure_category": self.failure_category,
            "created_at": _iso(self.created_at),
        }


class SoftphoneLeg(ScopeColumns, Base):
    __tablename__ = "cc2_softphone_legs"
    __table_args__ = (
        UniqueConstraint("provider_call_id", name="uq_cc2_softphone_leg_provider"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_cc2_softphone_leg_key"),
        CheckConstraint("kind IN ('customer','agent','consult')", name="ck_cc2_softphone_leg_kind"),
        CheckConstraint(
            "status IN ('pending','ringing','in_progress','completed','failed','cancelled')",
            name="ck_cc2_softphone_leg_status",
        ),
        Index("ix_cc2_softphone_leg_call", "tenant_id", "softphone_call_id", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    softphone_call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_softphone_calls.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    provider_call_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    destination_redacted: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": _id(self.id), "softphone_call_id": _id(self.softphone_call_id),
            "call_id": _id(self.call_id), "kind": self.kind, "status": self.status,
            "provider_call_id": self.provider_call_id, "destination_redacted": self.destination_redacted,
            "answered_at": _iso(self.answered_at), "ended_at": _iso(self.ended_at),
        }


class AssistSession(ScopeColumns, Base):
    __tablename__ = "cc2_assist_sessions"
    __table_args__ = (
        UniqueConstraint("active_key", name="uq_cc2_assist_active_call"),
        CheckConstraint("status IN ('active','ended','failed')", name="ck_cc2_assist_session_status"),
        Index("ix_cc2_assist_session_agent", "tenant_id", "agent_user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    agent_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    active_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssistTurn(ScopeColumns, Base):
    __tablename__ = "cc2_assist_turns"
    __table_args__ = (
        UniqueConstraint("call_id", "speaker", "seq", name="uq_cc2_assist_turn_sequence"),
        CheckConstraint("speaker IN ('caller','agent','system','supervisor')", name="ck_cc2_assist_turn_speaker"),
        CheckConstraint(
            "processing_status IN ('queued','retrieval_only','completed','no_evidence','failed')",
            name="ck_cc2_assist_turn_status",
        ),
        Index("ix_cc2_assist_turn_call", "tenant_id", "call_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_assist_sessions.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(String(16), nullable=False)
    text_redacted: Mapped[str] = mapped_column(Text, nullable=False)
    processing_status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    sentiment_score: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True)
    sentiment_label: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    sentiment_method: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    failure_category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class AssistSuggestion(ScopeColumns, Base):
    __tablename__ = "cc2_assist_suggestions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "turn_id", "kind", name="uq_cc2_assist_suggestion_kind"),
        CheckConstraint("kind IN ('knowledge','next_best_action')", name="ck_cc2_assist_suggestion_kind"),
        CheckConstraint(
            "feedback IN ('','accepted','dismissed','helpful','not_helpful')",
            name="ck_cc2_assist_suggestion_feedback",
        ),
        Index("ix_cc2_assist_suggestion_call", "tenant_id", "call_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_assist_sessions.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_assist_turns.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    generation_method: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    feedback: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    feedback_note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    feedback_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": _id(self.id), "call_id": _id(self.call_id), "turn_id": _id(self.turn_id),
            "kind": self.kind, "text": self.text, "citations": list(self.citations or []),
            "generation_method": self.generation_method, "model": self.model,
            "feedback": self.feedback, "feedback_note": self.feedback_note,
            "feedback_by": _id(self.feedback_by), "feedback_at": _iso(self.feedback_at),
            "created_at": _iso(self.created_at),
        }


class AssistAlert(ScopeColumns, Base):
    __tablename__ = "cc2_assist_alerts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "turn_id", "kind", name="uq_cc2_assist_alert_turn_kind"),
        CheckConstraint("kind IN ('negative_sentiment','compliance_review')", name="ck_cc2_assist_alert_kind"),
        CheckConstraint("status IN ('open','acknowledged','resolved')", name="ck_cc2_assist_alert_status"),
        Index("ix_cc2_assist_alert_call", "tenant_id", "call_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_assist_turns.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": _id(self.id), "call_id": _id(self.call_id), "turn_id": _id(self.turn_id),
            "kind": self.kind, "severity": self.severity, "message": self.message,
            "evidence": dict(self.evidence or {}), "status": self.status, "created_at": _iso(self.created_at),
        }


class AcdQueuePolicy(ScopeColumns, Base):
    __tablename__ = "cc2_acd_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "queue_id", name="uq_cc2_acd_policy_queue"),
        CheckConstraint(
            "overflow_kind IN ('none','callback','escalate','voicemail','queue')",
            name="ck_cc2_acd_overflow_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc_queues.id", ondelete="CASCADE"), nullable=False
    )
    overflow_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    overflow_after_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    overflow_waiting: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    overflow_queue_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc_queues.id", ondelete="SET NULL"), nullable=True
    )
    callback_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_overflow_sweep_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class AgentReservation(ScopeColumns, Base):
    __tablename__ = "cc2_agent_reservations"
    __table_args__ = (
        UniqueConstraint("active_key", name="uq_cc2_agent_reservation_active"),
        CheckConstraint("kind IN ('dialer','softphone','callback')", name="ck_cc2_agent_reservation_kind"),
        CheckConstraint("status IN ('active','released','expired')", name="ck_cc2_agent_reservation_status"),
        Index("ix_cc2_agent_reservation_agent", "tenant_id", "user_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    active_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentStateInterval(ScopeColumns, Base):
    __tablename__ = "cc2_agent_state_periods"
    __table_args__ = (
        UniqueConstraint("open_key", name="uq_cc2_agent_state_open"),
        CheckConstraint(
            "state IN ('offline','available','ringing','busy','hold','wrap_up','away','paused','disabled')",
            name="ck_cc2_agent_period_state",
        ),
        Index("ix_cc2_agent_period_accounting", "tenant_id", "user_id", "started_at", "ended_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    open_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class CallbackRequest(ScopeColumns, Base):
    __tablename__ = "cc2_callbacks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "queue_entry_id", name="uq_cc2_callback_entry"),
        CheckConstraint(
            "status IN ('scheduled','running','completed','failed','cancelled')",
            name="ck_cc2_callback_status",
        ),
        Index("ix_cc2_callback_due", "tenant_id", "status", "scheduled_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc_queue_entries.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc_queues.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    failure_category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": _id(self.id), "queue_entry_id": _id(self.queue_entry_id),
            "queue_id": _id(self.queue_id), "call_id": _id(self.call_id),
            "requested_by": _id(self.requested_by), "assigned_agent_id": _id(self.assigned_agent_id),
            "job_id": _id(self.job_id), "status": self.status, "timezone": self.timezone,
            "scheduled_at": _iso(self.scheduled_at), "attempts": self.attempts,
            "next_attempt_at": _iso(self.next_attempt_at), "note": self.note,
            "failure_category": self.failure_category, "created_at": _iso(self.created_at),
            "completed_at": _iso(self.completed_at), "cancelled_at": _iso(self.cancelled_at),
        }


AgentStatePeriod = AgentStateInterval


class AbandonEvent(ScopeColumns, Base):
    """Durable numerator/denominator ledger for rolling abandonment math."""

    __tablename__ = "cc2_abandon_events"
    __table_args__ = (
        UniqueConstraint("attempt_id", "event_type", name="uq_cc2_abandon_attempt_event"),
        CheckConstraint(
            "event_type IN ('human_answered','agent_connected','abandoned','machine_answered','safe_harbor')",
            name="ck_cc2_abandon_event_type",
        ),
        Index("ix_cc2_abandon_rollup", "tenant_id", "campaign_id", "occurred_at", "event_type"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_dialer_policies.id", ondelete="CASCADE"), nullable=False
    )
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_dialer_attempts.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    numerator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    denominator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    connected_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class PacingSnapshot(ScopeColumns, Base):
    __tablename__ = "cc2_pacing_snapshots"
    __table_args__ = (
        CheckConstraint("mode IN ('predictive','progressive','preview','ai_agent_blast')", name="ck_cc2_pacing_mode"),
        CheckConstraint("dial_ratio >= 0", name="ck_cc2_pacing_ratio"),
        CheckConstraint(
            "projected_abandon_rate >= 0 AND projected_abandon_rate <= 1",
            name="ck_cc2_pacing_projected",
        ),
        Index("ix_cc2_pacing_campaign_time", "tenant_id", "campaign_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_dialer_policies.id", ondelete="CASCADE"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    answer_rate: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    occupancy: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    dial_ratio: Mapped[Decimal] = mapped_column(Numeric(7, 3), nullable=False)
    projected_abandon_rate: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    available_agents: Mapped[int] = mapped_column(Integer, nullable=False)
    soon_available_agents: Mapped[Decimal] = mapped_column(Numeric(7, 3), nullable=False, default=Decimal("0"))
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    throttled_to_progressive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class PreviewReservation(ScopeColumns, Base):
    __tablename__ = "cc2_preview_reservations"
    __table_args__ = (
        UniqueConstraint("active_key", name="uq_cc2_preview_active_contact"),
        CheckConstraint(
            "status IN ('active','dialed','released','expired','skipped')",
            name="ck_cc2_preview_status",
        ),
        Index("ix_cc2_preview_agent", "tenant_id", "agent_user_id", "status", "expires_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    policy_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_dialer_policies.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_dialer_contacts.id", ondelete="CASCADE"), nullable=False
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    agent_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    active_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    disposition: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentDevice(ScopeColumns, Base):
    __tablename__ = "cc2_agent_devices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "fingerprint_hash", name="uq_cc2_device_fingerprint"),
        CheckConstraint("kind IN ('browser','desk_phone')", name="ck_cc2_device_kind"),
        CheckConstraint("status IN ('active','revoked','disabled')", name="ck_cc2_device_status"),
        Index("ix_cc2_device_agent", "tenant_id", "user_id", "status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="browser")
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    fingerprint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": _id(self.id), "user_id": _id(self.user_id), "environment_id": _id(self.environment_id),
            "kind": self.kind, "label": self.label, "status": self.status,
            "last_heartbeat_at": _iso(self.last_heartbeat_at), "created_at": _iso(self.created_at),
            "revoked_at": _iso(self.revoked_at),
        }


class AssistFeedback(ScopeColumns, Base):
    __tablename__ = "cc2_assist_feedback"
    __table_args__ = (
        UniqueConstraint("suggestion_id", "agent_user_id", name="uq_cc2_feedback_suggestion_agent"),
        CheckConstraint(
            "action IN ('shown','accepted','ignored','dismissed','helpful','not_helpful')",
            name="ck_cc2_feedback_action",
        ),
        Index("ix_cc2_feedback_call", "tenant_id", "call_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    suggestion_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_assist_suggestions.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    agent_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    note_redacted: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    shown_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class ComplianceScript(ScopeColumns, Base):
    __tablename__ = "cc2_compliance_scripts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "environment_id", "name", "version", name="uq_cc2_script_version"),
        CheckConstraint("version >= 1", name="ck_cc2_script_version"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(32), nullable=False, default="US")
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="voice")
    script_text: Mapped[str] = mapped_column(Text, nullable=False)
    required_phrases: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    forbidden_phrases: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class ComplianceAdherence(ScopeColumns, Base):
    __tablename__ = "cc2_compliance_adherence"
    __table_args__ = (
        UniqueConstraint("call_id", "script_id", name="uq_cc2_adherence_call_script"),
        CheckConstraint(
            "status IN ('pending','met','missed','violated')",
            name="ck_cc2_adherence_status",
        ),
        Index("ix_cc2_adherence_agent", "tenant_id", "agent_user_id", "status", "updated_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    script_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc2_compliance_scripts.id", ondelete="CASCADE"), nullable=False
    )
    agent_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    matched_phrases: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    missed_phrases: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    violated_phrases: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    evidence_redacted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class WrapupDraft(ScopeColumns, Base):
    __tablename__ = "cc2_wrapup_drafts"
    __table_args__ = (
        UniqueConstraint("call_id", "agent_user_id", name="uq_cc2_wrapup_call_agent"),
        CheckConstraint("status IN ('draft','submitted','failed')", name="ck_cc2_wrapup_status"),
        CheckConstraint(
            "crm_status IN ('not_requested','pending','written','failed')",
            name="ck_cc2_wrapup_crm_status",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    agent_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    summary_redacted: Mapped[str] = mapped_column(Text, nullable=False, default="")
    disposition: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    tags: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    crm_status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_requested")
    crm_reference: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    failure_category: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    generation_method: Mapped[str] = mapped_column(String(32), nullable=False, default="governed")
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QueueSkillRequirement(ScopeColumns, Base):
    __tablename__ = "cc2_queue_skill_requirements"
    __table_args__ = (
        UniqueConstraint("queue_id", "skill_id", name="uq_cc2_queue_skill"),
        CheckConstraint("min_proficiency >= 1 AND min_proficiency <= 5", name="ck_cc2_queue_skill_proficiency"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc_queues.id", ondelete="CASCADE"), nullable=False
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc_skills.id", ondelete="CASCADE"), nullable=False
    )
    min_proficiency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class QueuePriorityConfig(ScopeColumns, Base):
    __tablename__ = "cc2_queue_priority_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "queue_id", name="uq_cc2_queue_priority"),
        CheckConstraint("priority >= 0", name="ck_cc2_queue_priority_value"),
        CheckConstraint("max_wait_seconds >= 1", name="ck_cc2_queue_max_wait"),
        CheckConstraint(
            "fallback_kind IN ('ai_agent','voicemail','callback','queue')",
            name="ck_cc2_queue_fallback",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cc_queues.id", ondelete="CASCADE"), nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    strategy: Mapped[str] = mapped_column(String(32), nullable=False, default="longest_idle")
    overflow_chain: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    max_wait_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    fallback_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="callback")
    ewt_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


MODELS = (
    DialerCampaignState, DialerContact, DialerAttempt, DialerJurisdictionOverride,
    AbandonEvent, PacingSnapshot, PreviewReservation,
    AgentDevice, SoftphoneSession, SoftphoneCall, SoftphoneLeg,
    AssistSession, AssistTurn, AssistSuggestion, AssistFeedback, AssistAlert,
    ComplianceScript, ComplianceAdherence, WrapupDraft,
    AcdQueuePolicy, QueueSkillRequirement, QueuePriorityConfig,
    AgentReservation, AgentStateInterval, CallbackRequest,
)

__all__ = sorted(
    {item.__name__ for item in MODELS}
    | {Skill.__name__, AgentSkill.__name__}
    | {"DialerPolicy", "AgentStatePeriod", "MODELS"}
)
