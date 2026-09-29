"""Compatibility facade for the durable provider-backed clone service."""
from __future__ import annotations

import uuid

from app.voice.voice_clone_service import (
    cancel_clone_job,
    process_clone_job,
    public_job,
    request_clone,
)
from app.voice.voice_profile_service import list_profiles, public_profile


class VoiceCloneService:
    """Preserves the old service name without inventing clone identifiers."""

    async def clone(
        self,
        audio_path: str | None = None,
        name: str = "",
        *,
        session=None,
        tenant_id: uuid.UUID | None = None,
        requested_by: uuid.UUID | None = None,
        input_object_reference: str | None = None,
        provider: str | None = None,
        idempotency_key: str | None = None,
        environment_id: uuid.UUID | None = None,
    ):
        """Queue a durable clone using an opaque storage reference.

        ``audio_path`` remains as a positional compatibility alias for the
        former method, but it is treated as a storage key, never read as a
        local path and never converted into a fabricated provider id.
        """
        reference = input_object_reference or audio_path
        if session is None or tenant_id is None or not reference:
            raise ValueError("durable clone requires session, tenant_id, and an object reference")
        return await request_clone(
            session,
            tenant_id=tenant_id,
            requested_by=requested_by,
            name=name,
            input_object_reference=reference,
            provider=provider,
            idempotency_key=idempotency_key,
            environment_id=environment_id,
        )

    async def process(self, session, job):
        return await process_clone_job(session, job)

    async def cancel(self, session, job):
        return await cancel_clone_job(session, job)

    async def list_voices(self, session=None, tenant_id: uuid.UUID | None = None) -> list[dict]:
        if session is None or tenant_id is None:
            raise ValueError("listing voices requires session and tenant_id")
        return [public_profile(profile) for profile in await list_profiles(session, tenant_id)]

    @staticmethod
    def job_response(job) -> dict:
        return public_job(job)
