"""Compatibility facade and ElevenLabs instant voice cloning adapter (Sub-Phase 2C)."""
from __future__ import annotations

import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.providers.registry import (
    get_provider_credential,
    require_provider_configured,
)
from app.core.config import settings
from app.voice.voice_clone_service import (
    cancel_clone_job,
    process_clone_job,
    public_job,
    request_clone,
)
from app.voice.voice_profile_service import create_profile, list_profiles, public_profile


async def clone_voice_elevenlabs(
    *,
    name: str,
    audio_bytes: bytes,
    filename: str = "sample.wav",
    description: str = "",
    language: str = "en-US",
    session: AsyncSession | None = None,
    tenant_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    configured_settings: Any = settings,
) -> dict[str, Any]:
    """Upload a voice sample to ElevenLabs Instant Voice Cloning (`POST /v1/voices/add`) and persist `VoiceProfile`."""
    require_provider_configured("tts", "elevenlabs", configured_settings=configured_settings)
    api_key = get_provider_credential("ELEVENLABS_API_KEY", "elevenlabs_api_key", configured_settings)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.elevenlabs.io/v1/voices/add",
            headers={"xi-api-key": api_key},
            data={"name": name, "description": description},
            files={"files": (filename, audio_bytes, "audio/wav")},
        )
        resp.raise_for_status()
        payload = resp.json()

    provider_voice_id = str(payload.get("voice_id") or "").strip()
    if not provider_voice_id:
        raise RuntimeError("ElevenLabs did not return a valid voice_id")

    if session is not None and tenant_id is not None:
        profile = await create_profile(
            session,
            tenant_id=tenant_id,
            name=name,
            provider="elevenlabs",
            provider_voice_id=provider_voice_id,
            language=language,
            locale=language,
            voice_type="cloned",
            metadata={"description": description},
            created_by=created_by,
        )
        return public_profile(profile)

    return {
        "provider": "elevenlabs",
        "provider_voice_id": provider_voice_id,
        "name": name,
        "language": language,
        "voice_type": "cloned",
    }


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
        """Queue a durable clone using an opaque storage reference."""
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

    async def clone_instant(
        self,
        *,
        name: str,
        audio_bytes: bytes,
        filename: str = "sample.wav",
        description: str = "",
        language: str = "en-US",
        session: AsyncSession | None = None,
        tenant_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        return await clone_voice_elevenlabs(
            name=name,
            audio_bytes=audio_bytes,
            filename=filename,
            description=description,
            language=language,
            session=session,
            tenant_id=tenant_id,
            created_by=created_by,
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
