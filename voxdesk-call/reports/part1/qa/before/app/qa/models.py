"""QA persistence.

Reviews point at an existing call and at existing transcript turns. They do
not copy the call, the transcript body, or a recording. AI suggestions and
human scores are separate columns.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base

REVIEW_STATES = (
    "created",
    "assigned",
    "in_review",
    "submitted",
    "calculated",
    "finalized",
    "reopened",
    "cancelled",
)
TRANSITIONS = {
    "created": {"assigned", "in_review", "cancelled"},
    "assigned": {"in_review", "cancelled"},
    "in_review": {"submitted", "cancelled"},
    "submitted": {"calculated", "in_review"},
    "calculated": {"finalized", "in_review"},
    "finalized": {"reopened"},
    "reopened": {"in_review", "assigned", "cancelled"},
    "cancelled": set(),
}
TERMINAL = {"cancelled"}
OPEN_STATES = {"created", "assigned", "in_review", "submitted", "calculated", "reopened"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def legal(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


class Scorecard(Base):
    __tablename__ = "qa_scorecards"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "version", name="uq_qa_scorecards_version"),
        CheckConstraint("version >= 1", name="ck_qa_scorecards_version"),
        CheckConstraint(
            "pass_threshold >= 0 AND pass_threshold <= 10000",
            name="ck_qa_scorecards_threshold",
        ),
        CheckConstraint("status IN ('active', 'inactive', 'retired')", name="ck_qa_scorecards_status"),
        Index("ix_qa_scorecards_tenant", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    pass_threshold: Mapped[int] = mapped_column(Integer, default=7000, nullable=False)
    description: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "environment_id": str(self.environment_id) if self.environment_id else None,
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "pass_threshold": self.pass_threshold,
            "description": self.description,
        }


class ScorecardSection(Base):
    __tablename__ = "qa_scorecard_sections"
    __table_args__ = (
        UniqueConstraint("scorecard_id", "name", name="uq_qa_sections_name"),
        CheckConstraint("weight >= 1", name="ck_qa_sections_weight"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    scorecard_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("qa_scorecards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "scorecard_id": str(self.scorecard_id),
            "name": self.name,
            "weight": self.weight,
            "position": self.position,
        }


class ScorecardItem(Base):
    __tablename__ = "qa_scorecard_items"
    __table_args__ = (
        UniqueConstraint("section_id", "name", name="uq_qa_items_name"),
        CheckConstraint("weight >= 1", name="ck_qa_items_weight"),
        CheckConstraint("max_score > min_score", name="ck_qa_items_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    scorecard_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("qa_scorecards.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("qa_scorecard_sections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    min_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_score: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_na: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "scorecard_id": str(self.scorecard_id),
            "section_id": str(self.section_id),
            "name": self.name,
            "weight": self.weight,
            "min_score": self.min_score,
            "max_score": self.max_score,
            "required": self.required,
            "allow_na": self.allow_na,
            "position": self.position,
        }


class QAReview(Base):
    __tablename__ = "qa_reviews"
    __table_args__ = (
        UniqueConstraint("open_key", name="uq_qa_reviews_open"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_qa_reviews_idempotency"),
        CheckConstraint(
            "status IN ('created','assigned','in_review','submitted','calculated',"
            "'finalized','reopened','cancelled')",
            name="ck_qa_reviews_status",
        ),
        Index("ix_qa_reviews_tenant_status", "tenant_id", "status"),
        Index("ix_qa_reviews_call", "tenant_id", "call_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    scorecard_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("qa_scorecards.id", ondelete="RESTRICT"), nullable=False
    )
    scorecard_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="created", nullable=False)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    open_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    overall_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    calculation_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    prior_snapshots: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "environment_id": str(self.environment_id),
            "call_id": str(self.call_id),
            "queue_id": str(self.queue_id) if self.queue_id else None,
            "scorecard_id": str(self.scorecard_id),
            "scorecard_version": self.scorecard_version,
            "status": self.status,
            "assignee_id": str(self.assignee_id) if self.assignee_id else None,
            "priority": self.priority,
            "version": self.version,
            "overall_score": self.overall_score,
            "passed": self.passed,
            "calculation_snapshot": self.calculation_snapshot or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "finalized_at": self.finalized_at.isoformat() if self.finalized_at else None,
        }


class QAReviewItem(Base):
    __tablename__ = "qa_review_items"
    __table_args__ = (UniqueConstraint("review_id", "item_id", name="uq_qa_review_items"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("qa_reviews.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("qa_scorecard_items.id", ondelete="RESTRICT"), nullable=False
    )
    human_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    human_na: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ai_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_na: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    accepted_source: Mapped[str] = mapped_column(String(16), default="unset", nullable=False)
    override_reason: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "review_id": str(self.review_id),
            "item_id": str(self.item_id),
            "human_score": self.human_score,
            "human_na": self.human_na,
            "ai_score": self.ai_score,
            "ai_na": self.ai_na,
            "accepted_source": self.accepted_source,
            "override_reason": self.override_reason,
            "actor_id": str(self.actor_id) if self.actor_id else None,
        }


class QAEvidence(Base):
    __tablename__ = "qa_evidence"
    __table_args__ = (Index("ix_qa_evidence_review", "tenant_id", "review_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("qa_reviews.id", ondelete="CASCADE"), nullable=True
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("turns.id", ondelete="SET NULL"), nullable=True
    )
    speaker: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "review_id": str(self.review_id) if self.review_id else None,
            "call_id": str(self.call_id),
            "turn_id": str(self.turn_id) if self.turn_id else None,
            "speaker": self.speaker,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text_hash": self.text_hash,
            "evidence_type": self.evidence_type,
            "target_kind": self.target_kind,
            "target_id": str(self.target_id) if self.target_id else None,
        }


class QAFinding(Base):
    __tablename__ = "qa_findings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_qa_findings_idempotency"),
        Index("ix_qa_findings_review", "tenant_id", "review_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    review_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="low", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    source: Mapped[str] = mapped_column(String(16), default="human", nullable=False)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "review_id": str(self.review_id) if self.review_id else None,
            "call_id": str(self.call_id),
            "kind": self.kind,
            "summary": self.summary,
            "severity": self.severity,
            "status": self.status,
            "source": self.source,
            "confidence": self.confidence,
            "evidence_id": str(self.evidence_id) if self.evidence_id else None,
        }


class CompliancePolicy(Base):
    __tablename__ = "qa_compliance_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", "version", name="uq_qa_policies_version"),
        CheckConstraint("version >= 1", name="ck_qa_policies_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    required_phrase: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "code": self.code,
            "version": self.version,
            "category": self.category,
            "required_phrase": self.required_phrase,
            "severity": self.severity,
            "enabled": self.enabled,
        }


class ComplianceFinding(Base):
    __tablename__ = "qa_compliance_findings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_qa_compliance_idempotency"),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_qa_compliance_severity",
        ),
        CheckConstraint(
            "status IN ('suggested', 'open', 'confirmed', 'dismissed', 'remediated')",
            name="ck_qa_compliance_status",
        ),
        Index("ix_qa_compliance_call", "tenant_id", "call_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    review_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    policy_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("qa_compliance_policies.id", ondelete="SET NULL"), nullable=True
    )
    policy_code: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="suggested", nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewer_disposition: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    remediation_state: Mapped[str] = mapped_column(String(32), default="none", nullable=False)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "call_id": str(self.call_id),
            "review_id": str(self.review_id) if self.review_id else None,
            "policy_code": self.policy_code,
            "policy_version": self.policy_version,
            "status": self.status,
            "severity": self.severity,
            "confidence": self.confidence,
            "source": self.source,
            "reviewer_disposition": self.reviewer_disposition,
            "remediation_state": self.remediation_state,
            "evidence_id": str(self.evidence_id) if self.evidence_id else None,
        }


class CoachingSignal(Base):
    __tablename__ = "qa_coaching_signals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'assigned', 'acknowledged', 'completed', 'cancelled')",
            name="ck_qa_coaching_status",
        ),
        Index("ix_qa_coaching_agent", "tenant_id", "agent_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("qa_reviews.id", ondelete="SET NULL"), nullable=True
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    agent_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    recommendation: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="created", nullable=False)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "review_id": str(self.review_id) if self.review_id else None,
            "call_id": str(self.call_id),
            "agent_user_id": str(self.agent_user_id),
            "category": self.category,
            "priority": self.priority,
            "recommendation": self.recommendation,
            "status": self.status,
            "assignee_id": str(self.assignee_id) if self.assignee_id else None,
            "notes": self.notes,
            "evidence_id": str(self.evidence_id) if self.evidence_id else None,
            "version": self.version,
        }


class CalibrationSession(Base):
    __tablename__ = "qa_calibration_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'completed', 'cancelled')",
            name="ck_qa_calibration_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    reference_review_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    resolution_notes: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "status": self.status,
            "reference_review_id": str(self.reference_review_id) if self.reference_review_id else None,
            "resolution_notes": self.resolution_notes,
        }


class CalibrationScore(Base):
    __tablename__ = "qa_calibration_scores"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "reviewer_id",
            "review_id",
            "item_key",
            name="uq_qa_calibration_score",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("qa_calibration_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    review_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    item_key: Mapped[str] = mapped_column(String(40), default="overall", nullable=False)
    reference_score: Mapped[int] = mapped_column(Integer, nullable=False)
    reviewer_score: Mapped[int] = mapped_column(Integer, nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str] = mapped_column(String(300), default="", nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "session_id": str(self.session_id),
            "review_id": str(self.review_id),
            "reviewer_id": str(self.reviewer_id),
            "item_key": self.item_key,
            "reference_score": self.reference_score,
            "reviewer_score": self.reviewer_score,
            "delta": self.delta,
            "notes": self.notes,
        }


class SamplingRule(Base):
    __tablename__ = "qa_sampling_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_qa_sampling_name"),
        CheckConstraint(
            "kind IN ('percentage', 'count', 'min_per_agent', 'disposition', 'queue', "
            "'agent', 'compliance', 'low_qos')",
            name="ck_qa_sampling_kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    min_per_agent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disposition: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    queue_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    agent_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    qos_threshold: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "name": self.name,
            "kind": self.kind,
            "percent": self.percent,
            "sample_count": self.sample_count,
            "min_per_agent": self.min_per_agent,
            "disposition": self.disposition,
            "queue_id": str(self.queue_id) if self.queue_id else None,
            "agent_user_id": str(self.agent_user_id) if self.agent_user_id else None,
            "qos_threshold": self.qos_threshold,
            "enabled": self.enabled,
        }


class SampleSelection(Base):
    __tablename__ = "qa_sample_selections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "rule_id",
            "window_key",
            "call_id",
            name="uq_qa_sample_once",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("qa_sampling_rules.id", ondelete="CASCADE"), nullable=False
    )
    window_key: Mapped[str] = mapped_column(String(40), nullable=False)
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    review_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    selected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "rule_id": str(self.rule_id),
            "window_key": self.window_key,
            "call_id": str(self.call_id),
            "review_id": str(self.review_id) if self.review_id else None,
        }


class AutoReviewRun(Base):
    __tablename__ = "qa_auto_review_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_qa_auto_review_key"),
        Index("ix_qa_auto_review_review", "tenant_id", "review_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("qa_reviews.id", ondelete="CASCADE"), nullable=False
    )
    scorecard_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    scorecard_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    provider: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), default="qa-auto-review-v1", nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_class: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    suggestion: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "review_id": str(self.review_id),
            "scorecard_id": str(self.scorecard_id),
            "scorecard_version": self.scorecard_version,
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "latency_ms": self.latency_ms,
            "token_count": self.token_count,
            "attempt_count": self.attempt_count,
            "error_class": self.error_class,
            "job_id": str(self.job_id) if self.job_id else None,
            "suggestion": self.suggestion or {},
        }


class SentimentResult(Base):
    __tablename__ = "qa_sentiment_results"
    __table_args__ = (
        UniqueConstraint("tenant_id", "scope_key", name="uq_qa_sentiment_scope"),
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_qa_sentiment_confidence"),
        Index("ix_qa_sentiment_call", "tenant_id", "call_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(120), nullable=False)
    label: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "call_id": str(self.call_id),
            "turn_id": str(self.turn_id) if self.turn_id else None,
            "scope": self.scope,
            "label": self.label,
            "confidence": self.confidence,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "evidence_id": str(self.evidence_id) if self.evidence_id else None,
            "certainty": "classification",
        }


class TopicResult(Base):
    __tablename__ = "qa_topic_results"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "call_id",
            "taxonomy_version",
            "label",
            name="uq_qa_topics_label",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_qa_topics_confidence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    taxonomy_version: Mapped[str] = mapped_column(String(40), nullable=False)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[str] = mapped_column(String(16), default="secondary", nullable=False)
    provider: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    model: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    evidence_turn_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "call_id": str(self.call_id),
            "taxonomy_version": self.taxonomy_version,
            "label": self.label,
            "confidence": self.confidence,
            "rank": self.rank,
            "provider": self.provider,
            "model": self.model,
            "evidence_turn_ids": list(self.evidence_turn_ids or []),
        }
