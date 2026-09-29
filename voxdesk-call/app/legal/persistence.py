"""Durable legal result projection over the existing governed execution row."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.governance.context import GovernanceScope
from app.governance.hashing import sha256_hex
from app.specialized_agents.executor import SpecializedExecutionRecord
from app.tenancy.isolation import BoundaryDenied


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class LegalReviewRecord(Base):
    __tablename__ = "specialized_agent_legal_reviews"
    __table_args__ = {"extend_existing": True}
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(String(200), nullable=False)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_state: Mapped[str] = mapped_column(String(24), nullable=False)
    source_fingerprints: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    result_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="review_required")
    disclaimer: Mapped[str] = mapped_column(Text, nullable=False, default="")
    review_required: Mapped[bool] = mapped_column(nullable=False, default=False)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class LegalFindingRecord(Base):
    __tablename__ = "specialized_agent_legal_findings"
    __table_args__ = (Index("ix_legal_findings_scope", "tenant_id", "organization_id", "environment_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    finding_index: Mapped[int] = mapped_column(Integer, nullable=False)
    clause: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(200), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    extracted_text_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    location: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    recommended_follow_up: Mapped[str] = mapped_column(Text, nullable=False)
    citation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("specialized_agent_legal_citations.id", ondelete="SET NULL"), nullable=True)


class LegalCitationRecord(Base):
    __tablename__ = "specialized_agent_legal_citations"
    __table_args__ = (UniqueConstraint("execution_id", "document_id", "chunk_id", "content_fingerprint", name="uq_legal_citation_source"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(String(200), nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_title: Mapped[str] = mapped_column(String(500), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    character_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    character_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retrieval_timestamp: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class LegalOutcomeRecord(Base):
    __tablename__ = "specialized_agent_legal_outcomes"
    __table_args__ = (UniqueConstraint("execution_id", "outcome_version", name="uq_legal_outcome_version"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=False)
    review_case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("review_cases.id", ondelete="SET NULL"), nullable=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    outcome_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("governance_evidence_events.id"), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


async def persist_review(session: AsyncSession, scope: GovernanceScope, *, execution_id: uuid.UUID, output: dict, source_fingerprints: list[str] | None = None) -> LegalReviewRecord:
    execution = await session.scalar(select(SpecializedExecutionRecord).where(SpecializedExecutionRecord.id == execution_id, SpecializedExecutionRecord.tenant_id == scope.tenant_id, SpecializedExecutionRecord.organization_id == scope.organization_id, SpecializedExecutionRecord.environment_id == scope.environment_id, SpecializedExecutionRecord.agent_type == "legal"))
    if execution is None:
        raise BoundaryDenied()
    existing = await session.get(LegalReviewRecord, execution_id)
    if existing is not None:
        return existing
    citations_by_key: dict[tuple[str, str, str], LegalCitationRecord] = {}
    for item in output.get("citations", []):
        retrieved_at = item.get("retrieval_timestamp")
        if isinstance(retrieved_at, str):
            retrieved_at = dt.datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
        citation = LegalCitationRecord(execution_id=execution_id, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, document_id=str(item.get("document_id", "")), chunk_id=str(item.get("chunk_id", "")), source_title=str(item.get("source_title", "")), page_number=item.get("page_number"), character_start=item.get("character_start"), character_end=item.get("character_end"), retrieval_timestamp=retrieved_at, content_fingerprint=str(item.get("content_fingerprint", "")))
        session.add(citation)
        citations_by_key[(citation.document_id, citation.chunk_id, citation.content_fingerprint)] = citation
    await session.flush()
    for index, item in enumerate(output.get("findings", [])):
        ref = item.get("source_reference") or {}
        citation = citations_by_key.get((str(ref.get("document_id", "")), str(ref.get("chunk_id", "")), str(ref.get("content_fingerprint", ""))))
        session.add(LegalFindingRecord(execution_id=execution_id, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, finding_index=index, clause=str(item.get("clause", "")), category=str(item.get("category", "")), issue_type=str(item.get("issue_type", "")), severity=str(item.get("severity", "")), confidence=float(item.get("confidence", 0.0)), extracted_text_reference=str(item.get("extracted_text_reference", "")), location=dict(item.get("location") or {}), rationale=str(item.get("rationale", "")), recommended_follow_up=str(item.get("recommended_follow_up", "")), citation_id=citation.id if citation else None))
    persisted_fingerprints = list(source_fingerprints or output.get("source_fingerprints", []))
    record = LegalReviewRecord(execution_id=execution_id, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, document_id=str(output.get("document_id", "")), finding_count=len(output.get("findings", [])), review_state=str(output.get("review_state", "not_required")), source_fingerprints=persisted_fingerprints, result_metadata={"source_count": output.get("source_count", 0), "legal_certainty": output.get("legal_certainty", "not_claimed"), "disclaimer": output.get("disclaimer", "")}, source_fingerprint=sha256_hex(sorted(persisted_fingerprints)) if persisted_fingerprints else execution.input_fingerprint, status="review_required" if output.get("review_required") else "completed", disclaimer=str(output.get("disclaimer", "")), review_required=bool(output.get("review_required")), completed_at=None if output.get("review_required") else _now())
    session.add(record)
    await session.flush()
    return record
