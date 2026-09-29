"""Existing durable-job worker adapter for voice clone processing."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.models import DurableJob, VoiceCloneJob
from app.db.session import get_sessionmaker
from app.jobs.types import JobType, register_handler
from app.voice.voice_clone_service import process_clone_job


@register_handler(JobType.VOICE_CLONE)
async def process_voice_clone_job(job: DurableJob) -> None:
    payload = job.payload or {}
    raw_job_id = payload.get("voice_clone_job_id")
    if not isinstance(raw_job_id, str):
        raise ValueError("voice clone durable job is missing its clone job id")
    clone_job_id = uuid.UUID(raw_job_id)
    async with get_sessionmaker()() as session:
        clone_job = (
            await session.execute(
                select(VoiceCloneJob).where(
                    VoiceCloneJob.id == clone_job_id,
                    VoiceCloneJob.tenant_id == job.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if clone_job is None:
            raise ValueError("voice clone job was not found in its tenant")
        await process_clone_job(session, clone_job)
        await session.commit()
