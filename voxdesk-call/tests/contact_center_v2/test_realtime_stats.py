from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.acd.realtime_stats import (
    realtime_stats_snapshot,
    summarize_queue_statistics,
)


def entry(offset: int, *, answered: bool, abandoned: bool = False):
    enqueued = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        enqueued_at=enqueued,
        assigned_at=enqueued + timedelta(seconds=offset) if answered else None,
        status="abandoned" if abandoned else "completed",
    )


def test_queue_sla_asa_aht_and_abandonment_use_explicit_denominators():
    result = summarize_queue_statistics([
        (entry(10, answered=True), 120),
        (entry(30, answered=True), 60),
        (entry(0, answered=False, abandoned=True), None),
    ])
    assert result == {
        "answered": 2,
        "abandoned": 1,
        "offered": 3,
        "service_level_percent": 50.0,
        "service_level_threshold_seconds": 20,
        "abandon_percent": 33.33,
        "asa_seconds": 20.0,
        "aht_seconds": 90.0,
        "aht_sample_size": 2,
    }
    empty = summarize_queue_statistics([])
    assert empty["offered"] == 0
    assert empty["service_level_percent"] is None
    assert empty["asa_seconds"] is None
    assert empty["aht_sample_size"] == 0


class OneSecondCache:
    def __init__(self):
        self.values = {}
        self.ttls = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value, *, ttl):
        self.values[key] = value
        self.ttls.append(ttl)


async def test_realtime_snapshot_cache_is_tenant_scoped_and_expires_after_one_second(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch,
):
    import uuid

    tenant_id = uuid.uuid4()
    cache = OneSecondCache()
    calls = 0

    async def durable(session, *, tenant_id):
        nonlocal calls
        calls += 1
        return {"tenant_id": str(tenant_id), "queues": [], "statistics": {"method": "test-double"}}

    monkeypatch.setattr(
        "app.contact_center_v2.acd.realtime_stats.durable_realtime_stats", durable
    )
    first = await realtime_stats_snapshot(db, tenant_id=tenant_id, cache=cache)
    replay = await realtime_stats_snapshot(db, tenant_id=tenant_id, cache=cache)
    assert first is replay
    assert calls == 1
    assert cache.ttls == [1]
    assert list(cache.values) == [f"cc2:wallboard:{tenant_id}"]
