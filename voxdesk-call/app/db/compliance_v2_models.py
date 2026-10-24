"""Durable persistence for the Batch 03 compliance controls.

Every table here is additive: no existing table is altered, no existing column
changes shape. The rows are tenant-scoped wherever the operation is
customer-scoped (``tenant_id`` non-null), and the two genuinely platform-wide
records — the KMS rekey job and the evidence snapshot schedule — carry a
nullable ``tenant_id`` so an operator run is not falsely attributed to a
customer.

Design rules the rest of the batch relies on:

* a sink, a campaign and a residency pin are unique per tenant, so a retried
  request cannot create a second copy of the same control;
* enum-shaped columns carry a CHECK constraint, so an unexpected state is a
  database error rather than a silently ignored string;
* the SIEM cursor stores its own circuit-breaker state, because "how many
  consecutive failures has this sink produced" must survive a process restart
  and must be shared by every API replica.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
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


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _sql_in(values: tuple[str, ...]) -> str:
    """Render an enum tuple as a CHECK-constraint body (values are literals)."""
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


# ------------------------------------------------------------------ enums ---

SIEM_SINK_KINDS = ("splunk_hec", "datadog_logs", "elastic_bulk", "syslog_cef", "s3_jsonl")
SIEM_CURSOR_STATES = ("idle", "retrying", "circuit_open", "dead_lettered")
AUDIT_EXPORT_STATUS = ("queued", "running", "succeeded", "failed")
REKEY_STATUS = ("queued", "running", "succeeded", "failed", "cancelled")
RESIDENCY_STATUS = ("pinned", "pending", "unpinned")
EVIDENCE_STATUS = ("PASS", "FAIL", "NOT_CONFIGURED", "ERROR")
CAMPAIGN_STATUS = ("draft", "active", "completed", "cancelled")
REVIEW_DECISION = ("pending", "attest", "revoke", "not_applicable")
INCIDENT_STATUS = ("detected", "triaged", "contained", "resolved", "closed")
INCIDENT_SEVERITY = ("sev1", "sev2", "sev3", "sev4")
BREACH_STATUS = ("open", "notified", "overdue", "closed")
DSAR_KINDS = ("export", "erase")
DSAR_STATUS = ("received", "verifying", "in_progress", "completed", "rejected", "failed")
BREAK_GLASS_STATUS = ("pending_approval", "active", "expired", "revoked", "denied")

# ------------------------------------------------------------------ SIEM -----

class SiemSink(Base):
    """One configured log destination for a tenant.

    ``secret_env`` is *not* the secret: it is an opaque ``secret://`` reference
    produced by the existing :class:`app.security.secret_store.EncryptedSecretStore`,
    so a database dump never yields a Splunk token or a Datadog API key.
    """

    __tablename__ = "compliance_siem_sinks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_compliance_siem_sink_name"),
        Index("ix_compliance_siem_sink_tenant_enabled", "tenant_id", "enabled"),
        CheckConstraint(
            "kind IN " + _sql_in(SIEM_SINK_KINDS),
            name="ck_compliance_siem_sink_kind",
        ),
        CheckConstraint("batch_size > 0 AND batch_size <= 5000", name="ck_compliance_siem_sink_batch"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    #: Opaque ``secret://...`` reference; empty when the sink needs no secret
    #: (for example a syslog collector authenticated by network position).
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False, default="")
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class SiemCursor(Base):
    """Per-sink delivery watermark plus its circuit-breaker state.

    The watermark is a ``(created_at, id)`` pair over ``audit_logs``, advanced
    only when a batch is fully delivered or terminally dead-lettered, which is
    what makes "each audit row reaches each sink at least once, and the cursor
    moves exactly once per delivered row" true across restarts.
    """

    __tablename__ = "compliance_siem_cursors"
    __table_args__ = (
        UniqueConstraint("sink_id", name="uq_compliance_siem_cursor_sink"),
        Index("ix_compliance_siem_cursor_due", "state", "next_attempt_at"),
        CheckConstraint(
            "state IN " + _sql_in(SIEM_CURSOR_STATES),
            name="ck_compliance_siem_cursor_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sink_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("compliance_siem_sinks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    watermark_created_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    watermark_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    events_delivered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="idle")
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    circuit_open_until: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    last_success_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class SiemDeadLetter(Base):
    """An audit row a sink refused permanently, retained for operator redrive."""

    __tablename__ = "compliance_siem_dead_letters"
    __table_args__ = (
        UniqueConstraint("sink_id", "audit_id", name="uq_compliance_siem_dlq_event"),
        Index("ix_compliance_siem_dlq_tenant", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sink_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("compliance_siem_sinks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    audit_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    redrive_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    redriven_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


# --------------------------------------------------------- audit integrity ---

class AuditExport(Base):
    """A requested signed evidence bundle over ``audit_logs``."""

    __tablename__ = "compliance_audit_exports"
    __table_args__ = (
        Index("ix_compliance_audit_export_tenant_status", "tenant_id", "status"),
        CheckConstraint(
            "status IN " + _sql_in(AUDIT_EXPORT_STATUS),
            name="ck_compliance_audit_export_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    range_start: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    range_end: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chain_head: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    bundle_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    signing_key_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ------------------------------------------------------------------- KMS -----

class KmsRekeyJob(Base):
    """A resumable re-encryption run over the existing encrypted columns."""

    __tablename__ = "compliance_kms_rekey_jobs"
    __table_args__ = (
        Index("ix_compliance_kms_rekey_status", "status", "created_at"),
        CheckConstraint(
            "status IN " + _sql_in(REKEY_STATUS),
            name="ck_compliance_kms_rekey_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    target_tables: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    cursor_table: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    cursor_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    rows_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_rekeyed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_key_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    target_key_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ------------------------------------------------------------- residency -----

class TenantResidency(Base):
    """The region a tenant's data is pinned to (one row per tenant)."""

    __tablename__ = "compliance_tenant_residency"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_compliance_residency_tenant"),
        CheckConstraint(
            "status IN " + _sql_in(RESIDENCY_STATUS),
            name="ck_compliance_residency_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pinned")
    previous_region: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    #: Observed storage locations (bucket/shard) recorded by the audit tick.
    data_locations: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    changed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class ResidencyViolationLog(Base):
    """A cross-region access attempt, recorded whether or not it was blocked."""

    __tablename__ = "compliance_residency_violations"
    __table_args__ = (
        Index("ix_compliance_residency_violation_tenant", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pinned_region: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    requested_region: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    resource_id: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    ip_address: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reason: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


# ------------------------------------------------------------------ SOC 2 ----

class EvidenceSnapshot(Base):
    """One evidence-collection run, stored with its content hash."""

    __tablename__ = "compliance_evidence_snapshots"
    __table_args__ = (
        Index("ix_compliance_evidence_snapshot_tenant", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    run_key: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    pass_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    not_configured_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class ControlStatus(Base):
    """Per-control outcome for one snapshot — never defaulted to PASS."""

    __tablename__ = "compliance_control_status"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "control_id", name="uq_compliance_control_status"),
        Index("ix_compliance_control_status_control", "control_id", "status"),
        CheckConstraint(
            "status IN " + _sql_in(EVIDENCE_STATUS),
            name="ck_compliance_control_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("compliance_evidence_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    control_id: Mapped[str] = mapped_column(String(24), nullable=False)
    collector: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    collected_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class AccessReviewCampaign(Base):
    """A time-boxed access recertification campaign."""

    __tablename__ = "compliance_access_review_campaigns"
    __table_args__ = (
        UniqueConstraint("tenant_id", "scope_key", "status", name="uq_compliance_campaign_scope"),
        Index("ix_compliance_campaign_due", "status", "due_at"),
        CheckConstraint(
            "status IN " + _sql_in(CAMPAIGN_STATUS),
            name="ck_compliance_campaign_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(120), nullable=False, default="all")
    reviewer_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    auto_revoke: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    due_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AccessReviewDecision(Base):
    """One user's outcome inside one campaign."""

    __tablename__ = "compliance_access_review_decisions"
    __table_args__ = (
        UniqueConstraint("campaign_id", "user_id", name="uq_compliance_review_decision"),
        Index("ix_compliance_review_decision_tenant", "tenant_id", "decision"),
        CheckConstraint(
            "decision IN " + _sql_in(REVIEW_DECISION),
            name="ck_compliance_review_decision",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("compliance_access_review_campaigns.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role_at_review: Mapped[str] = mapped_column(String(24), nullable=False, default="member")
    decision: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    auto_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")


# -------------------------------------------------------------- incidents ----

class Incident(Base):
    """A security or availability incident with a real lifecycle."""

    __tablename__ = "compliance_incidents"
    __table_args__ = (
        Index("ix_compliance_incident_status", "status", "detected_at"),
        Index("ix_compliance_incident_tenant", "tenant_id", "detected_at"),
        CheckConstraint(
            "status IN " + _sql_in(INCIDENT_STATUS),
            name="ck_compliance_incident_status",
        ),
        CheckConstraint(
            "severity IN " + _sql_in(INCIDENT_SEVERITY),
            name="ck_compliance_incident_severity",
        ),
        UniqueConstraint("reference", name="uq_compliance_incident_reference"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    reference: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(8), nullable=False, default="sev3")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="detected")
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="security")
    affected_tenants: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    personal_data_involved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    detected_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    triaged_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    contained_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class IncidentTimeline(Base):
    """Append-only timeline entry for one incident."""

    __tablename__ = "compliance_incident_timeline"
    __table_args__ = (
        Index("ix_compliance_incident_timeline_incident", "incident_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("compliance_incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class BreachClock(Base):
    """A statutory notification deadline computed from an incident's detect time."""

    __tablename__ = "compliance_breach_clocks"
    __table_args__ = (
        UniqueConstraint("incident_id", "tenant_id", "jurisdiction", name="uq_compliance_breach_clock"),
        Index("ix_compliance_breach_clock_due", "status", "deadline_at"),
        CheckConstraint(
            "status IN " + _sql_in(BREACH_STATUS),
            name="ck_compliance_breach_clock_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("compliance_incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    jurisdiction: Mapped[str] = mapped_column(String(32), nullable=False)
    obligation: Mapped[str] = mapped_column(String(120), nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    reminder_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_reminder_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    escalated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_proof: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


# ------------------------------------------------------------------- DSAR ----

class DsarRequest(Base):
    """One subject access or erasure request."""

    __tablename__ = "compliance_dsar_requests"
    __table_args__ = (
        Index("ix_compliance_dsar_tenant_status", "tenant_id", "status"),
        CheckConstraint("kind IN " + _sql_in(DSAR_KINDS), name="ck_compliance_dsar_kind"),
        CheckConstraint("status IN " + _sql_in(DSAR_STATUS), name="ck_compliance_dsar_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False, default="contact")
    subject_key: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="received")
    identity_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    legal_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    due_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bundle_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    bundle_path: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    bundle_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DsarLedger(Base):
    """Per-table result of an executed DSAR, so a rerun is measurable."""

    __tablename__ = "compliance_dsar_ledger"
    __table_args__ = (
        UniqueConstraint("request_id", "table_name", name="uq_compliance_dsar_ledger"),
        Index("ix_compliance_dsar_ledger_tenant", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("compliance_dsar_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    table_name: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    rows_affected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


# ------------------------------------------------------------------ HIPAA ----

class BreakGlassGrant(Base):
    """A time-boxed elevation to read PHI, with a mandatory reason."""

    __tablename__ = "compliance_break_glass_grants"
    __table_args__ = (
        Index("ix_compliance_break_glass_tenant_status", "tenant_id", "status"),
        CheckConstraint(
            "status IN " + _sql_in(BREAK_GLASS_STATUS),
            name="ck_compliance_break_glass_status",
        ),
        CheckConstraint("length(reason) >= 10", name="ck_compliance_break_glass_reason"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending_approval")
    dual_approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class PhiPolicy(Base):
    """One row per tenant: whether PHI handling rules apply and how."""

    __tablename__ = "compliance_phi_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_compliance_phi_policy_tenant"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    hipaa_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    baa_on_file: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    baa_reference: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    redact_webhooks: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    minimum_necessary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


__all__ = [
    "AUDIT_EXPORT_STATUS",
    "BREACH_STATUS",
    "BREAK_GLASS_STATUS",
    "CAMPAIGN_STATUS",
    "DSAR_KINDS",
    "DSAR_STATUS",
    "EVIDENCE_STATUS",
    "INCIDENT_SEVERITY",
    "INCIDENT_STATUS",
    "REKEY_STATUS",
    "RESIDENCY_STATUS",
    "REVIEW_DECISION",
    "SIEM_CURSOR_STATES",
    "SIEM_SINK_KINDS",
    "AccessReviewCampaign",
    "AccessReviewDecision",
    "AuditExport",
    "BreachClock",
    "BreakGlassGrant",
    "ControlStatus",
    "DsarLedger",
    "DsarRequest",
    "EvidenceSnapshot",
    "Incident",
    "IncidentTimeline",
    "KmsRekeyJob",
    "PhiPolicy",
    "ResidencyViolationLog",
    "SiemCursor",
    "SiemDeadLetter",
    "SiemSink",
    "TenantResidency",
]
