"""Transaction-scoped PostgreSQL tenant context and RLS helpers."""

from __future__ import annotations

import uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_tenant_context(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Set the tenant for the current PostgreSQL transaction.

    SQLite is used by the repository's unit tests and has no RLS/settings
    equivalent; production configuration is PostgreSQL and the migration
    installs the real policies there.
    """
    bind = session.bind
    if bind is None or bind.dialect.name != "postgresql":
        return
    await session.execute(
        text("select set_config('app.tenant_id', :tenant_id, true)"), {"tenant_id": str(tenant_id)}
    )


async def set_public_webhook_lookup(session: AsyncSession, endpoint_id: uuid.UUID) -> None:
    """Allow one anonymous webhook endpoint lookup before tenant context exists.

    The migration's narrow policy matches only this exact endpoint id; the
    handler installs the discovered tenant context before reading or writing
    any other tenant row.
    """
    bind = session.bind
    if bind is None or bind.dialect.name != "postgresql":
        return
    await session.execute(
        text("select set_config('app.webhook_endpoint_id', :endpoint_id, true)"),
        {"endpoint_id": str(endpoint_id)},
    )


async def clear_tenant_context(session: AsyncSession) -> None:
    await session.execute(text("select set_config('app.tenant_id', '', true)"))
    await session.execute(text("select set_config('app.webhook_endpoint_id', '', true)"))


async def require_tenant_context(session: AsyncSession) -> uuid.UUID:
    value = (
        await session.execute(text("select current_setting('app.tenant_id', true)"))
    ).scalar_one_or_none()
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise RuntimeError("tenant context is not installed; refusing tenant query") from None
