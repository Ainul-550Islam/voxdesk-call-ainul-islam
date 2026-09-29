"""Provider-backed, restart-safe voice clone jobs."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
import wave
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx
from sqlalchemy import select

from app.agent.errors import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderConfigurationError,
    ProviderInvalidRequestError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.auth.identity.events import emit as audit_event
from app.agent.provider_observability import record_provider_error
from app.core.config import settings
from app.core.logging import log
from app.core.tracing import span
from app.db.models import AuditAction, VoiceCloneJob
from app.knowledge.storage import get_storage
from app.voice.voice_profile_service import create_profile

REQUESTED = "requested"
VALIDATING = "validating"
UPLOADING = "uploading"
PROVIDER_PROCESSING = "provider_processing"
VALIDATING_RESULT = "validating_result"
READY = "ready"
FAILED = "failed"
CANCELLED = "cancelled"
ACTIVE_STATES = frozenset({REQUESTED, VALIDATING, UPLOADING, PROVIDER_PROCESSING, VALIDATING_RESULT})


@dataclass(frozen=True)
class CloneSubmission:
    provider_job_id: str
    provider_voice_id: str
    metadata: dict[str, Any]


class CloneProvider(Protocol):
    name: str

    async def submit(self, *, name: str, audio: bytes, filename: str) -> CloneSubmission: ...
    async def validate_voice(self, provider_voice_id: str) -> None: ...
    async def cancel(self, provider_job_id: str) -> None: ...


class ElevenLabsCloneProvider:
    name = "elevenlabs"

    def __init__(self, api_key: str, *, endpoint: str = "https://api.elevenlabs.io") -> None:
        if not api_key.strip():
            raise ProviderConfigurationError("ElevenLabs clone API key is not configured", provider=self.name)
        self._api_key = api_key
        self._endpoint = endpoint.rstrip("/")

    async def submit(self, *, name: str, audio: bytes, filename: str) -> CloneSubmission:
        if not audio:
            raise ProviderInvalidRequestError("clone audio is empty", provider=self.name)
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                with span("voxdesk.voice_clone.submit", provider=self.name):
                    response = await client.post(
                        f"{self._endpoint}/v1/voices/add",
                        headers={"xi-api-key": self._api_key},
                        data={"name": name},
                        files={"files": (filename, audio, _content_type(filename))},
                    )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("voice clone provider timed out", provider=self.name) from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError("voice clone provider is unreachable", provider=self.name) from exc
        if response.status_code == 401:
            raise ProviderAuthenticationError("voice clone credentials were rejected", provider=self.name)
        if response.status_code == 429:
            raise ProviderRateLimitedError(provider=self.name)
        if response.status_code >= 500:
            raise ProviderUnavailableError("voice clone provider failed", provider=self.name)
        if response.status_code >= 400:
            raise ProviderInvalidRequestError("voice clone request was rejected", provider=self.name)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnavailableError("voice clone provider returned malformed JSON", provider=self.name) from exc
        provider_voice_id = payload.get("voice_id")
        if not isinstance(provider_voice_id, str) or not provider_voice_id:
            raise ProviderUnavailableError("voice clone response has no voice id", provider=self.name)
        return CloneSubmission(provider_voice_id, provider_voice_id, {"provider": self.name})

    async def validate_voice(self, provider_voice_id: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get(
                    f"{self._endpoint}/v1/voices/{provider_voice_id}",
                    headers={"xi-api-key": self._api_key},
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("voice validation provider timed out", provider=self.name) from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError("voice validation provider is unreachable", provider=self.name) from exc
        if response.status_code == 401:
            raise ProviderAuthenticationError("voice validation credentials were rejected", provider=self.name)
        if response.status_code == 429:
            raise ProviderRateLimitedError(provider=self.name)
        if response.status_code == 404:
            raise ProviderUnavailableError("provider did not return the cloned voice", provider=self.name)
        if response.status_code >= 500:
            raise ProviderUnavailableError("voice validation provider failed", provider=self.name)
        if response.status_code >= 400:
            raise ProviderInvalidRequestError("provider rejected voice validation", provider=self.name)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnavailableError("voice validation returned malformed JSON", provider=self.name) from exc
        if payload.get("voice_id") != provider_voice_id:
            raise ProviderUnavailableError("voice validation returned a mismatched voice id", provider=self.name)

    async def cancel(self, provider_job_id: str) -> None:
        # ElevenLabs voice cloning is an immediate create endpoint. There is no
        # cancellable remote job after the request is accepted.
        return None


def _content_type(filename: str) -> str:
    return "audio/wav" if filename.lower().endswith(".wav") else "audio/mpeg"


def _fingerprint(
    *, tenant_id: uuid.UUID, provider: str, name: str, object_ref: str, environment_id: uuid.UUID | None = None
) -> str:
    payload = json.dumps(
        {
            "tenant_id": str(tenant_id),
            "environment_id": str(environment_id) if environment_id else None,
            "provider": provider,
            "name": name,
            "object_ref": object_ref,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _validate_audio(data: bytes, object_ref: str) -> tuple[str, int]:
    if not data:
        raise ValueError("clone audio is empty")
    lowered = object_ref.lower()
    if lowered.endswith(".wav") or data[:4] == b"RIFF":
        try:
            import io

            with wave.open(io.BytesIO(data), "rb") as wav:
                if wav.getnchannels() not in {1, 2} or wav.getsampwidth() not in {2}:
                    raise ValueError("clone WAV must be mono/stereo 16-bit PCM")
                duration = wav.getnframes() / max(1, wav.getframerate())
        except (wave.Error, EOFError) as exc:
            raise ValueError("clone WAV is malformed") from exc
        if duration < 1 or duration > settings.voice_clone_max_duration_seconds:
            raise ValueError("clone audio duration is outside the allowed range")
        return "wav", int(duration)
    if lowered.endswith((".mp3", ".mpeg")) and (data[:3] == b"ID3" or data[:1] == b"\xff"):
        # MP3 frame duration is provider-validated; the local size bound still
        # prevents unbounded uploads before the provider receives the bytes.
        return "mp3", 0
    raise ValueError("clone audio must be a supported WAV or MP3 object")


async def request_clone(
    session,
    *,
    tenant_id: uuid.UUID,
    requested_by: uuid.UUID | None,
    name: str,
    input_object_reference: str,
    provider: str | None = None,
    idempotency_key: str | None = None,
    environment_id: uuid.UUID | None = None,
) -> VoiceCloneJob:
    provider_name = (provider or settings.voice_clone_provider).strip().lower()
    object_prefix = f"tenant/{tenant_id}/voice-clones/"
    if not input_object_reference.startswith(object_prefix):
        raise ValueError("input object reference is not tenant-scoped")
    relative_object = input_object_reference[len(object_prefix):]
    if not relative_object or any(part in {"", ".", ".."} for part in relative_object.split("/")):
        raise ValueError("input object reference is invalid")
    fingerprint = (
        hashlib.sha256(f"{tenant_id}:{environment_id}:{idempotency_key}".encode()).hexdigest()
        if idempotency_key
        else _fingerprint(
            tenant_id=tenant_id,
            provider=provider_name,
            name=name,
            object_ref=input_object_reference,
            environment_id=environment_id,
        )
    )
    existing = (
        await session.execute(
            select(VoiceCloneJob).where(
                VoiceCloneJob.tenant_id == tenant_id,
                VoiceCloneJob.request_fingerprint == fingerprint,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    job = VoiceCloneJob(
        tenant_id=tenant_id,
        environment_id=environment_id,
        provider=provider_name,
        status=REQUESTED,
        input_object_reference=input_object_reference,
        request_fingerprint=fingerprint,
        requested_by=requested_by,
        progress=0,
    )
    session.add(job)
    await session.flush()
    await audit_event(
        session,
        AuditAction.INTEGRATION_UPDATED,
        tenant_id=tenant_id,
        actor_user_id=requested_by,
        detail={"voice_clone_job_id": str(job.id), "provider": provider_name, "outcome": "requested"},
    )
    return job


async def process_clone_job(session, job: VoiceCloneJob, *, provider: CloneProvider | None = None) -> VoiceCloneJob:
    if job.status in {READY, FAILED, CANCELLED}:
        return job
    started = time.perf_counter()
    try:
        provider = provider or _provider_for(job.provider)
        job.status, job.progress, job.last_heartbeat_at = VALIDATING, 10, datetime.utcnow()
        await session.flush()
        if len(job.input_object_reference) > 1000:
            raise ValueError("clone object reference is invalid")
        audio = await get_storage().get(job.input_object_reference)
        if len(audio) > settings.voice_clone_max_audio_bytes:
            raise ValueError("clone audio exceeds the upload limit")
        file_format, _duration = _validate_audio(audio, job.input_object_reference)
        job.status, job.progress = UPLOADING, 30
        job.last_heartbeat_at = datetime.utcnow()
        await session.flush()
        job.status, job.progress = PROVIDER_PROCESSING, 60
        await session.flush()
        submission = None
        for attempt in range(max(0, min(settings.voice_clone_max_retries, 3)) + 1):
            job.attempt_count += 1
            job.last_heartbeat_at = datetime.utcnow()
            await session.flush()
            try:
                submission = await provider.submit(
                    name=f"voxdesk-{job.id}",
                    audio=audio,
                    filename=f"sample.{file_format}",
                )
                break
            except ProviderError as provider_error:
                if not provider_error.retryable or attempt >= settings.voice_clone_max_retries:
                    raise
                await asyncio.sleep(settings.voice_provider_retry_backoff_seconds * (2**attempt))
        if submission is None:
            raise ProviderUnavailableError("voice clone provider did not return a result", provider=job.provider)
        job.provider_job_id = submission.provider_job_id
        job.status, job.progress = VALIDATING_RESULT, 90
        await session.flush()
        if not submission.provider_voice_id:
            raise ValueError("provider returned no voice id")
        validator = getattr(provider, "validate_voice", None)
        if validator is None:
            raise ProviderUnavailableError(
                "clone provider cannot validate its voice response", provider=job.provider
            )
        await validator(submission.provider_voice_id)
        profile = await create_profile(
            session,
            tenant_id=job.tenant_id,
            name=f"Clone {str(job.id)[:8]}",
            provider=job.provider,
            provider_voice_id=submission.provider_voice_id,
            language="en-US",
            locale="en-US",
            voice_type="cloned",
            capabilities={"streaming": True, "voice_clone": True},
            metadata={"clone_job_id": str(job.id)},
            created_by=job.requested_by,
            status="active",
        )
        job.voice_profile_id = profile.id
        job.status, job.progress, job.completed_at = READY, 100, datetime.utcnow()
        job.error_code, job.error_message_redacted = None, None
        await session.flush()
        log.info(
            "voice.clone.completed",
            provider=job.provider,
            status=job.status,
            attempt_count=job.attempt_count,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return job
    except Exception as exc:
        job.status = FAILED
        job.error_code = getattr(
            exc, "category", "validation_error" if isinstance(exc, ValueError) else "provider_error"
        )
        job.error_message_redacted = getattr(exc, "safe_message", "voice clone job failed")[:500]
        job.last_heartbeat_at = datetime.utcnow()
        if isinstance(exc, ProviderError):
            record_provider_error(exc.provider, exc.category)
        log.warning(
            "voice.clone.failed",
            provider=job.provider,
            error_code=job.error_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        await audit_event(
            session,
            AuditAction.INTEGRATION_UPDATED,
            tenant_id=job.tenant_id,
            actor_user_id=job.requested_by,
            detail={"voice_clone_job_id": str(job.id), "provider": job.provider, "outcome": "failed", "error_code": job.error_code},
            commit=False,
        )
        await session.flush()
        return job


async def cancel_clone_job(session, job: VoiceCloneJob) -> VoiceCloneJob:
    if job.status == READY:
        raise ValueError("ready voice clone jobs cannot be cancelled")
    if job.status in {FAILED, CANCELLED}:
        return job
    if job.provider_job_id:
        await _provider_for(job.provider).cancel(job.provider_job_id)
    job.status = CANCELLED
    job.completed_at = datetime.utcnow()
    await session.flush()
    return job


async def recover_stale_jobs(session, *, older_than_seconds: int | None = None) -> int:
    from sqlalchemy import select

    cutoff = datetime.utcnow() - timedelta(seconds=older_than_seconds or settings.voice_clone_job_timeout_seconds)
    rows = (
        await session.execute(
            select(VoiceCloneJob).where(
                VoiceCloneJob.status.in_(ACTIVE_STATES),
                VoiceCloneJob.updated_at < cutoff,
            )
        )
    ).scalars().all()
    for job in rows:
        if job.attempt_count < 3:
            job.status = REQUESTED
            job.error_code = "recovered_for_retry"
            job.error_message_redacted = "worker heartbeat expired; queued for retry"
        else:
            job.status = FAILED
            job.error_code = "timeout"
            job.error_message_redacted = "provider job exceeded the recovery window"
    await session.flush()
    return len(rows)


def _provider_for(name: str) -> CloneProvider:
    from app.voice.provider_registry import build_voice_provider_registry

    try:
        return build_voice_provider_registry().resolve_clone(name)
    except ValueError as exc:
        raise ProviderConfigurationError(
            f"voice clone provider {name!r} is not configured", provider=name
        ) from exc


def public_job(job: VoiceCloneJob) -> dict:
    return {
        "id": str(job.id),
        "provider": job.provider,
        "provider_job_id": job.provider_job_id,
        "status": job.status,
        "progress": job.progress,
        "voice_profile_id": str(job.voice_profile_id) if job.voice_profile_id else None,
        "error_code": job.error_code,
        "error_message": job.error_message_redacted,
        "attempt_count": job.attempt_count,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }
