"""One-second live queue statistics with durable 30-day accounting."""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.models import QueueEntry
from app.contact_center_v2.acd.wallboard import wallboard_snapshot
from app.contact_center_v2.repository import as_utc, utcnow
from app.core.cache import get_cache
from app.db.models import Call

STATS_WINDOW = timedelta(days=30)
DEFAULT_SERVICE_LEVEL_THRESHOLD_SECONDS = 20


def _percent(numerator: int, denominator: int) -> float | None:
    return round((numerator / denominator) * 100, 2) if denominator else None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def summarize_queue_statistics(
    rows: Iterable[tuple[QueueEntry, float | None]],
    *,
    service_level_threshold_seconds: int = DEFAULT_SERVICE_LEVEL_THRESHOLD_SECONDS,
) -> dict[str, Any]:
    """Calculate explicit queue metrics from canonical entries and call durations.

    ASA includes answered entries, including currently active assignments. AHT
    includes only positive observed call durations. Abandonment denominator is
    answered plus abandoned offerings; machine-answered dialer attempts are not
    queue entries and therefore cannot inflate these queue metrics.
    """
    entries = list(rows)
    answered = [entry for entry, _ in entries if entry.assigned_at is not None]
    abandoned = [entry for entry, _ in entries if entry.status == "abandoned"]
    waits = [max(0.0, (as_utc(entry.assigned_at) - as_utc(entry.enqueued_at)).total_seconds()) for entry in answered]
    durations = [float(duration) for entry, duration in entries if entry.assigned_at is not None and duration and duration > 0]
    offered = len(answered) + len(abandoned)
    return {
        "answered": len(answered),
        "abandoned": len(abandoned),
        "offered": offered,
        "service_level_percent": _percent(
            sum(wait <= service_level_threshold_seconds for wait in waits),
            len(waits),
        ),
        "service_level_threshold_seconds": service_level_threshold_seconds,
        "abandon_percent": _percent(len(abandoned), offered),
        "asa_seconds": _mean(waits),
        "aht_seconds": _mean(durations),
        "aht_sample_size": len(durations),
    }


async def durable_realtime_stats(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
) -> dict[str, Any]:
    """Build a tenant-scoped snapshot exclusively from durable rows."""
    snapshot = await wallboard_snapshot(session, tenant_id=tenant_id)
    moment = utcnow()
    rows = (
        await session.execute(
            select(QueueEntry, Call.duration_seconds)
            .join(Call, Call.id == QueueEntry.call_id)
            .where(
                QueueEntry.tenant_id == tenant_id,
                Call.tenant_id == tenant_id,
                QueueEntry.enqueued_at >= moment - STATS_WINDOW,
            )
        )
    ).all()
    grouped: dict[uuid.UUID, list[tuple[QueueEntry, float | None]]] = {}
    for entry, duration in rows:
        grouped.setdefault(entry.queue_id, []).append((entry, duration))
    for queue in snapshot["queues"]:
        threshold = DEFAULT_SERVICE_LEVEL_THRESHOLD_SECONDS
        queue.update(
            summarize_queue_statistics(
                grouped.get(uuid.UUID(queue["queue_id"]), []),
                service_level_threshold_seconds=threshold,
            )
        )
    snapshot["statistics"] = {
        "window_seconds": int(STATS_WINDOW.total_seconds()),
        "method": "durable_queue_entries_v1",
        "cache_ttl_seconds": 1,
    }
    return snapshot


async def realtime_stats_snapshot(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    cache: Any = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Return a one-second cached snapshot; a cache miss always reads Postgres."""
    if not use_cache:
        return await durable_realtime_stats(session, tenant_id=tenant_id)
    cache = cache or get_cache()
    key = f"cc2:wallboard:{tenant_id}"
    cached = await cache.get(key)
    if isinstance(cached, dict):
        return cached
    snapshot = await durable_realtime_stats(session, tenant_id=tenant_id)
    await cache.set(key, snapshot, ttl=1)
    return snapshot


__all__ = [
    "DEFAULT_SERVICE_LEVEL_THRESHOLD_SECONDS",
    "STATS_WINDOW",
    "durable_realtime_stats",
    "realtime_stats_snapshot",
    "summarize_queue_statistics",
]
