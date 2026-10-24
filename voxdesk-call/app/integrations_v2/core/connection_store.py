"""Encrypted per-tenant credential persistence & masked serialization for Integrations v2.

Reuses ``app.integrations.crm.crypto`` AES-256-GCM envelopes with domain-separated
AAD namespace ``int2`` so a ciphertext copied across tenants or across credential
families refuses to authenticate. Raw credentials are never returned by ``mask_connection``.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.integrations_v2_models import IntegrationConnection
from app.integrations.crm.crypto import (
    CredentialCryptoError,
    CredentialDecryptionError,
    KeyRing,
    decrypt_credentials,
    encrypt_credentials,
    key_ring_from_settings,
)
from app.integrations_v2.core.errors import AuthExpired, IntegrationV2Error, redact_secrets
from app.integrations_v2.core.provider_base import (
    ConnectionRuntimeContext,
    VerificationStatus,
)

INT2_CRYPTO_NAMESPACE = "int2"


def _resolve_key_ring() -> KeyRing:
    ring = key_ring_from_settings()
    if ring is not None:
        return ring
    if settings.is_production:
        raise CredentialCryptoError(
            "CRM_ENCRYPTION_KEYS must be configured in production to store integration secrets"
        )
    # Deterministic dev/test-only key material derived from configured runtime jwt_secret
    seed = settings.jwt_secret.encode("utf-8")
    material = hashlib.sha256(b"voxdesk-int2-dev-ring:" + seed).digest()
    return KeyRing(keys={"dev": material}, active_id="dev")


def seal_secret_bundle(
    payload: dict[str, Any], *, tenant_id: uuid.UUID | str, provider: str
) -> tuple[str, str]:
    """Encrypt ``payload`` with AAD bound to ``(tenant_id, provider, 'int2')``."""
    ring = _resolve_key_ring()
    return encrypt_credentials(
        payload,
        tenant_id=str(tenant_id),
        provider=provider,
        key_ring=ring,
        namespace=INT2_CRYPTO_NAMESPACE,
    )


def open_secret_bundle(
    envelope: str, *, tenant_id: uuid.UUID | str, provider: str
) -> dict[str, Any]:
    """Decrypt ``envelope``, failing closed on tampering or cross-tenant substitution."""
    if not envelope:
        return {}
    ring = _resolve_key_ring()
    try:
        return decrypt_credentials(
            envelope,
            tenant_id=str(tenant_id),
            provider=provider,
            key_ring=ring,
            namespace=INT2_CRYPTO_NAMESPACE,
        )
    except CredentialDecryptionError as exc:
        raise AuthExpired(
            "Stored integration credentials failed integrity check; reconnect provider",
            provider=provider,
        ) from exc


def load_runtime_context(row: IntegrationConnection) -> ConnectionRuntimeContext:
    """Decrypt credentials in memory for a single outbound provider invocation."""
    creds: dict[str, Any] = {}
    if row.token_secret_ref:
        creds.update(
            open_secret_bundle(
                row.token_secret_ref, tenant_id=row.tenant_id, provider=row.provider
            )
        )
    refresh_tok = ""
    if row.refresh_secret_ref:
        refresh_data = open_secret_bundle(
            row.refresh_secret_ref,
            tenant_id=row.tenant_id,
            provider=f"{row.provider}:refresh",
        )
        creds.update(refresh_data)
        refresh_tok = str(refresh_data.get("refresh_token") or "")
    wh_sec = ""
    if row.webhook_secret_ref:
        wh_data = open_secret_bundle(
            row.webhook_secret_ref,
            tenant_id=row.tenant_id,
            provider=f"{row.provider}:webhook",
        )
        wh_sec = str(wh_data.get("webhook_secret") or "")
    return ConnectionRuntimeContext(
        tenant_id=str(row.tenant_id),
        connection_id=str(row.id),
        provider=row.provider,
        auth_kind=row.auth_kind,
        instance_url=row.instance_url,
        region=row.region,
        external_account_id=row.external_account_id,
        scopes=tuple(row.scopes or ()),
        refresh_token=refresh_tok,
        webhook_secret=wh_sec,
        credentials=creds,
        config=dict(row.config_json or {}),
    )


def connection_verification_status(
    row: IntegrationConnection | None, *, env_configured: bool
) -> VerificationStatus:
    """Report honest verification status — LIVE_VERIFIED requires a real live-run artifact."""
    from app.integrations_v2.matrix import resolve_provider_verification

    provider_slug = row.provider if row is not None else ""
    tier, _ = resolve_provider_verification(
        provider_slug, conn_row=row, env_configured=env_configured
    )
    return tier


def mask_connection(row: IntegrationConnection, *, env_configured: bool = False) -> dict[str, Any]:
    """Return a JSON-safe representation of ``IntegrationConnection`` with zero secret material."""
    ver = connection_verification_status(row, env_configured=env_configured)
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "provider": row.provider,
        "category": row.category,
        "display_name": row.display_name or row.provider,
        "auth_kind": row.auth_kind,
        "status": row.status,
        "verification_status": ver.value,
        "external_account_id": row.external_account_id,
        "instance_url": row.instance_url,
        "region": row.region,
        "scopes": list(row.scopes or []),
        "has_credentials": bool(row.token_secret_ref),
        "has_refresh_token": bool(row.refresh_secret_ref),
        "has_webhook_secret": bool(row.webhook_secret_ref),
        "encryption_key_id": row.encryption_key_id,
        "token_expires_at": row.token_expires_at.isoformat() if row.token_expires_at else None,
        "config": dict(row.config_json or {}),
        "last_health_at": row.last_health_at.isoformat() if row.last_health_at else None,
        "last_health_ok": row.last_health_ok,
        "last_error": redact_secrets(row.last_error or ""),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def get_tenant_connection(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
) -> IntegrationConnection:
    stmt = select(IntegrationConnection).where(
        IntegrationConnection.id == connection_id,
        IntegrationConnection.tenant_id == tenant_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise IntegrationV2Error("Integration connection not found for tenant")
    return row


async def list_tenant_connections(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    category: str | None = None,
    provider: str | None = None,
) -> list[IntegrationConnection]:
    stmt = select(IntegrationConnection).where(IntegrationConnection.tenant_id == tenant_id)
    if category:
        stmt = stmt.where(IntegrationConnection.category == category)
    if provider:
        stmt = stmt.where(IntegrationConnection.provider == provider)
    stmt = stmt.order_by(IntegrationConnection.created_at.desc())
    return list((await session.execute(stmt)).scalars().all())


async def upsert_connection(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    provider: str,
    category: str,
    auth_kind: str,
    credentials: dict[str, Any],
    display_name: str = "",
    external_account_id: str = "default",
    instance_url: str = "",
    region: str = "us",
    scopes: list[str] | None = None,
    refresh_token: str | None = None,
    expires_in: int | None = None,
    webhook_secret: str | None = None,
    config: dict[str, Any] | None = None,
    created_by_user_id: uuid.UUID | None = None,
    status: str = "active",
) -> IntegrationConnection:
    """Create or update a tenant-scoped connection with AES-256-GCM sealed credentials."""
    ext_id = (external_account_id or "default").strip()[:160]
    stmt = select(IntegrationConnection).where(
        IntegrationConnection.tenant_id == tenant_id,
        IntegrationConnection.provider == provider,
        IntegrationConnection.external_account_id == ext_id,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()

    resolved_refresh = str(refresh_token or credentials.get("refresh_token") or "").strip()
    primary_creds = {k: v for k, v in credentials.items() if k != "refresh_token" and v is not None}

    token_env, key_id = seal_secret_bundle(
        primary_creds, tenant_id=tenant_id, provider=provider
    )
    refresh_env = ""
    if resolved_refresh:
        refresh_env, _ = seal_secret_bundle(
            {"refresh_token": resolved_refresh},
            tenant_id=tenant_id,
            provider=f"{provider}:refresh",
        )

    webhook_env = ""
    if webhook_secret:
        webhook_env, _ = seal_secret_bundle(
            {"webhook_secret": webhook_secret},
            tenant_id=tenant_id,
            provider=f"{provider}:webhook",
        )

    now = datetime.now(timezone.utc)
    expires_at = (
        now + timedelta(seconds=max(60, int(expires_in)))
        if expires_in is not None and int(expires_in) > 0
        else None
    )

    if row is None:
        row = IntegrationConnection(
            tenant_id=tenant_id,
            provider=provider,
            category=category,
            display_name=(display_name or provider)[:160],
            auth_kind=auth_kind,
            status=status,
            external_account_id=ext_id,
            instance_url=instance_url.strip()[:512],
            region=(region or "us").strip()[:32],
            scopes=list(scopes or []),
            token_secret_ref=token_env,
            refresh_secret_ref=refresh_env,
            webhook_secret_ref=webhook_env,
            encryption_key_id=key_id,
            token_expires_at=expires_at,
            config_json=dict(config or {}),
            created_by_user_id=created_by_user_id,
            last_error="",
        )
        session.add(row)
    else:
        if display_name:
            row.display_name = display_name[:160]
        row.auth_kind = auth_kind
        row.status = status
        if instance_url:
            row.instance_url = instance_url.strip()[:512]
        if region:
            row.region = region.strip()[:32]
        if scopes is not None:
            row.scopes = list(scopes)
        row.token_secret_ref = token_env
        if refresh_env:
            row.refresh_secret_ref = refresh_env
        if webhook_env:
            row.webhook_secret_ref = webhook_env
        row.encryption_key_id = key_id
        if expires_at is not None:
            row.token_expires_at = expires_at
        if config is not None:
            merged = dict(row.config_json or {})
            merged.update(config)
            row.config_json = merged
        row.last_error = ""
        row.updated_at = now

    await session.flush()
    return row


async def mark_connection_error(
    session: AsyncSession,
    row: IntegrationConnection,
    *,
    error_message: str,
    needs_reauth: bool = False,
) -> None:
    row.last_error = redact_secrets(error_message, max_len=480)
    row.last_health_ok = False
    row.last_health_at = datetime.now(timezone.utc)
    if needs_reauth:
        row.status = "needs_reauth"
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
