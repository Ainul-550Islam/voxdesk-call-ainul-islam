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
from app.db.models import Base, Call, Speaker, Tenant, Turn, UserRole
from app.gdpr.redact import redact
from app.telephony.recording_policy import effective
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


CallTranscriptJob = TranscriptJob


async def enqueue(
    session, call, *, recording_id: uuid.UUID | None = None, language: str = ""
) -> TranscriptJob:
    from app.agent.errors import ProviderConfigurationError
    from app.agent.language import resolve_language_config
    from app.agent.stt import validate_stt_config

    raw_lang = language or getattr(call, "language", "") or ""
    lang_profile = resolve_language_config(raw_lang) if raw_lang else None
    resolved_model = (
        lang_profile.stt_model
        if lang_profile and lang_profile.is_multilingual
        else (settings.deepgram_model or "")
    )
    resolved_lang = (
        lang_profile.stt_language if lang_profile else raw_lang
    )

    job = TranscriptJob(
        tenant_id=call.tenant_id,
        call_id=call.id,
        recording_id=recording_id,
        provider="deepgram",
        model=resolved_model[:64],
        language=resolved_lang[:16],
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


def resolve_transcription_options(
    language: str | None = "en-US",
    boosted_keywords: list[tuple[str, float]] | tuple[tuple[str, float], ...] | None = None,
) -> dict[str, object]:
    """Return STT transcription options including `multi` language detection and `keywords` (Sub-Phase 2G)."""
    from app.agent.language import resolve_language_config
    from app.agent.providers.stt_providers import format_boosted_keywords

    prof = resolve_language_config(language)
    return {
        "language": prof.stt_language,
        "model": prof.stt_model,
        "multilingual": prof.is_multilingual,
        "keywords": format_boosted_keywords(boosted_keywords),
    }


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


class TranscriptUnavailable(ValueError):
    """Safe reason code, without transcript/provider content."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


async def should_redact_pii(
    session,
    call: Call,
    *,
    agent_id: str | uuid.UUID | None = None,
) -> bool:
    """Return True when the effective RecordingPolicy for ``call`` enables PII redaction."""
    tenant = await session.get(Tenant, call.tenant_id)
    resolved_agent_id = agent_id or getattr(call, "agent_id", None)
    policy = await effective(
        session,
        tenant,
        environment_id=getattr(call, "environment_id", None),
        agent_id=resolved_agent_id,
    )
    return bool(policy.get("redact_pii", True))


async def redact_turn_text(
    session,
    call: Call,
    text: str,
    *,
    agent_id: str | uuid.UUID | None = None,
) -> str:
    """Redact ``text`` according to the effective RecordingPolicy before persistence."""
    if not text:
        return text
    if await should_redact_pii(session, call, agent_id=agent_id):
        return redact(text).text
    return text


async def record_turn(
    session,
    call: Call,
    *,
    speaker: Speaker,
    text: str,
    latency_ms: float | None = None,
    agent_id: str | uuid.UUID | None = None,
) -> Turn:
    """Persist a ``Turn`` row after applying policy-governed PII redaction."""
    clean_text = await redact_turn_text(session, call, text, agent_id=agent_id)
    turn = Turn(
        call_id=call.id,
        speaker=speaker,
        text=clean_text,
        latency_ms=latency_ms,
    )
    session.add(turn)
    await session.flush()
    return turn


async def finalize_stored_turns(
    session,
    call,
    *,
    snapshot: dict | None = None,
    agent_id: str | uuid.UUID | None = None,
) -> tuple[str, dict]:
    """Freeze existing text evidence for post-call work; never synthesize STT.

    The caller holds the Call lock. A checkpoint is references plus a digest,
    not another copy of customer text. Retry refuses changed/deleted evidence
    rather than combining old and new transcripts or resurrecting erased text.
    Long calls are explicitly unsupported until a governed chunking policy is
    implemented; nothing is silently truncated to fit the model input bound.
    """
    import hashlib
    import json

    query = (select(Turn).join(Call, Call.id == Turn.call_id).where(
        Call.id == call.id, Call.tenant_id == call.tenant_id,
        Call.environment_id == call.environment_id,
    ).order_by(Turn.created_at, Turn.id))
    if snapshot is not None:
        ids = snapshot.get("turn_ids") or []
        if not ids or len(ids) > 1000:
            raise TranscriptUnavailable("transcript_unavailable")
        query = query.where(Turn.id.in_([uuid.UUID(value) for value in ids]))
    turns = list((await session.scalars(query.limit(1001))).all())
    if not turns or not any((row.text or "").strip() for row in turns):
        raise TranscriptUnavailable("no_transcript")
    if len(turns) > 1000:
        raise TranscriptUnavailable("transcript_too_large")
    redact_enabled = await should_redact_pii(session, call, agent_id=agent_id)
    if redact_enabled:
        for row in turns:
            if row.text:
                scrubbed = redact(row.text).text
                if row.text != scrubbed:
                    row.text = scrubbed
    evidence = [(str(row.id), getattr(row.speaker, "value", str(row.speaker)), row.text or "") for row in turns]
    text = "\n".join(f"{speaker}: {value}" for _, speaker, value in evidence)
    if len(text) > 6300:
        raise TranscriptUnavailable("transcript_too_large")
    digest = hashlib.sha256(json.dumps(evidence, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    result = {
        "turn_ids": [row[0] for row in evidence],
        "turn_count": len(evidence),
        "sha256": digest,
        "pii_redacted": redact_enabled,
    }
    if snapshot is not None and (snapshot.get("sha256") != digest or snapshot.get("turn_ids") != result["turn_ids"]):
        raise TranscriptUnavailable("transcript_changed")
    # Existing asynchronous transcription records may now be closed from real
    # stored turns. Failed provider/configuration records are not recertified.
    jobs = list((await session.scalars(select(TranscriptJob).where(
        TranscriptJob.tenant_id == call.tenant_id, TranscriptJob.call_id == call.id,
        TranscriptJob.status.in_(("queued", "processing")),
    ))).all())
    for job in jobs:
        await complete_if_turns_exist(session, job)
    return text, result
