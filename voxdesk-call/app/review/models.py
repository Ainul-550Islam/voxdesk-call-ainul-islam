"""Tenant and environment-scoped durable human review records."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.db.models import Base
from .enums import ReviewCaseStatus, ReviewAssignmentStatus, ReviewPriority


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class ReviewCase(Base):
    __tablename__ = "review_cases"
    __table_args__ = (
        Index("ix_review_cases_scope_status", "tenant_id", "organization_id", "environment_id", "status"),
        Index("ix_review_cases_execution", "tenant_id", "execution_id"),
        Index("ix_review_cases_queue", "tenant_id", "environment_id", "status", "priority", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    case_type: Mapped[str] = mapped_column(String(64), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(80), nullable=False, default="specialized_execution")
    subject_id: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ReviewCaseStatus.PENDING.value)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default=ReviewPriority.NORMAL.value)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    requested_controls: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    requested_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewAssignment(Base):
    __tablename__ = "review_assignments"
    __table_args__ = (
        UniqueConstraint("review_case_id", "reviewer_id", "assignment_version", name="uq_review_assignment_version"),
        Index("ix_review_assignments_reviewer", "tenant_id", "reviewer_id", "status"),
        Index("ix_review_assignments_case", "tenant_id", "review_case_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column("review_case_id", ForeignKey("review_cases.id", ondelete="CASCADE"), nullable=False)
    review_case_id = synonym("case_id")
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ReviewAssignmentStatus.ACTIVE.value)
    assignment_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    assigned_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (
        UniqueConstraint("review_case_id", "decision_version", name="uq_review_decision_version"),
        Index("ix_review_decisions_scope", "tenant_id", "organization_id", "environment_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column("review_case_id", ForeignKey("review_cases.id", ondelete="CASCADE"), nullable=False)
    review_case_id = synonym("case_id")
    assignment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("review_assignments.id", ondelete="RESTRICT"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decision: Mapped[str] = mapped_column(String(24), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    policy_decision_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("governance_policy_decisions.id"), nullable=True)
    evidence_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("governance_evidence_events.id"), nullable=True)
    decision_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
