"""Durable translation job, segment, glossary and retry projections."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint, event as _sa_event, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.governance.context import GovernanceScope
from app.specialized_agents.executor import SpecializedExecutionRecord
from app.tenancy.isolation import BoundaryDenied


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class TranslationJobRecord(Base):
    __tablename__ = "specialized_agent_translation_jobs"
    __table_args__ = {"extend_existing": True}
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    source_language: Mapped[str] = mapped_column(String(32), nullable=False)
    target_language: Mapped[str] = mapped_column(String(32), nullable=False)
    glossary_version: Mapped[str] = mapped_column(String(100), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    segment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_status: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="completed")
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_state: Mapped[str] = mapped_column(String(24), nullable=False)
    total_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class GlossaryVersionRecord(Base):
    __tablename__ = "specialized_agent_glossary_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    entries: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class TranslationSegmentRecord(Base):
    __tablename__ = "specialized_agent_translation_segments"
    __table_args__ = (UniqueConstraint("execution_id", "segment_index", name="uq_translation_segment_index"), Index("ix_translation_segments_scope", "tenant_id", "organization_id", "environment_id"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    segment_id: Mapped[str] = mapped_column(String(200), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    target_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    target_length: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quality_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class TranslationProgressRecord(Base):
    __tablename__ = "specialized_agent_translation_progress"
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    total_segments: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_segments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    review_state: Mapped[str] = mapped_column(String(24), nullable=False)
    last_error: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class TranslationAttemptRecord(Base):
    __tablename__ = "specialized_agent_translation_attempts"
    __table_args__ = (UniqueConstraint("execution_id", "segment_index", "attempt_number", name="uq_translation_attempt"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    error_code: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    target_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


async def persist_translation(session: AsyncSession, scope: GovernanceScope, *, execution_id: uuid.UUID, output: dict, dedupe_key: str, source_language: str, target_language: str, glossary_version: str, glossary_entries: list[dict] | None = None, source_lengths: dict[str, int] | None = None) -> TranslationJobRecord:
    execution = await session.scalar(select(SpecializedExecutionRecord).where(SpecializedExecutionRecord.id == execution_id, SpecializedExecutionRecord.tenant_id == scope.tenant_id, SpecializedExecutionRecord.organization_id == scope.organization_id, SpecializedExecutionRecord.environment_id == scope.environment_id, SpecializedExecutionRecord.agent_type == "translation"))
    if execution is None:
        raise BoundaryDenied()
    existing = await session.scalar(select(TranslationJobRecord).where(TranslationJobRecord.tenant_id == scope.tenant_id, TranslationJobRecord.organization_id == scope.organization_id, TranslationJobRecord.environment_id == scope.environment_id, TranslationJobRecord.dedupe_key == dedupe_key))
    if existing is not None:
        return existing
    entries = list(glossary_entries or [])
    glossary = await session.scalar(select(GlossaryVersionRecord).where(GlossaryVersionRecord.tenant_id == scope.tenant_id, GlossaryVersionRecord.organization_id == scope.organization_id, GlossaryVersionRecord.environment_id == scope.environment_id, GlossaryVersionRecord.version == glossary_version))
    if glossary is None:
        glossary = GlossaryVersionRecord(tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, version=glossary_version, entries=entries, active=True)
        session.add(glossary)
    segments = list(output.get("segments", []))
    for index, item in enumerate(segments):
        session.add(TranslationSegmentRecord(execution_id=execution_id, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, segment_index=index, segment_id=str(item.get("segment_id", index)), source_fingerprint=str(item.get("source_fingerprint", "")), target_fingerprint=str(item.get("target_fingerprint", "")) or None, source_length=int((source_lengths or {}).get(str(item.get("segment_id", index)), 0)), target_length=len(str(item.get("translated_text", ""))), status="completed", retry_count=0, review_required=bool(output.get("review_required")), quality_metadata={"quality_flags": [flag for flag in output.get("quality_flags", []) if isinstance(flag, dict) and flag.get("segment_id") == item.get("segment_id")] }))
    count = len(segments)
    session.add(TranslationProgressRecord(execution_id=execution_id, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, total_segments=count, completed_segments=count, failed_segments=0, retry_count=0, state="review_required" if output.get("review_required") else "completed", review_state=str(output.get("review_state", "not_required"))))
    row = TranslationJobRecord(execution_id=execution_id, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, source_language=source_language, target_language=target_language, glossary_version=glossary_version, dedupe_key=dedupe_key, segment_count=count, quality_status=str(output.get("quality_status", "not_available")), status="review_required" if output.get("review_required") else "completed", review_required=bool(output.get("review_required")), review_state=str(output.get("review_state", "not_required")), total_segments=count, completed_segments=count, failed_segments=0, completed_at=None if output.get("review_required") else _now(), metadata_json={"provider": output.get("provider"), "model": output.get("model")})
    session.add(row)
    await session.flush()
    return row


@_sa_event.listens_for(GlossaryVersionRecord, "before_update")
def _immutable_glossary_update(mapper, connection, target) -> None:
    raise ValueError("glossary versions are immutable")


@_sa_event.listens_for(GlossaryVersionRecord, "before_delete")
def _immutable_glossary_delete(mapper, connection, target) -> None:
    raise ValueError("glossary versions are immutable")
