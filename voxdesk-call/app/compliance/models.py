from __future__ import annotations
import datetime as dt
import uuid
from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, Boolean, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.models import Base

def now(): return dt.datetime.now(dt.timezone.utc)

class ComplianceFramework(Base):
    __tablename__ = "compliance_frameworks"
    __table_args__ = (Index("ix_compliance_framework_scope", "tenant_id", "organization_id", "environment_id", "framework_type", "status"), UniqueConstraint("tenant_id", "environment_id", "framework_type", "name", "version", name="uq_compliance_framework_version"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("environments.id",ondelete="RESTRICT"), nullable=False, index=True)
    framework_type: Mapped[str] = mapped_column(String(24), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    configuration: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    effective_from: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)

class ComplianceControl(Base):
    __tablename__ = "compliance_controls"
    __table_args__ = (UniqueConstraint("framework_id", "control_key", name="uq_compliance_control_key"), Index("ix_compliance_control_framework", "framework_id", "active"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    framework_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("compliance_frameworks.id", ondelete="CASCADE"), nullable=False, index=True)
    control_key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    severity: Mapped[str] = mapped_column(String(24), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(1000))
    required_evidence: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rules: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)

class ComplianceFinding(Base):
    __tablename__ = "compliance_findings"
    __table_args__ = (Index("ix_compliance_finding_scope", "tenant_id", "organization_id", "environment_id", "status", "created_at"), UniqueConstraint("tenant_id","environment_id","framework_id","control_id","subject_type","subject_id","evidence_fingerprint",name="uq_compliance_finding_dedupe"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    framework_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("compliance_frameworks.id", ondelete="CASCADE"), nullable=False, index=True)
    control_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("compliance_controls.id", ondelete="RESTRICT"), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("environments.id",ondelete="RESTRICT"), nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(24), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(1000))
    evidence_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)

class Remediation(Base):
    __tablename__ = "compliance_remediations"
    __table_args__ = (Index("ix_compliance_remediation_scope", "tenant_id", "organization_id", "environment_id", "status"), UniqueConstraint("finding_id",name="uq_compliance_remediation_finding"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    finding_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("compliance_findings.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    due_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_reference: Mapped[str | None] = mapped_column(String(1000))
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now, nullable=False)
