"""Tenant-scoped SSE wallboard stream backed by one-second live snapshots."""
from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

from app.contact_center_v2.acd.realtime_stats import realtime_stats_snapshot
from app.contact_center_v2.settings import settings
from app.db.session import get_sessionmaker


async def stream_wallboard(
    *,
    tenant_id: uuid.UUID,
    session_factory: Callable[[], Any] | None = None,
    max_snapshots: int | None = None,
    sleep: Callable[[float], Any] = asyncio.sleep,
) -> AsyncIterator[str]:
    """Yield real SSE frames until disconnect/cancellation or a test bound.

    A fresh database session is used for every poll so the returned streaming
    response never depends on a closed request-scoped session. Exceptions are
    not converted into successful-looking snapshots; they terminate the stream.
    """
    maker = session_factory or get_sessionmaker()
    interval = settings.realtime_snapshot_interval_ms / 1000
    emitted = 0
    while max_snapshots is None or emitted < max_snapshots:
        async with maker() as session:
            snapshot = await realtime_stats_snapshot(session, tenant_id=tenant_id)
        payload = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=True)
        yield f"event: wallboard.snapshot\ndata: {payload}\n\n"
        emitted += 1
        if max_snapshots is None or emitted < max_snapshots:
            await sleep(interval)


__all__ = ["stream_wallboard"]
