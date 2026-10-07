"""Retry-safe translation persistence and existing durable-job enqueue adapter."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.governance.context import GovernanceScope
from app.governance.hashing import sha256_hex
from app.tenancy.isolation import BoundaryDenied, Conflict, ValidationFailed
from .persistence import (
    GlossaryVersionRecord, TranslationAttemptRecord, TranslationJobRecord,
    TranslationProgressRecord, TranslationSegmentRecord, persist_translation,
)


async def get_or_create_glossary(session: AsyncSession, scope: GovernanceScope, *, version: str, entries: list[dict]) -> GlossaryVersionRecord:
    if not version or len(version) > 100:
        raise ValidationFailed("glossary version is invalid")
    existing = await session.scalar(select(GlossaryVersionRecord).where(GlossaryVersionRecord.tenant_id == scope.tenant_id, GlossaryVersionRecord.organization_id == scope.organization_id, GlossaryVersionRecord.environment_id == scope.environment_id, GlossaryVersionRecord.version == version))
    if existing is not None:
        if entries and sha256_hex(existing.entries) != sha256_hex(entries):
            raise Conflict("glossary version already exists with different contents")
        return existing
    row = GlossaryVersionRecord(tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, version=version, entries=entries, active=True)
    session.add(row)
    await session.flush()
    return row


async def lookup_glossary(session: AsyncSession, scope: GovernanceScope, *, version: str) -> GlossaryVersionRecord | None:
    return await session.scalar(select(GlossaryVersionRecord).where(GlossaryVersionRecord.tenant_id == scope.tenant_id, GlossaryVersionRecord.organization_id == scope.organization_id, GlossaryVersionRecord.environment_id == scope.environment_id, GlossaryVersionRecord.version == version, GlossaryVersionRecord.active.is_(True)))


async def remove_glossary(session: AsyncSession, scope: GovernanceScope, *, version: str) -> None:
    # Historical versions are immutable. Even an inactive unreferenced record
    # is retained here because no retention authorization API is defined.
    row = await session.scalar(select(GlossaryVersionRecord).where(GlossaryVersionRecord.tenant_id == scope.tenant_id, GlossaryVersionRecord.organization_id == scope.organization_id, GlossaryVersionRecord.environment_id == scope.environment_id, GlossaryVersionRecord.version == version).with_for_update())
    if row is None:
        raise BoundaryDenied()
    raise Conflict("glossary versions are immutable and cannot be deleted through this service")


async def record_translation_result(session: AsyncSession, scope: GovernanceScope, *, execution_id: uuid.UUID, output: dict, idempotency_key: str, source_language: str, target_language: str, glossary_version: str, glossary_entries: list[dict] | None = None, source_lengths: dict[str, int] | None = None) -> TranslationJobRecord:
    return await persist_translation(session, scope, execution_id=execution_id, output=output, dedupe_key=idempotency_key, source_language=source_language, target_language=target_language, glossary_version=glossary_version, glossary_entries=glossary_entries, source_lengths=source_lengths)


async def retry_failed_segments(session: AsyncSession, scope: GovernanceScope, *, execution_id: uuid.UUID, max_attempts: int = 5) -> list[TranslationSegmentRecord]:
    progress = await session.scalar(select(TranslationProgressRecord).where(TranslationProgressRecord.execution_id == execution_id, TranslationProgressRecord.tenant_id == scope.tenant_id, TranslationProgressRecord.organization_id == scope.organization_id, TranslationProgressRecord.environment_id == scope.environment_id).with_for_update())
    if progress is None:
        raise BoundaryDenied()
    if progress.state not in {"failed", "running"}:
        raise Conflict("translation job is not retryable")
    rows = list((await session.scalars(select(TranslationSegmentRecord).where(TranslationSegmentRecord.execution_id == execution_id, TranslationSegmentRecord.tenant_id == scope.tenant_id, TranslationSegmentRecord.organization_id == scope.organization_id, TranslationSegmentRecord.environment_id == scope.environment_id, TranslationSegmentRecord.status == "failed").order_by(TranslationSegmentRecord.segment_index))).all())
    retryable = []
    for segment in rows:
        attempts = list((await session.scalars(select(TranslationAttemptRecord).where(TranslationAttemptRecord.execution_id == execution_id, TranslationAttemptRecord.segment_index == segment.segment_index, TranslationAttemptRecord.tenant_id == scope.tenant_id, TranslationAttemptRecord.organization_id == scope.organization_id, TranslationAttemptRecord.environment_id == scope.environment_id).order_by(TranslationAttemptRecord.attempt_number))).all())
        attempt_number = len(attempts) + 1
        if attempt_number > max_attempts:
            continue
        session.add(TranslationAttemptRecord(execution_id=execution_id, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, segment_index=segment.segment_index, attempt_number=attempt_number, status="queued", source_fingerprint=segment.source_fingerprint))
        segment.status = "queued"
        segment.retry_count = attempt_number
        retryable.append(segment)
    if not retryable:
        raise Conflict("no failed segment has remaining retry budget")
    progress.state = "queued"
    progress.failed_segments = max(0, progress.failed_segments - len(retryable))
    progress.retry_count += 1
    job = await session.get(TranslationJobRecord, execution_id)
    if job is not None:
        if job.status == "completed":
            raise Conflict("completed translation cannot be retried")
        job.status = "queued"
        job.completed_at = None
        job.attempt_count += 1
    await session.flush()
    return retryable


async def enqueue_translation(session: AsyncSession, scope: GovernanceScope, *, execution_id: uuid.UUID, idempotency_key: str):
    from app.jobs.ai_specialized_jobs import enqueue_specialized
    return await enqueue_specialized(session, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, execution_id=execution_id, agent_type="translation", idempotency_key=idempotency_key)
