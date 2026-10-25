"""Overflow policy evaluation; execution stays in the canonical ACD extension."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .queue_manager import _apply_overflow, sweep_overflow


def next_overflow_action(
    *,
    waited_seconds: int,
    waiting: int,
    chain: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Choose the first due action in a configured, ordered overflow chain."""
    for action in chain:
        after = int(action.get("after_seconds", 0))
        depth = int(action.get("waiting", 0))
        if (after > 0 and waited_seconds >= after) or (depth > 0 and waiting > depth):
            kind = str(action.get("kind", ""))
            if kind not in {"callback", "escalate", "voicemail", "queue"}:
                raise ValueError("unsupported overflow action")
            return dict(action)
    return None


async def apply_overflow(session: AsyncSession, *, policy: Any, entry: Any, now: datetime) -> str:
    return await _apply_overflow(session, policy=policy, entry=entry, now=now)


async def sweep(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    return await sweep_overflow(session, tenant_id=tenant_id, queue_id=queue_id, now=now)


__all__ = ["apply_overflow", "next_overflow_action", "sweep", "sweep_overflow"]
