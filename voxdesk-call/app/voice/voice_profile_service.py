"""Tenant-scoped persisted voice-profile lifecycle."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit as audit_event
from app.db.models import AuditAction, Tenant, VoiceProfile
from app.jobs.types import ensure_payload_safe

ACTIVE = "active"
INACTIVE = "inactive"
PENDING = "pending"
FAILED = "failed"
VALID_TYPES = frozenset({"provider", "cloned", "system"})


def _safe_name(value: str) -> str:
    value = " ".join((value or "").split())
    if not value or len(value) > 120:
        raise ValueError("voice profile name is required and must be at most 120 characters")
    return value


def _safe_provider_id(value: str) -> str:
    value = (value or "").strip()
    if not value or len(value) > 255 or any(ord(char) < 32 for char in value):
        raise ValueError("provider voice id is invalid")
    return value


async def create_profile(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    provider: str,
    provider_voice_id: str,
    language: str,
    locale: str,
    voice_type: str = "provider",
    capabilities: dict | None = None,
    metadata: dict | None = None,
    created_by: uuid.UUID | None = None,
    status: str = ACTIVE,
) -> VoiceProfile:
    if voice_type not in VALID_TYPES:
        raise ValueError("voice_type is not supported")
    if status not in {ACTIVE, INACTIVE, PENDING, FAILED}:
        raise ValueError("voice profile status is invalid")
    if not provider.strip():
        raise ValueError("voice provider is required")
    try:
        ensure_payload_safe(capabilities or {})
        ensure_payload_safe(metadata or {})
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("voice profile metadata contains a forbidden or oversized field") from exc
    row = VoiceProfile(
        tenant_id=tenant_id,
        name=_safe_name(name),
        provider=provider.strip().lower(),
        provider_voice_id=_safe_provider_id(provider_voice_id),
        language=(language or "en-US")[:32],
        locale=(locale or language or "en-US")[:32],
        voice_type=voice_type,
        status=status,
        capabilities=capabilities or {},
        profile_metadata=metadata or {},
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    await audit_event(
        session,
        AuditAction.INTEGRATION_UPDATED,
        tenant_id=tenant_id,
        actor_user_id=created_by,
        detail={"voice_profile_id": str(row.id), "provider": row.provider, "outcome": "created"},
    )
    return row


async def list_profiles(session: AsyncSession, tenant_id: uuid.UUID) -> list[VoiceProfile]:
    return list(
        (
            await session.execute(
                select(VoiceProfile)
                .where(VoiceProfile.tenant_id == tenant_id)
                .order_by(VoiceProfile.name, VoiceProfile.created_at)
            )
        )
        .scalars()
        .all()
    )


async def get_profile(
    session: AsyncSession, *, tenant_id: uuid.UUID, profile_id: uuid.UUID
) -> VoiceProfile | None:
    return (
        await session.execute(
            select(VoiceProfile).where(
                VoiceProfile.id == profile_id, VoiceProfile.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()


async def validate_profile(profile: VoiceProfile, *, provider_adapter=None) -> None:
    if profile.status not in {ACTIVE, PENDING}:
        raise ValueError("voice profile is not usable")
    provider_voice_id = _safe_provider_id(profile.provider_voice_id)
    if not profile.provider:
        raise ValueError("voice profile provider is missing")
    if provider_adapter is not None:
        validator = getattr(provider_adapter, "validate_voice", None)
        if validator is None:
            raise ValueError("voice provider cannot validate voice profiles")
        await validator(provider_voice_id)


async def activate_profile(session: AsyncSession, profile: VoiceProfile, *, provider_adapter=None) -> VoiceProfile:
    await validate_profile(profile, provider_adapter=provider_adapter)
    profile.status = ACTIVE
    profile.deactivated_at = None
    await session.flush()
    return profile


async def deactivate_profile(session: AsyncSession, profile: VoiceProfile) -> VoiceProfile:
    profile.status = INACTIVE
    profile.deactivated_at = datetime.utcnow()
    await session.flush()
    return profile


async def attach_to_tenant(
    session: AsyncSession, *, tenant: Tenant, profile: VoiceProfile, provider_adapter=None
) -> VoiceProfile:
    if profile.tenant_id != tenant.id:
        raise ValueError("voice profile belongs to another tenant")
    await validate_profile(profile, provider_adapter=provider_adapter)
    tenant.voice_id = profile.provider_voice_id
    tenant.language = profile.language
    await session.flush()
    return profile


def public_profile(profile: VoiceProfile) -> dict:
    """Allowlist response; never return provider credentials or raw audio refs."""
    return {
        "id": str(profile.id),
        "name": profile.name,
        "provider": profile.provider,
        "provider_voice_id": profile.provider_voice_id,
        "language": profile.language,
        "locale": profile.locale,
        "voice_type": profile.voice_type,
        "status": profile.status,
        "capabilities": profile.capabilities,
        "metadata": profile.profile_metadata,
        "created_at": profile.created_at.isoformat() if profile.created_at else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }
