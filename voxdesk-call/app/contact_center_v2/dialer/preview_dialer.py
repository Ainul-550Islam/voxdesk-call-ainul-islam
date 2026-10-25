"""Agent-owned preview workflow interface over the canonical dialer service."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from .dialer_service import preview_dial, preview_next, preview_skip


async def next_contact(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    policy_id: uuid.UUID,
    agent_id: uuid.UUID,
    now: datetime | None = None,
):
    """Atomically claim one eligible contact for this agent's preview timer."""
    return await preview_next(
        session,
        tenant_id=tenant_id,
        policy_id=policy_id,
        agent_id=agent_id,
        now=now,
    )


async def dial_claimed_contact(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    contact_id: uuid.UUID,
    agent_id: uuid.UUID,
    idempotency_key: str,
    now: datetime | None = None,
):
    """Dial only the caller-owned, unexpired preview reservation."""
    return await preview_dial(
        session,
        tenant_id=tenant_id,
        contact_id=contact_id,
        agent_id=agent_id,
        idempotency_key=idempotency_key,
        now=now,
    )


async def skip_claimed_contact(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    contact_id: uuid.UUID,
    agent_id: uuid.UUID,
):
    """Record an agent decision and release the single preview claim."""
    return await preview_skip(
        session,
        tenant_id=tenant_id,
        contact_id=contact_id,
        agent_id=agent_id,
    )


__all__ = ["dial_claimed_contact", "next_contact", "skip_claimed_contact"]
