"""Multi-provider voice & provider catalog API routes (Sub-Phase 2C).

Exposes:
- `GET /api/voices/catalog`: Unified voice catalog across ElevenLabs, OpenAI, Deepgram,
  Cartesia, PlayHT, Azure, Google, plus any tenant-cloned `VoiceProfile` rows, with
  `configured: bool` per provider.
- `GET /api/voices/providers`: Full STT, TTS, LLM, and S2S provider capability matrix.
- `POST /api/voices/clone`: Instant voice cloning endpoint (uploads sample to ElevenLabs
  and persists a `VoiceProfile` row).
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.providers.registry import (
    ProviderNotConfigured,
    is_provider_configured,
    list_provider_catalog,
)
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.voice.clone import clone_voice_elevenlabs
from app.voice.voice_profile_service import list_profiles, public_profile

router = APIRouter(prefix="/api/voices", tags=["voice-catalog"])

CURATED_VOICES: tuple[dict[str, Any], ...] = (
    {
        "provider": "elevenlabs",
        "voice_id": "21m00Tcm4TlvDq8ikWAM",
        "name": "Rachel",
        "gender": "female",
        "accent": "American",
        "language": "en-US",
        "model": "eleven_flash_v2_5",
        "preview_url": None,
    },
    {
        "provider": "elevenlabs",
        "voice_id": "pNInz6obpgDQGcFmaJgB",
        "name": "Adam",
        "gender": "male",
        "accent": "American",
        "language": "en-US",
        "model": "eleven_flash_v2_5",
        "preview_url": None,
    },
    {
        "provider": "openai",
        "voice_id": "alloy",
        "name": "Alloy",
        "gender": "neutral",
        "accent": "American",
        "language": "en-US",
        "model": "gpt-4o-mini-tts",
        "preview_url": None,
    },
    {
        "provider": "openai",
        "voice_id": "nova",
        "name": "Nova",
        "gender": "female",
        "accent": "American",
        "language": "en-US",
        "model": "gpt-4o-mini-tts",
        "preview_url": None,
    },
    {
        "provider": "deepgram",
        "voice_id": "aura-asteria-en",
        "name": "Asteria",
        "gender": "female",
        "accent": "American",
        "language": "en-US",
        "model": "aura-asteria-en",
        "preview_url": None,
    },
    {
        "provider": "cartesia",
        "voice_id": "a0e99841-438c-4a64-b679-ae501e7d6091",
        "name": "Barbershop Man",
        "gender": "male",
        "accent": "American",
        "language": "en-US",
        "model": "sonic-2",
        "preview_url": None,
    },
    {
        "provider": "playht",
        "voice_id": "s3://voice-cloning-zero-shot/default/manifest.json",
        "name": "PlayHT Conversational",
        "gender": "female",
        "accent": "American",
        "language": "en-US",
        "model": "Play3.0-mini",
        "preview_url": None,
    },
    {
        "provider": "azure",
        "voice_id": "en-US-AvaMultilingualNeural",
        "name": "Ava Multilingual",
        "gender": "female",
        "accent": "American",
        "language": "en-US",
        "model": "en-US-AvaMultilingualNeural",
        "preview_url": None,
    },
    {
        "provider": "google",
        "voice_id": "en-US-Journey-F",
        "name": "Journey Female",
        "gender": "female",
        "accent": "American",
        "language": "en-US",
        "model": "en-US-Journey-F",
        "preview_url": None,
    },
)


@router.get("/catalog")
async def get_voice_catalog(
    provider: str | None = None,
    language: str | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return curated multi-provider voices + tenant cloned VoiceProfile rows."""
    items: list[dict[str, Any]] = []
    for entry in CURATED_VOICES:
        if provider and entry["provider"] != provider.lower():
            continue
        if language and not entry["language"].lower().startswith(language.lower()[:2]):
            continue
        items.append(
            {
                **entry,
                "voice_type": "curated",
                "configured": is_provider_configured("tts", entry["provider"]),
            }
        )

    profiles = await list_profiles(session, ctx.tenant_id)
    for prof in profiles:
        pub = public_profile(prof)
        if provider and pub["provider"] != provider.lower():
            continue
        if language and not str(pub.get("language", "")).lower().startswith(language.lower()[:2]):
            continue
        items.append(
            {
                "provider": pub["provider"],
                "voice_id": pub["provider_voice_id"],
                "name": pub["name"],
                "gender": pub.get("metadata", {}).get("gender", "custom"),
                "accent": pub.get("metadata", {}).get("accent", "custom"),
                "language": pub["language"],
                "model": "eleven_multilingual_v2",
                "preview_url": None,
                "voice_type": pub["voice_type"],
                "configured": is_provider_configured("tts", pub["provider"]),
            }
        )

    return {
        "items": items,
        "total": len(items),
        "providers": list_provider_catalog("tts"),
    }


@router.get("/providers")
async def get_all_providers_catalog(
    category: str | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
) -> dict[str, Any]:
    """Return full STT, TTS, LLM, and S2S provider catalog with `configured: bool`."""
    items = list_provider_catalog(category)
    return {"items": items, "total": len(items)}


@router.post("/clone")
async def clone_voice_endpoint(
    name: str = Form(..., min_length=1, max_length=120),
    description: str = Form(default="", max_length=400),
    language: str = Form(default="en-US", max_length=32),
    file: UploadFile = File(...),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Clone a voice from an uploaded WAV/MP3 sample via ElevenLabs and save a `VoiceProfile`."""
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Uploaded audio sample is empty.")
    try:
        result = await clone_voice_elevenlabs(
            name=name,
            audio_bytes=audio_bytes,
            filename=file.filename or "sample.wav",
            description=description,
            language=language,
            session=session,
            tenant_id=ctx.tenant_id,
            created_by=ctx.user_id,
        )
        await session.commit()
        return result
    except ProviderNotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Provider voice clone failed: {exc}") from exc
