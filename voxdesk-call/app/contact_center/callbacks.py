"""Callback overflow persists a durable job. It does not place a call.

The job row is the record. This function returns False when that row was not
stored, and the caller must not report a callback as requested.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.models import QueueEntry
from app.jobs.repository import create_job


async def request_callback(session: AsyncSession, entry: QueueEntry) -> bool:
    job, _created = await create_job(
        session,
        tenant_id=entry.tenant_id,
        environment_id=entry.environment_id,
        job_type="acd_callback",
        payload={
            "entry_id": str(entry.id),
            "queue_id": str(entry.queue_id),
            "call_id": str(entry.call_id),
        },
        idempotency_key=f"acd-callback:{entry.id}",
    )
    return job.id is not None
