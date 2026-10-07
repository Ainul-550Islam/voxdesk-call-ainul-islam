"""Durable enterprise governance persistence.

All rows are tenant-scoped where the operation is customer-scoped. Nullable
tenant/organization fields are reserved for explicitly global policy/catalog
records; query services always bind those records to the caller's hierarchy.
No model in this module replaces the existing ``app.ai.models`` contracts.
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
)
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.db.models import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class GovernancePolicy(Base):
    __tablename__ = "governance_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "policy_type", "name", "version", name="uq_gov_policy_version"),
        Index("ix_gov_policy_tenant_type_status", "tenant_id", "policy_type", "status"),
        CheckConstraint(
            "tenant_id IS NOT NULL OR organization_id IS NOT NULL", name="ck_gov_policy_scope"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    policy_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    rules: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    effective_from: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GovernancePolicyDecision(Base):
    __tablename__ = "governance_policy_decisions"
    __table_args__ = (
        Index("ix_gov_decision_scope_created", "tenant_id", "organization_id", "created_at"),
        Index("ix_gov_decision_correlation", "correlation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    principal_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("governance_policies.id"), nullable=True)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(100), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    output_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class RiskAssessment(Base):
    __tablename__ = "governance_risk_assessments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "subject_type", "subject_id", "version", name="uq_gov_risk_version"),
        Index("ix_gov_risk_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    subject_type: Mapped[str] = mapped_column(String(60), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tier: Mapped[str] = mapped_column(String(24), nullable=False)
    # ``risk_tier`` is the public vocabulary required by governance clients;
    # ``tier`` remains the compatibility attribute used by the first additive
    # service implementation.
    risk_tier = synonym("tier")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    factors: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    required_controls: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    assessed_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    assessed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class ModelRegistry(Base):
    __tablename__ = "governance_model_registries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "model_name", name="uq_gov_model_registry_name"),
        Index("ix_gov_model_registry_scope_status", "tenant_id", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    family: Mapped[str | None] = mapped_column(String(200), nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="registered")
    risk_tier: Mapped[str] = mapped_column(String(24), nullable=False)
    intended_use: Mapped[str] = mapped_column(Text, nullable=False)
    data_classes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    approved_for_channels: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    approved_for_environments: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class ModelVersion(Base):
    __tablename__ = "governance_model_versions"
    __table_args__ = (
        UniqueConstraint("registry_id", "version", name="uq_gov_model_version"),
        Index("ix_gov_model_version_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    registry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("governance_model_registries.id"), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    artifact_uri: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    input_modalities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    output_modalities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="registered")
    evaluation_status: Mapped[str] = mapped_column(String(24), nullable=False, default="not_evaluated")
    approval_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    evaluation_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LineageRecord(Base):
    __tablename__ = "governance_lineage_records"
    __table_args__ = (
        Index("ix_gov_lineage_tenant_created", "tenant_id", "created_at"),
        Index("ix_gov_lineage_correlation", "correlation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    subject_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_registry_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    model_version_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    output_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tool_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tool_fingerprints: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    source_references: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    parent_lineage_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class EvidenceEvent(Base):
    __tablename__ = "governance_evidence_events"
    __table_args__ = (
        UniqueConstraint("chain_scope", "sequence", name="uq_gov_evidence_sequence"),
        UniqueConstraint("event_hash", name="uq_gov_evidence_hash"),
        Index("ix_gov_evidence_tenant_created", "tenant_id", "created_at"),
        Index("ix_gov_evidence_chain", "chain_scope", "sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    chain_scope: Mapped[str] = mapped_column(String(200), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_number = synonym("sequence")
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    subject_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    chain_hash = synonym("event_hash")
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


# SQLite and other non-PostgreSQL environments do not have the migration's
# trigger. These ORM guards keep the same append-only contract in focused tests
# and local development; PostgreSQL gets both this guard and the database
# trigger.
from sqlalchemy import event  # noqa: E402


@event.listens_for(EvidenceEvent, "before_update")
def _reject_evidence_update(mapper, connection, target) -> None:
    raise ValueError("governance evidence is append-only")


@event.listens_for(EvidenceEvent, "before_delete")
def _reject_evidence_delete(mapper, connection, target) -> None:
    raise ValueError("governance evidence is append-only")


class RetentionRule(Base):
    __tablename__ = "governance_retention_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "evidence_type", "version", name="uq_gov_retention_version"),
        Index("ix_gov_retention_scope_active", "tenant_id", "active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    evidence_type: Mapped[str] = mapped_column(String(80), nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    legal_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class ResidencyIntent(Base):
    __tablename__ = "governance_residency_intents"
    __table_args__ = (
        Index("ix_gov_residency_tenant_status", "tenant_id", "status"),
        UniqueConstraint("tenant_id", "requested_region", "version", name="uq_gov_residency_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    requested_region: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="requested")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    physical_residency_proven: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    authoritative_verifier: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verification_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verification_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class Attestation(Base):
    __tablename__ = "governance_attestations"
    __table_args__ = (
        Index("ix_gov_attestation_tenant_subject", "tenant_id", "subject_type", "subject_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    attestation_type: Mapped[str] = mapped_column(String(80), nullable=False, default="evidence_attestation")
    subject_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(200), nullable=False)
    period_start: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    issuer: Mapped[str] = mapped_column(String(200), nullable=False)
    issued_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    claims: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    evidence_root: Mapped[str] = mapped_column(String(64), nullable=False)
    from_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issued_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


# Public conceptual name used by governance integrations. The table remains
# ``governance_attestations`` for compatibility with the initial foundation.
GovernanceAttestation = Attestation


class EvidencePackage(Base):
    __tablename__ = "governance_evidence_packages"
    __table_args__ = (
        Index("ix_gov_package_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="generated")
    from_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_root: Mapped[str] = mapped_column(String(64), nullable=False)
    package_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
