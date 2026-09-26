"""Transcript job lifecycle.

The speech engine remains ``app.agent.stt``. This module records status,
ownership and retry. It does not call Deepgram and it does not invent text.
Completion is allowed only when the call already has transcript turns.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.core.config import settings
from app.db.models import Base, Turn, UserRole
from app.tenancy.isolation import Forbidden, NotFound

_MAX_ATTEMPTS = 3


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TranscriptJob(Base):
    __tablename__ = "transcript_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_transcript_jobs_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    recording_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), default="deepgram", nullable=False)
    model: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_class: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "call_id": str(self.call_id),
            "recording_id": str(self.recording_id) if self.recording_id else None,
            "provider": self.provider,
            "model": self.model,
            "language": self.language,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "error_class": self.error_class,
            "text": None,
        }


async def enqueue(
    session, call, *, recording_id: uuid.UUID | None = None, language: str = ""
) -> TranscriptJob:
    from app.agent.errors import ProviderConfigurationError
    from app.agent.stt import validate_stt_config

    job = TranscriptJob(
        tenant_id=call.tenant_id,
        call_id=call.id,
        recording_id=recording_id,
        provider="deepgram",
        model=(settings.deepgram_model or "")[:64],
        language=(language or getattr(call, "language", "") or "")[:16],
        status="queued",
        attempt_count=1,
    )
    try:
        validate_stt_config()
    except ProviderConfigurationError:
        job.status = "failed"
        job.error_class = "configuration"
    session.add(job)
    await session.flush()
    return job


async def mark_processing(session, job: TranscriptJob) -> TranscriptJob:
    if job.status != "queued":
        return job
    job.status = "processing"
    job.updated_at = _now()
    await session.flush()
    return job


async def complete_if_turns_exist(session, job: TranscriptJob) -> TranscriptJob:
    if job.tenant_id is None:
        raise NotFound()
    turns = (
        await session.execute(select(Turn.id).where(Turn.call_id == job.call_id).limit(1))
    ).first()
    if turns is None:
        job.status = "failed"
        job.error_class = "no_transcript"
        job.updated_at = _now()
        await session.flush()
        return job
    job.status = "completed"
    job.error_class = ""
    job.updated_at = _now()
    await session.flush()
    return job


async def retry(session, job: TranscriptJob) -> TranscriptJob:
    if job.status != "failed":
        return job
    if job.attempt_count >= _MAX_ATTEMPTS:
        job.error_class = job.error_class or "attempts_exhausted"
        await session.flush()
        return job
    job.attempt_count += 1
    job.status = "queued"
    job.updated_at = _now()
    await session.flush()
    return job


async def get_owned(
    session, tenant_id: uuid.UUID, job_id: uuid.UUID, role: UserRole | None
) -> TranscriptJob:
    row = await session.get(TranscriptJob, job_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    if role is None or not has_permission(role, Permission.TRANSCRIPT_READ):
        raise Forbidden()
    return row
