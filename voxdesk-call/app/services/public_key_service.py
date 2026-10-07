"""Prompt 5: Scoped Public Widget Key Lifecycle Service.

Implements:
- Key generation (`vdpk_<id_hex>_<secret>`) with SHA-256 secret hashing (`key_hash`)
- Scope binding to `tenant_id`, `agent_id`, `environment_id`, `allowed_origins`, and `allowed_capabilities`
- Prohibition of private/admin capabilities (`FORBIDDEN_PUBLIC_KEY_CAPABILITIES`)
- Lifecycle transitions: `CREATE -> ACTIVE -> ROTATE -> REVOKE / EXPIRE`
- Immediate invalidation of active widget sessions upon key rotation or revocation
- Server-side origin validation and constant-time secret verification
- Durable audit logging on create, update, rotate, and revoke
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any as Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.public_site_models import PublicDomainError as AppError
from app.db.models import (
    Agent,
    AuditAction,
    ChatAgent,
    Environment,
    PublicWidgetKey,
    PublicWidgetKeyStatusEnum,
    PublicWidgetSession,
    PublicWidgetSessionStatusEnum,
)
from app.auth import service as auth_service
from app.domain.public_widget_models import (
    DEFAULT_PUBLIC_WIDGET_CAPABILITIES,
    FORBIDDEN_PUBLIC_KEY_CAPABILITIES,
    PublicWidgetAppearanceConfig,
    PublicWidgetCapability,
    PublicWidgetKeyCreate,
    PublicWidgetKeyCreatedResponse,
    PublicWidgetKeyRead,
    PublicWidgetKeyRevokeRequest,
    PublicWidgetKeyRotateRequest,
    PublicWidgetKeyStatus,
    PublicWidgetKeyUpdate,
)
from app.services.public_origin_service import (
    normalize_origin_list,
    validate_origin_against_allowlist,
)

PUBLIC_KEY_PREFIX = "vdpk"
ALLOWED_CAPABILITY_SET = frozenset(c.value for c in PublicWidgetCapability)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def hash_secret(secret: str) -> str:
    """Compute SHA-256 hex digest of a secret string."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def mint_public_key_token(row_id: uuid.UUID) -> tuple[str, str, str]:
    """Mint `(plaintext_public_key, key_prefix, key_hash)` for a `PublicWidgetKey` row."""
    raw_secret = secrets.token_urlsafe(32)
    plaintext_key = f"{PUBLIC_KEY_PREFIX}_{row_id.hex}_{raw_secret}"
    key_prefix = f"{PUBLIC_KEY_PREFIX}_{row_id.hex[:8]}"
    key_hash = hash_secret(raw_secret)
    return plaintext_key, key_prefix, key_hash


def parse_public_key_token(raw_key: str | None) -> tuple[uuid.UUID, str] | None:
    """Parse `vdpk_<row_id_hex>_<secret>` into `(row_id, secret)` or return `None`."""
    if not raw_key or not isinstance(raw_key, str):
        return None
    parts = raw_key.strip().split("_", 2)
    if len(parts) != 3:
        return None
    prefix, raw_id, secret = parts
    if prefix != PUBLIC_KEY_PREFIX or not secret or len(secret) < 16:
        return None
    try:
        row_id = uuid.UUID(hex=raw_id)
    except ValueError:
        return None
    return row_id, secret


def validate_public_key_capabilities(capabilities: list[str] | None) -> list[str]:
    """Ensure requested capabilities only include safe widget capabilities and never admin/tool permissions."""
    if capabilities is None:
        return list(DEFAULT_PUBLIC_WIDGET_CAPABILITIES)
    cleaned: list[str] = []
    seen: set[str] = set()
    for cap in capabilities:
        norm = str(cap or "").strip().lower()
        if not norm:
            continue
        if norm in FORBIDDEN_PUBLIC_KEY_CAPABILITIES or norm not in ALLOWED_CAPABILITY_SET:
            raise AppError(
                status_code=422,
                code="forbidden_public_key_capability",
                message=(
                    f"Capability '{norm}' is forbidden for public widget keys. "
                    f"Allowed capabilities: {sorted(ALLOWED_CAPABILITY_SET)}."
                ),
            )
        if norm not in seen:
            seen.add(norm)
            cleaned.append(norm)
    if not cleaned:
        raise AppError(
            status_code=422,
            code="empty_public_key_capabilities",
            message="At least one valid widget capability is required.",
        )
    return cleaned


async def resolve_tenant_agent(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: str,
    agent_kind: str = "voice",
) -> tuple[str, str, str, int, uuid.UUID | None, bool]:
    """Resolve a tenant-owned Voice `Agent` or `ChatAgent`.

    Returns `(canonical_agent_id, agent_name, agent_kind, published_version_number, published_version_id, is_published)`.
    """
    kind = (agent_kind or "voice").lower().strip()
    if kind == "chat":
        try:
            chat_uuid = uuid.UUID(str(agent_id))
            stmt = select(ChatAgent).where(
                ChatAgent.tenant_id == tenant_id,
                ChatAgent.id == chat_uuid,
            )
        except ValueError:
            stmt = select(ChatAgent).where(
                ChatAgent.tenant_id == tenant_id,
                ChatAgent.slug == str(agent_id),
            )
        chat_agent = (await db.execute(stmt)).scalar_one_or_none()
        if chat_agent is None:
            raise AppError(
                status_code=404,
                code="agent_not_found",
                message=f"Chat agent '{agent_id}' not found in this tenant.",
            )
        pub_ver = int(chat_agent.active_version or 1)
        is_pub = str(chat_agent.status or "").lower() == "published"
        return str(chat_agent.id), chat_agent.name, "chat", pub_ver, None, is_pub

    # Voice Agent lookup by UUID or external_key
    voice_agent: Agent | None = None
    try:
        agent_uuid = uuid.UUID(str(agent_id))
        voice_agent = (
            await db.execute(
                select(Agent).where(
                    Agent.tenant_id == tenant_id,
                    Agent.id == agent_uuid,
                )
            )
        ).scalar_one_or_none()
    except ValueError:
        voice_agent = None

    if voice_agent is None:
        voice_agent = (
            await db.execute(
                select(Agent).where(
                    Agent.tenant_id == tenant_id,
                    Agent.external_key == str(agent_id),
                )
            )
        ).scalar_one_or_none()

    if voice_agent is None:
        raise AppError(
            status_code=404,
            code="agent_not_found",
            message=f"Voice agent '{agent_id}' not found in this tenant.",
        )

    pub_ver_num = int(voice_agent.published_version_number or 1)
    is_pub = (
        str(voice_agent.status or "").lower() == "published"
        and voice_agent.published_version_number is not None
    )
    return (
        str(voice_agent.id),
        voice_agent.name,
        "voice",
        pub_ver_num,
        voice_agent.published_version_id,
        is_pub,
    )


def _build_embed_snippet(row: PublicWidgetKey) -> str:
    origins_comment = ", ".join(row.allowed_origins or []) or "none configured"
    return (
        f"<!-- VoxDesk Public Widget ({row.name}) | Allowed Origins: {origins_comment} -->\n"
        f'<script src="https://cdn.voxdesk.ai/widget/v1/loader.js" async\n'
        f'  data-voxdesk-public-key="{row.key_prefix}_<YOUR_SECRET>"\n'
        f'  data-voxdesk-agent-id="{row.agent_id}"\n'
        f'  data-voxdesk-environment="{row.environment_name}">\n'
        f"</script>"
    )


def _to_read_model(row: PublicWidgetKey) -> PublicWidgetKeyRead:
    raw_cfg = row.widget_config if isinstance(row.widget_config, dict) else {}
    appearance = PublicWidgetAppearanceConfig(**raw_cfg)
    status_val = str(row.status or PublicWidgetKeyStatus.ACTIVE.value).lower()
    now = _utcnow()
    exp = _ensure_aware(row.expires_at)
    if status_val == PublicWidgetKeyStatus.ACTIVE.value and exp is not None and exp <= now:
        status_val = PublicWidgetKeyStatus.EXPIRED.value

    return PublicWidgetKeyRead(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        environment_id=str(row.environment_id) if row.environment_id else None,
        environment_name=row.environment_name,
        agent_id=row.agent_id,
        agent_kind=row.agent_kind,
        name=row.name,
        key_prefix=row.key_prefix,
        status=PublicWidgetKeyStatus(status_val),
        allowed_origins=list(row.allowed_origins or []),
        allowed_capabilities=list(row.allowed_capabilities or []),
        rate_limit_per_minute=int(row.rate_limit_per_minute or 30),
        session_ttl_seconds=int(row.session_ttl_seconds or 900),
        require_published_agent=bool(row.require_published_agent),
        widget_config=appearance,
        rotated_from_key_id=str(row.rotated_from_key_id) if row.rotated_from_key_id else None,
        rotated_to_key_id=str(row.rotated_to_key_id) if row.rotated_to_key_id else None,
        created_by=str(row.created_by) if row.created_by else None,
        revoked_by=str(row.revoked_by) if row.revoked_by else None,
        revoke_reason=row.revoke_reason,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        rotated_at=row.rotated_at,
        last_used_at=row.last_used_at,
        last_used_origin=row.last_used_origin,
        created_at=row.created_at,
        updated_at=row.updated_at,
        embed_snippet=_build_embed_snippet(row),
    )


async def create_public_key(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    actor_email: str,
    payload: PublicWidgetKeyCreate,
) -> PublicWidgetKeyCreatedResponse:
    """Create a scoped `PublicWidgetKey` and return the plaintext key once."""
    canonical_agent_id, agent_name, resolved_kind, _, _, _ = await resolve_tenant_agent(
        db,
        tenant_id=tenant_id,
        agent_id=payload.agent_id,
        agent_kind=payload.agent_kind,
    )
    normalized_origins = normalize_origin_list(payload.allowed_origins)
    if not normalized_origins:
        raise AppError(
            status_code=422,
            code="missing_allowed_origins",
            message="At least one valid allowed origin (e.g., https://example.com) is required.",
        )

    validated_caps = validate_public_key_capabilities(payload.allowed_capabilities)

    env_row = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant_id,
                Environment.slug == payload.environment_name.lower().strip(),
            )
        )
    ).scalar_one_or_none()

    row_id = uuid.uuid4()
    plaintext_key, key_prefix, key_hash = mint_public_key_token(row_id)
    now = _utcnow()
    expires_at = (
        now + timedelta(days=payload.expires_in_days)
        if payload.expires_in_days is not None
        else None
    )

    appearance_dict = payload.widget_config.model_dump(mode="json")
    if appearance_dict.get("title") == "Talk with our AI Assistant" and agent_name:
        appearance_dict["title"] = f"Talk with {agent_name}"

    row = PublicWidgetKey(
        id=row_id,
        tenant_id=tenant_id,
        environment_id=env_row.id if env_row else None,
        environment_name=payload.environment_name.strip().lower(),
        agent_id=canonical_agent_id,
        agent_kind=resolved_kind,
        name=payload.name.strip(),
        key_prefix=key_prefix,
        key_hash=key_hash,
        status=PublicWidgetKeyStatusEnum.ACTIVE.value,
        allowed_origins=normalized_origins,
        allowed_capabilities=validated_caps,
        rate_limit_per_minute=payload.rate_limit_per_minute,
        session_ttl_seconds=payload.session_ttl_seconds,
        require_published_agent=payload.require_published_agent,
        widget_config=appearance_dict,
        created_by=actor_user_id,
        expires_at=expires_at,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()

    await auth_service.record_audit(
        db,
        action=AuditAction.GOVERNANCE_EVENT,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        detail={
            "event_kind": "public_widget_key.created",
            "public_key_id": str(row.id),
            "key_prefix": row.key_prefix,
            "agent_id": row.agent_id,
            "allowed_origins": normalized_origins,
            "allowed_capabilities": validated_caps,
        },
        commit=False,
    )
    await db.commit()
    await db.refresh(row)

    return PublicWidgetKeyCreatedResponse(
        key=_to_read_model(row),
        public_key=plaintext_key,
    )


async def list_public_keys(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: str | None = None,
) -> list[PublicWidgetKeyRead]:
    """List tenant-scoped public widget keys (never returning secret hashes or plaintext keys)."""
    stmt = select(PublicWidgetKey).where(PublicWidgetKey.tenant_id == tenant_id)
    if agent_id:
        stmt = stmt.where(PublicWidgetKey.agent_id == str(agent_id))
    stmt = stmt.order_by(PublicWidgetKey.created_at.desc())
    rows = (await db.execute(stmt)).scalars().all()
    now = _utcnow()
    dirty = False
    for row in rows:
        exp = _ensure_aware(row.expires_at)
        if row.status == PublicWidgetKeyStatusEnum.ACTIVE.value and exp is not None and exp <= now:
            row.status = PublicWidgetKeyStatusEnum.EXPIRED.value
            row.updated_at = now
            dirty = True
    if dirty:
        await db.commit()
    return [_to_read_model(r) for r in rows]


async def get_public_key_row(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
) -> PublicWidgetKey:
    row = (
        await db.execute(
            select(PublicWidgetKey).where(
                PublicWidgetKey.tenant_id == tenant_id,
                PublicWidgetKey.id == key_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(
            status_code=404,
            code="public_key_not_found",
            message="Public widget key not found in this tenant.",
        )
    return row


async def get_public_key(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
) -> PublicWidgetKeyRead:
    row = await get_public_key_row(db, tenant_id=tenant_id, key_id=key_id)
    return _to_read_model(row)


async def update_public_key(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    actor_email: str,
    payload: PublicWidgetKeyUpdate,
) -> PublicWidgetKeyRead:
    """Update origin allowlist, capabilities, rate limits, or appearance config on an active key."""
    row = await get_public_key_row(db, tenant_id=tenant_id, key_id=key_id)
    if row.status != PublicWidgetKeyStatusEnum.ACTIVE.value:
        raise AppError(
            status_code=409,
            code="public_key_not_active",
            message=f"Cannot update public key in '{row.status}' state.",
        )

    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.allowed_origins is not None:
        norm_origins = normalize_origin_list(payload.allowed_origins)
        if not norm_origins:
            raise AppError(
                status_code=422,
                code="missing_allowed_origins",
                message="At least one valid allowed origin is required.",
            )
        row.allowed_origins = norm_origins
    if payload.allowed_capabilities is not None:
        row.allowed_capabilities = validate_public_key_capabilities(payload.allowed_capabilities)
    if payload.rate_limit_per_minute is not None:
        row.rate_limit_per_minute = payload.rate_limit_per_minute
    if payload.session_ttl_seconds is not None:
        row.session_ttl_seconds = payload.session_ttl_seconds
    if payload.require_published_agent is not None:
        row.require_published_agent = payload.require_published_agent
    if payload.widget_config is not None:
        row.widget_config = payload.widget_config.model_dump(mode="json")

    row.updated_at = _utcnow()
    await auth_service.record_audit(
        db,
        action=AuditAction.GOVERNANCE_EVENT,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        detail={
            "event_kind": "public_widget_key.updated",
            "public_key_id": str(row.id),
            "key_prefix": row.key_prefix,
            "allowed_origins": row.allowed_origins,
            "allowed_capabilities": row.allowed_capabilities,
        },
        commit=False,
    )
    await db.commit()
    await db.refresh(row)
    return _to_read_model(row)


async def rotate_public_key(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    actor_email: str,
    payload: PublicWidgetKeyRotateRequest,
) -> PublicWidgetKeyCreatedResponse:
    """Rotate an active key: mark old key ROTATED, invalidate its sessions, and issue a new ACTIVE key."""
    old_row = await get_public_key_row(db, tenant_id=tenant_id, key_id=key_id)
    if old_row.status != PublicWidgetKeyStatusEnum.ACTIVE.value:
        raise AppError(
            status_code=409,
            code="public_key_not_active",
            message=f"Cannot rotate a public key in '{old_row.status}' state.",
        )

    now = _utcnow()
    new_origins = (
        normalize_origin_list(payload.allowed_origins)
        if payload.allowed_origins is not None
        else list(old_row.allowed_origins or [])
    )
    if not new_origins:
        raise AppError(
            status_code=422,
            code="missing_allowed_origins",
            message="At least one valid allowed origin is required.",
        )

    new_row_id = uuid.uuid4()
    plaintext_key, key_prefix, key_hash = mint_public_key_token(new_row_id)
    expires_at = (
        now + timedelta(days=payload.expires_in_days)
        if payload.expires_in_days is not None
        else old_row.expires_at
    )

    new_row = PublicWidgetKey(
        id=new_row_id,
        tenant_id=tenant_id,
        environment_id=old_row.environment_id,
        environment_name=old_row.environment_name,
        agent_id=old_row.agent_id,
        agent_kind=old_row.agent_kind,
        name=old_row.name,
        key_prefix=key_prefix,
        key_hash=key_hash,
        status=PublicWidgetKeyStatusEnum.ACTIVE.value,
        allowed_origins=new_origins,
        allowed_capabilities=list(old_row.allowed_capabilities or []),
        rate_limit_per_minute=old_row.rate_limit_per_minute,
        session_ttl_seconds=old_row.session_ttl_seconds,
        require_published_agent=old_row.require_published_agent,
        widget_config=dict(old_row.widget_config or {}),
        rotated_from_key_id=old_row.id,
        created_by=actor_user_id,
        expires_at=expires_at,
        created_at=now,
        updated_at=now,
    )

    old_row.status = PublicWidgetKeyStatusEnum.ROTATED.value
    old_row.rotated_at = now
    old_row.rotated_to_key_id = new_row_id
    old_row.revoke_reason = payload.reason
    old_row.updated_at = now

    db.add(new_row)
    await db.flush()

    # Invalidate active sessions created by the rotated key
    await db.execute(
        update(PublicWidgetSession)
        .where(
            PublicWidgetSession.public_key_id == old_row.id,
            PublicWidgetSession.status.in_(
                [
                    PublicWidgetSessionStatusEnum.READY.value,
                    PublicWidgetSessionStatusEnum.CONNECTING.value,
                    PublicWidgetSessionStatusEnum.CONNECTED.value,
                ]
            ),
        )
        .values(
            status=PublicWidgetSessionStatusEnum.EXPIRED.value,
            ended_at=now,
            error_code="PUBLIC_KEY_ROTATED",
            error_message="Public key was rotated by workspace administrator.",
            updated_at=now,
        )
    )

    await auth_service.record_audit(
        db,
        action=AuditAction.GOVERNANCE_EVENT,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        detail={
            "event_kind": "public_widget_key.rotated",
            "old_public_key_id": str(old_row.id),
            "new_public_key_id": str(new_row.id),
            "new_key_prefix": new_row.key_prefix,
            "reason": payload.reason,
        },
        commit=False,
    )
    await db.commit()
    await db.refresh(new_row)

    return PublicWidgetKeyCreatedResponse(
        key=_to_read_model(new_row),
        public_key=plaintext_key,
    )


async def revoke_public_key(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    actor_email: str,
    payload: PublicWidgetKeyRevokeRequest,
) -> PublicWidgetKeyRead:
    """Immediately revoke a public key and expire all of its active widget sessions."""
    row = await get_public_key_row(db, tenant_id=tenant_id, key_id=key_id)
    now = _utcnow()
    row.status = PublicWidgetKeyStatusEnum.REVOKED.value
    row.revoked_at = now
    row.revoked_by = actor_user_id
    row.revoke_reason = payload.reason
    row.updated_at = now

    await db.execute(
        update(PublicWidgetSession)
        .where(
            PublicWidgetSession.public_key_id == row.id,
            PublicWidgetSession.status.in_(
                [
                    PublicWidgetSessionStatusEnum.READY.value,
                    PublicWidgetSessionStatusEnum.CONNECTING.value,
                    PublicWidgetSessionStatusEnum.CONNECTED.value,
                ]
            ),
        )
        .values(
            status=PublicWidgetSessionStatusEnum.EXPIRED.value,
            ended_at=now,
            error_code="PUBLIC_KEY_REVOKED",
            error_message="Public key was revoked by workspace administrator.",
            updated_at=now,
        )
    )

    await auth_service.record_audit(
        db,
        action=AuditAction.GOVERNANCE_EVENT,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        detail={
            "event_kind": "public_widget_key.revoked",
            "public_key_id": str(row.id),
            "key_prefix": row.key_prefix,
            "reason": payload.reason,
        },
        commit=False,
    )
    await db.commit()
    await db.refresh(row)
    return _to_read_model(row)


async def authenticate_public_key(
    db: AsyncSession,
    *,
    raw_public_key: str | None,
    request_origin: str | None,
    required_capability: PublicWidgetCapability,
    client_ip: str | None = None,
) -> tuple[PublicWidgetKey, str]:
    """Authenticate a public key and validate its origin allowlist and capability scope.

    Returns `(key_row, normalized_origin)` or raises `AppError` with explicit error codes
    (`INVALID_PUBLIC_KEY`, `FORBIDDEN_ORIGIN`, `FORBIDDEN_CAPABILITY`).
    """
    parsed = parse_public_key_token(raw_public_key)
    if parsed is None:
        raise AppError(
            status_code=401,
            code="INVALID_PUBLIC_KEY",
            message="Invalid or malformed public widget key.",
        )

    row_id, secret = parsed
    row = (
        await db.execute(select(PublicWidgetKey).where(PublicWidgetKey.id == row_id))
    ).scalar_one_or_none()
    if row is None:
        raise AppError(
            status_code=401,
            code="INVALID_PUBLIC_KEY",
            message="Invalid or unknown public widget key.",
        )

    candidate_hash = hash_secret(secret)
    if not hmac.compare_digest(row.key_hash, candidate_hash):
        raise AppError(
            status_code=401,
            code="INVALID_PUBLIC_KEY",
            message="Invalid public widget key secret.",
        )

    now = _utcnow()
    exp = _ensure_aware(row.expires_at)
    if exp is not None and exp <= now:
        if row.status == PublicWidgetKeyStatusEnum.ACTIVE.value:
            row.status = PublicWidgetKeyStatusEnum.EXPIRED.value
            row.updated_at = now
            await db.commit()
        raise AppError(
            status_code=401,
            code="INVALID_PUBLIC_KEY",
            message="This public widget key has expired.",
        )

    if (
        row.status != PublicWidgetKeyStatusEnum.ACTIVE.value
        or row.revoked_at is not None
        or row.rotated_at is not None
    ):
        raise AppError(
            status_code=401,
            code="INVALID_PUBLIC_KEY",
            message=f"This public widget key is no longer active (status: {row.status}).",
        )

    origin_check = validate_origin_against_allowlist(
        request_origin=request_origin,
        allowed_origins=list(row.allowed_origins or []),
    )
    if not origin_check.allowed or not origin_check.normalized_origin:
        raise AppError(
            status_code=403,
            code="FORBIDDEN_ORIGIN",
            message=origin_check.reason,
        )

    allowed_caps = {str(c).lower() for c in (row.allowed_capabilities or [])}
    if required_capability.value not in allowed_caps:
        raise AppError(
            status_code=403,
            code="FORBIDDEN_CAPABILITY",
            message=f"Public key does not permit capability '{required_capability.value}'.",
        )

    row.last_used_at = now
    row.last_used_origin = origin_check.normalized_origin
    if client_ip:
        row.last_used_ip = client_ip[:64]
    await db.commit()
    await db.refresh(row)

    return row, origin_check.normalized_origin
