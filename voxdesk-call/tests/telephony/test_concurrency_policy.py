"""PART 13 / P13-05 — the concurrency product: reserve, burst ceiling, fairness.

Three numbers with different owners (base, inbound reserve, burst), and the rule
that makes them a product rather than a limit: **inbound may always use the
reserve, outbound may not**, and anything above the base is burst and therefore
billed.

The tests are split the way the feature is:

* pure arithmetic and leasing against the in-memory store (fast, deterministic);
* the persisted entitlement and the 24 h series against the real database.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.telephony.concurrency_policy import (
    BURST_ABSOLUTE_CAP,
    ConcurrencyError,
    ConcurrencyManager,
    CpsGate,
    FairQueue,
    Limit,
    burst_ceiling,
    load_limit,
    minute_bucket,
    record_sample,
    save_limit,
    series,
)


def test_the_burst_ceiling_is_derived_not_chosen() -> None:
    # 3x until the absolute cap takes over, then base+300.
    # 3x until the additive cap binds, then base+300.
    assert burst_ceiling(10) == 30
    assert burst_ceiling(100) == 300
    assert burst_ceiling(200) == 500, "3x binds until base+300 is smaller"
    assert BURST_ABSOLUTE_CAP == 300
    assert burst_ceiling(1_000) == 1_300


def test_a_reserve_that_swallows_the_base_is_refused() -> None:
    with pytest.raises(ValueError, match="leave room for at least one outbound call"):
        Limit(base=4, reserved_inbound=4)


def test_the_limit_serialises_its_derived_ceiling() -> None:
    payload = Limit(base=10, reserved_inbound=2).as_dict()
    assert payload["burst_ceiling"] == 30
    assert payload["base"] == 10
    assert payload["reserved_inbound"] == 2


@pytest.mark.asyncio
async def test_outbound_cannot_take_the_inbound_reserve() -> None:
    manager = ConcurrencyManager()
    limit = Limit(base=4, reserved_inbound=2)  # 2 outbound slots, 2 reserved
    tenant = uuid.uuid4()
    for index in range(2):
        await manager.acquire(
            tenant, f"out-{index}", direction="outbound", limit=limit, allow_burst=False
        )

    # A dialer without burst cannot take a third slot: that slot is the reserve.
    with pytest.raises(ConcurrencyError) as refused:
        await manager.acquire(tenant, "out-2", direction="outbound", limit=limit, allow_burst=False)
    assert refused.value.code == "concurrency_exhausted"

    # The reserve is still there, and only for inbound.
    await manager.acquire(tenant, "in-1", direction="inbound", limit=limit)
    await manager.acquire(tenant, "in-2", direction="inbound", limit=limit)
    snapshot = await manager.snapshot(tenant, limit)
    assert snapshot["in_use"] == {"total": 4, "inbound": 2, "outbound": 2}


@pytest.mark.asyncio
async def test_concurrent_in_memory_acquisition_never_overbooks() -> None:
    manager = ConcurrencyManager()
    limit = Limit(base=10, reserved_inbound=2)
    tenant = uuid.uuid4()

    async def try_outbound(index: int):
        try:
            return await manager.acquire(
                tenant,
                f"parallel-{index}",
                direction="outbound",
                limit=limit,
                allow_burst=False,
            )
        except ConcurrencyError:
            return None

    leases = await asyncio.gather(*(try_outbound(index) for index in range(40)))
    granted = [lease for lease in leases if lease is not None]
    snapshot = await manager.snapshot(tenant, limit)
    assert len(granted) == limit.outbound_base
    assert snapshot["in_use"] == {
        "total": limit.outbound_base,
        "inbound": 0,
        "outbound": limit.outbound_base,
    }


@pytest.mark.asyncio
async def test_beyond_the_base_is_burst_and_billable() -> None:
    manager = ConcurrencyManager()
    limit = Limit(base=2, reserved_inbound=1, burst_billing=True)
    tenant = uuid.uuid4()
    first = await manager.acquire(tenant, "a", direction="inbound", limit=limit)
    assert first.burst is False
    for call in ("b", "c"):
        await manager.acquire(tenant, call, direction="outbound", limit=limit)
    burst = await manager.acquire(tenant, "d", direction="outbound", limit=limit)
    assert burst.burst is True
    assert burst.billable_overage is True
    assert burst.ceiling == burst_ceiling(2)


@pytest.mark.asyncio
async def test_the_ceiling_stops_everything_even_inbound() -> None:
    manager = ConcurrencyManager()
    limit = Limit(base=1, reserved_inbound=0)
    # base=1 with no reserve: ceiling is 3, so the fourth call is refused.
    tenant = uuid.uuid4()
    await manager.acquire(tenant, "1", direction="inbound", limit=limit)
    await manager.acquire(tenant, "2", direction="outbound", limit=limit)
    await manager.acquire(tenant, "3", direction="outbound", limit=limit)
    with pytest.raises(ConcurrencyError, match="burst ceiling"):
        await manager.acquire(tenant, "4", direction="inbound", limit=limit)


@pytest.mark.asyncio
async def test_two_tenants_do_not_share_a_counter() -> None:
    manager = ConcurrencyManager()
    limit = Limit(base=2, reserved_inbound=1)
    busy, quiet = uuid.uuid4(), uuid.uuid4()
    for call in ("a", "b"):
        await manager.acquire(busy, call, direction="outbound", limit=limit)
    # The busy tenant is at its ceiling; the quiet one still has a full base.
    snapshot = await manager.snapshot(quiet, limit)
    assert snapshot["in_use"]["total"] == 0
    lease = await manager.acquire(quiet, "c", direction="outbound", limit=limit)
    assert lease.burst is False


@pytest.mark.asyncio
async def test_releasing_twice_is_reported_not_swallowed() -> None:
    manager = ConcurrencyManager()
    limit = Limit()
    tenant = uuid.uuid4()
    await manager.acquire(tenant, "call", direction="inbound", limit=limit)
    assert await manager.release(tenant, "call") is True
    assert await manager.release(tenant, "call") is False


def test_fair_queueing_interleaves_campaigns() -> None:
    queue = FairQueue()
    queue.extend("big", [f"big-{index}" for index in range(5)])
    queue.extend("small", ["small-0", "small-1"])
    order = [queue.pop() for _ in range(4)]
    keys = [key for key, _ in order]
    # Round-robin: the big campaign cannot starve the small one.
    assert keys[:2] == ["big", "small"]
    assert "small-0" in [item for _, item in order]


def test_fair_queueing_drains_to_empty() -> None:
    queue = FairQueue()
    queue.extend("a", [1, 2])
    queue.push("b", 3)
    drained = []
    while True:
        item = queue.pop()
        if item is None:
            break
        drained.append(item)
    assert len(drained) == 3


def test_provider_cps_is_a_separate_budget() -> None:
    gate = CpsGate()
    assert gate.allow("telnyx", 2, now=0.0) is True
    assert gate.allow("telnyx", 2, now=0.1) is True
    assert gate.allow("telnyx", 2, now=0.2) is False
    # A different carrier is not affected by telnyx's budget.
    assert gate.allow("twilio", 2, now=0.2) is True
    # Once the window passes, capacity returns.
    assert gate.allow("telnyx", 2, now=1.5) is True


@pytest.mark.asyncio
async def test_the_entitlement_is_persisted_per_tenant(db) -> None:
    from tests.conftest import make_tenant

    tenant = await make_tenant(db, "Concurrency Ltd")
    limit = Limit(base=12, reserved_inbound=3, burst_billing=False, provider_cps={"telnyx": 5})
    await save_limit(db, tenant.id, limit)
    await db.commit()

    loaded, row = await load_limit(db, tenant.id)
    assert row is not None
    assert loaded.base == 12
    assert loaded.reserved_inbound == 3
    assert loaded.burst_billing is False
    assert loaded.ceiling == burst_ceiling(12)
    assert loaded.cps_for("telnyx") == 5


@pytest.mark.asyncio
async def test_a_tenant_without_a_row_gets_the_defaults_and_no_row_created(db) -> None:
    from tests.conftest import make_tenant

    tenant = await make_tenant(db, "Default Ltd")
    limit, row = await load_limit(db, tenant.id)
    assert row is None
    assert limit == Limit()


@pytest.mark.asyncio
async def test_the_series_upserts_one_point_per_minute(db) -> None:
    from datetime import datetime, timezone

    from tests.conftest import make_tenant

    tenant = await make_tenant(db, "Series Ltd")
    moment = datetime(2026, 11, 10, 9, 15, 30, tzinfo=timezone.utc)
    await record_sample(
        db, tenant.id, {"in_use": {"total": 3, "inbound": 1, "outbound": 2}}, moment=moment
    )
    await record_sample(
        db, tenant.id, {"in_use": {"total": 5, "inbound": 2, "outbound": 3}}, moment=moment
    )
    await db.commit()

    points = await series(db, tenant.id, hours=24, now=moment)
    assert len(points) == 1, "a second sample in the same minute must not add a point"
    assert points[0]["total"] == 5
    assert points[0]["peak"] == 5
    # The bucket is one minute; the driver may hand it back tz-aware or naive.
    assert points[0]["bucket_start"].startswith(minute_bucket(moment).strftime("%Y-%m-%dT%H:%M:00"))


@pytest.mark.asyncio
async def test_the_series_is_honestly_empty_before_the_sampler_runs(db) -> None:
    from tests.conftest import make_tenant

    tenant = await make_tenant(db, "Quiet Ltd")
    assert await series(db, tenant.id, hours=24) == []


@pytest.mark.asyncio
async def test_a_series_window_outside_the_range_is_refused(db) -> None:
    with pytest.raises(ConcurrencyError, match="1-168 hours"):
        await series(db, uuid.uuid4(), hours=500)
