"""Provider catalog served to UI with live connection-health status (CONFIGURED / NOT_CONFIGURED / ERROR)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.integrations_v2_models import IntegrationConnection
from app.integrations_v2.core.connection_store import list_tenant_connections


def compute_catalog_status(
    conn_row: IntegrationConnection | None, *, env_configured: bool
) -> str:
    """Compute honest UI status from real connection health — never hardcoded 'LIVE'.

    Returns one of ``CONFIGURED``, ``NOT_CONFIGURED``, or ``ERROR``.
    """
    if conn_row is None:
        return "NOT_CONFIGURED"
    if conn_row.status in ("error", "expired", "needs_reauth") or conn_row.last_health_ok is False:
        return "ERROR"
    if conn_row.status == "active" and bool(conn_row.token_secret_ref):
        return "CONFIGURED"
    return "NOT_CONFIGURED"


async def build_provider_catalog(
    session: AsyncSession, *, tenant_id: uuid.UUID | None = None
) -> list[dict[str, Any]]:
    """Build the 11-provider catalog grouped with live per-tenant connection status."""
    from app.integrations_v2.matrix import resolve_provider_verification
    from app.integrations_v2.register_all import (
        PROVIDERS_V2,
        is_provider_env_configured,
    )

    by_provider: dict[str, IntegrationConnection] = {}
    if tenant_id is not None:
        rows = await list_tenant_connections(session, tenant_id=tenant_id)
        for row in rows:
            if row.provider not in by_provider or row.status == "active":
                by_provider[row.provider] = row

    items: list[dict[str, Any]] = []
    for slug, cls in PROVIDERS_V2.items():
        env_ok = is_provider_env_configured(slug)
        conn_row = by_provider.get(slug)
        cat_status = compute_catalog_status(conn_row, env_configured=env_ok)
        ver_status, live_ts = resolve_provider_verification(
            slug, conn_row=conn_row, env_configured=env_ok
        )
        desc = cls.descriptor(env_configured=env_ok, verification=ver_status)
        desc["id"] = slug
        desc["slug"] = slug
        desc["status"] = cat_status
        desc["ui_status"] = cat_status
        desc["auth_modes"] = (
            ["oauth2", "api_key"]
            if cls.auth_kind.value == "oauth2_or_api_key"
            else [cls.auth_kind.value]
        )
        desc["docs_note"] = cls.docs_url
        desc["last_health_ok"] = conn_row.last_health_ok if conn_row is not None else None
        desc["connected"] = conn_row is not None and conn_row.status == "active"
        desc["connection_id"] = str(conn_row.id) if conn_row is not None else None
        desc["connection_status"] = (
            conn_row.status if conn_row is not None else "disconnected"
        )
        desc["last_error"] = conn_row.last_error if conn_row is not None else ""
        desc["last_health_at"] = (
            conn_row.last_health_at.isoformat()
            if (conn_row is not None and conn_row.last_health_at is not None)
            else None
        )
        desc["live_verified_at"] = live_ts
        items.append(desc)
    return items
