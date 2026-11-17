"""Concurrency entitlement (PART 13, P13-05).

The product is not "a number of calls". It is three numbers with different
owners: a **base** the tenant pays for, a slice of that base **reserved for
inbound** so an outbound campaign can never busy out the phone line, and a
**burst ceiling** that exists only above the base and is always billed.

Two rules keep this honest:

* the ceiling is derived, never stored -- ``min(3 * base, base + 300)`` -- so a
  row cannot carry a number the billing system has not agreed to;
* an inbound call may always use the reserved slice, even when outbound has
  taken every other slot. A tenant that loses inbound revenue to its own
  dialer is a support ticket nobody can explain;
* burst is **opt-in per call**. A batch dialer that has not been granted burst
  passes ``allow_burst=False`` and then physically cannot take the reserve,
  while an interactive outbound call may burst because a person is waiting.

The counters live behind a small store interface. Production passes a Redis
client (two app pods must see the same number); tests use the in-memory store,
which is also the honest fallback when Redis is not configured -- a single-pod
deployment is a real deployment, and refusing to place calls because a cache is
down would be worse than counting locally.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Protocol

#: Base, reserved and burst defaults for a tenant that has never been configured.
DEFAULT_BASE = 10
DEFAULT_RESERVED_INBOUND = 2
BURST_MULTIPLIER = 3
BURST_ABSOLUTE_CAP = 300

#: Directions a lease can be taken in. Anything else is a bug, not a call.
DIRECTIONS = ("inbound", "outbound")


def burst_ceiling(base: int) -> int:
    """The only definition of the burst ceiling in the codebase."""
    if base < 0:
        raise ValueError("base concurrency cannot be negative")
    return min(BURST_MULTIPLIER * base, base + BURST_ABSOLUTE_CAP)


class ConcurrencyError(Exception):
    """A refused lease. Carries a stable ``code`` for the API layer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Limit:
    """The entitlement for one tenant, resolved from its row or the defaults."""

    base: int = DEFAULT_BASE
    reserved_inbound: int = DEFAULT_RESERVED_INBOUND
    burst_billing: bool = True
    provider_cps: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.base < 1:
            raise ValueError("base must be at least 1")
        if self.reserved_inbound < 0:
            raise ValueError("reserved_inbound cannot be negative")
        if self.reserved_inbound >= self.base:
            raise ValueError("reserved_inbound must leave room for at least one outbound call")

    @classmethod
    def from_row(cls, row: Any | None) -> "Limit":
        if row is None:
            return cls()
        return cls(
            base=int(row.base),
            reserved_inbound=int(row.reserved_inbound),
            burst_billing=bool(row.burst_billing),
            provider_cps=dict(row.provider_cps or {}),
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "Limit":
        return cls(
            base=int(payload.get("base", DEFAULT_BASE)),
            reserved_inbound=int(payload.get("reserved_inbound", DEFAULT_RESERVED_INBOUND)),
            burst_billing=bool(payload.get("burst_billing", True)),
            provider_cps={str(k): int(v) for k, v in (payload.get("provider_cps") or {}).items()},
        )

    @property
    def ceiling(self) -> int:
        return burst_ceiling(self.base)

    @property
    def outbound_base(self) -> int:
        """How many outbound calls fit inside the paid base."""
        return self.base - self.reserved_inbound

    def cps_for(self, provider: str) -> int | None:
        value = self.provider_cps.get(provider)
        return None if value is None else int(value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "reserved_inbound": self.reserved_inbound,
            "burst_ceiling": self.ceiling,
            "burst_billing": self.burst_billing,
            "provider_cps": dict(self.provider_cps),
        }


@dataclass(frozen=True)
class Lease:
    """A granted slot. ``burst`` means it sits above the paid base."""

    tenant_id: str
    call_id: str
    direction: str
    burst: bool
    base: int
    ceiling: int

    @property
    def billable_overage(self) -> bool:
        """Burst is only billable on the outbound leg.

        An inbound call inside the reserve is paid for by the caller, and a
        tenant cannot be charged for a call it did not choose to place.
        """
        return self.burst and self.direction == "outbound"


class CounterStore(Protocol):
    async def counts(self, tenant_id: str) -> tuple[int, int]:
        """Return ``(inbound, outbound)``."""

    async def add(self, tenant_id: str, direction: str, call_id: str) -> tuple[int, int]:
        """Add a call and return the new ``(inbound, outbound)`` counts."""

    async def try_acquire(
        self,
        tenant_id: str,
        call_id: str,
        *,
        direction: str,
        limit: Limit,
        allow_burst: bool,
    ) -> bool | None:
        """Atomically lease a slot; return whether it is burst, or ``None`` if full."""

    async def remove(self, tenant_id: str, call_id: str) -> bool:
        """Remove a call; ``False`` if it was not there (double release)."""


class InMemoryCounterStore:
    """Per-process counters. Correct for one pod, a fallback everywhere else."""

    def __init__(self) -> None:
        self._calls: dict[str, dict[str, str]] = defaultdict(dict)
        self._lock = asyncio.Lock()

    async def counts(self, tenant_id: str) -> tuple[int, int]:
        calls = self._calls.get(tenant_id, {})
        inbound = sum(1 for direction in calls.values() if direction == "inbound")
        return inbound, len(calls) - inbound

    async def add(self, tenant_id: str, direction: str, call_id: str) -> tuple[int, int]:
        async with self._lock:
            self._calls[tenant_id][call_id] = direction
        return await self.counts(tenant_id)

    async def try_acquire(
        self,
        tenant_id: str,
        call_id: str,
        *,
        direction: str,
        limit: Limit,
        allow_burst: bool,
    ) -> bool | None:
        async with self._lock:
            calls = self._calls[tenant_id]
            if call_id in calls:
                raise ConcurrencyError("duplicate_lease", "call already holds a concurrency lease")
            inbound = sum(value == "inbound" for value in calls.values())
            outbound = len(calls) - inbound
            total = inbound + outbound
            if direction == "inbound":
                if total >= limit.ceiling:
                    return None
                burst = total >= limit.base
            elif total < limit.base and outbound < limit.outbound_base:
                burst = False
            elif allow_burst and total < limit.ceiling:
                burst = True
            else:
                return None
            calls[call_id] = direction
            return burst

    async def remove(self, tenant_id: str, call_id: str) -> bool:
        async with self._lock:
            return self._calls.get(tenant_id, {}).pop(call_id, None) is not None


_ACQUIRE_SLOT_LUA = """
local members = redis.call('ZRANGE', KEYS[1], 0, -1)
local inbound = 0
local outbound = 0
for _, member in ipairs(members) do
  local separator = string.find(member, ':', 1, true)
  local member_direction = string.sub(member, 1, separator - 1)
  local member_call = string.sub(member, separator + 1)
  if member_call == ARGV[2] then return -1 end
  if member_direction == 'inbound' then inbound = inbound + 1 else outbound = outbound + 1 end
end
local total = inbound + outbound
local direction = ARGV[1]
local base = tonumber(ARGV[3])
local reserved = tonumber(ARGV[4])
local ceiling = tonumber(ARGV[5])
local allow_burst = tonumber(ARGV[6]) == 1
local burst = false
if direction == 'inbound' then
  if total >= ceiling then return 0 end
  burst = total >= base
elseif total < base and outbound < (base - reserved) then
  burst = false
elseif allow_burst and total < ceiling then
  burst = true
else
  return 0
end
local member = direction .. ':' .. ARGV[2]
redis.call('ZADD', KEYS[1], 'NX', ARGV[7], member)
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[8]))
if burst then return 2 else return 1 end
"""


class RedisCounterStore:
    """Shared counters for a multi-pod deployment.

    One sorted set per tenant, scored by start time; a member is
    ``"{direction}:{call_id}"``. The expiry is a safety net, not the mechanism:
    a pod that dies mid-call leaves a member behind, and the 6 h TTL means the
    tenant's counter heals itself instead of staying permanently full.
    """

    TTL_SECONDS = 6 * 60 * 60

    def __init__(self, client: Any) -> None:
        self._client = client

    @staticmethod
    def _key(tenant_id: str) -> str:
        return f"voxdesk:concurrency:{tenant_id}"

    async def _members(self, tenant_id: str) -> dict[str, str]:
        raw = await self._client.zrange(self._key(tenant_id), 0, -1)
        members = {}
        for item in raw:
            value = item.decode() if isinstance(item, bytes) else str(item)
            direction, _, call_id = value.partition(":")
            members[call_id] = direction
        return members

    async def counts(self, tenant_id: str) -> tuple[int, int]:
        members = await self._members(tenant_id)
        inbound = sum(1 for direction in members.values() if direction == "inbound")
        return inbound, len(members) - inbound

    async def add(self, tenant_id: str, direction: str, call_id: str) -> tuple[int, int]:
        key = self._key(tenant_id)
        await self._client.zadd(key, {f"{direction}:{call_id}": time.time()})
        await self._client.expire(key, self.TTL_SECONDS)
        return await self.counts(tenant_id)

    async def try_acquire(
        self,
        tenant_id: str,
        call_id: str,
        *,
        direction: str,
        limit: Limit,
        allow_burst: bool,
    ) -> bool | None:
        result = int(
            await self._client.eval(
                _ACQUIRE_SLOT_LUA,
                1,
                self._key(tenant_id),
                direction,
                call_id,
                limit.base,
                limit.reserved_inbound,
                limit.ceiling,
                int(allow_burst),
                int(time.time() * 1000),
                self.TTL_SECONDS,
            )
        )
        if result == -1:
            raise ConcurrencyError("duplicate_lease", "call already holds a concurrency lease")
        if result == 0:
            return None
        return result == 2

    async def remove(self, tenant_id: str, call_id: str) -> bool:
        members = await self._members(tenant_id)
        if call_id not in members:
            return False
        removed = await self._client.zrem(self._key(tenant_id), f"{members[call_id]}:{call_id}")
        return bool(removed)


class ConcurrencyManager:
    """Grants and releases call slots against a tenant's :class:`Limit`."""

    def __init__(self, store: CounterStore | None = None) -> None:
        self.store: CounterStore = store or InMemoryCounterStore()

    async def acquire(
        self,
        tenant_id: uuid.UUID | str,
        call_id: uuid.UUID | str,
        *,
        direction: str,
        limit: Limit,
        allow_burst: bool = True,
    ) -> Lease:
        key = str(tenant_id)
        call = str(call_id)
        if direction not in DIRECTIONS:
            raise ConcurrencyError("invalid_direction", f"unknown direction {direction!r}")
        reserve = getattr(self.store, "try_acquire", None)
        if reserve is not None:
            burst = await reserve(
                key,
                call,
                direction=direction,
                limit=limit,
                allow_burst=allow_burst,
            )
            if burst is not None:
                return Lease(key, call, direction, burst, limit.base, limit.ceiling)
            inbound, outbound = await self.store.counts(key)
        else:
            # Compatibility for small external test stores; production stores
            # implement atomic try_acquire to avoid a cross-worker overbooking race.
            inbound, outbound = await self.store.counts(key)
            total = inbound + outbound
            if direction == "inbound":
                if total < limit.ceiling:
                    burst = total >= limit.base
                    await self.store.add(key, direction, call)
                    return Lease(key, call, direction, burst, limit.base, limit.ceiling)
            elif total < limit.base and outbound < limit.outbound_base:
                await self.store.add(key, direction, call)
                return Lease(key, call, direction, False, limit.base, limit.ceiling)
            elif allow_burst and total < limit.ceiling:
                await self.store.add(key, direction, call)
                return Lease(key, call, direction, True, limit.base, limit.ceiling)

        total = inbound + outbound
        if direction == "inbound":
            detail = f"inbound refused: {total} calls at the burst ceiling {limit.ceiling}"
        else:
            detail = (
                f"outbound refused: {outbound} outbound calls, {total} total, "
                f"outbound cap {limit.outbound_base}, ceiling {limit.ceiling}"
            )
        raise ConcurrencyError("concurrency_exhausted", detail)

    async def release(self, tenant_id: uuid.UUID | str, call_id: uuid.UUID | str) -> bool:
        return await self.store.remove(str(tenant_id), str(call_id))

    async def snapshot(self, tenant_id: uuid.UUID | str, limit: Limit) -> dict[str, Any]:
        inbound, outbound = await self.store.counts(str(tenant_id))
        total = inbound + outbound
        return {
            **limit.as_dict(),
            "tenant_id": str(tenant_id),
            "in_use": {"total": total, "inbound": inbound, "outbound": outbound},
            "remaining": max(0, limit.ceiling - total),
            "at_base": total >= limit.base,
            "over_base": max(0, total - limit.base),
        }


_MANAGER = ConcurrencyManager()


def get_concurrency_manager() -> ConcurrencyManager:
    """Return the configured process-wide manager used by APIs and call paths."""
    return _MANAGER


def configure_counter_store(store: CounterStore | None) -> None:
    """Install the process-wide counter store (Redis in multi-worker production)."""
    global _MANAGER
    _MANAGER = ConcurrencyManager(store)


@dataclass
class FairQueue:
    """Round-robin over keys (campaigns), so one big batch cannot starve others.

    Built as a plain in-process queue because the dialer pulls one lead at a
    time per available slot; the ordering decision is what matters, and it is
    testable without a broker in the loop.
    """

    _queues: dict[str, deque[Any]] = field(default_factory=dict)
    _order: deque[str] = field(default_factory=deque)

    def push(self, key: str, item: Any) -> None:
        if key not in self._queues:
            self._queues[key] = deque()
            self._order.append(key)
        self._queues[key].append(item)

    def pop(self) -> tuple[str, Any] | None:
        while self._order:
            key = self._order.popleft()
            queue = self._queues.get(key)
            if not queue:
                self._queues.pop(key, None)
                continue
            item = queue.popleft()
            if queue:
                self._order.append(key)
            else:
                self._queues.pop(key, None)
            return key, item
        return None

    def extend(self, key: str, items: Iterable[Any]) -> None:
        for item in items:
            self.push(key, item)

    def __len__(self) -> int:
        return sum(len(queue) for queue in self._queues.values())

    def pending_by_key(self) -> dict[str, int]:
        return {key: len(queue) for key, queue in self._queues.items()}


@dataclass
class CpsGate:
    """Per-provider calls-per-second budget.

    A carrier does not care how many calls a tenant bought; it rejects the
    eleven in a second that exceed its own limit. This gate is deliberately
    separate from the concurrency limit for that reason.
    """

    window_seconds: float = 1.0

    _stamps: dict[str, deque[float]] = field(default_factory=dict)

    def allow(self, provider: str, cps: int | None, *, now: float | None = None) -> bool:
        if cps is None:
            return True
        moment = time.monotonic() if now is None else now
        stamps = self._stamps.setdefault(provider, deque())
        while stamps and moment - stamps[0] >= self.window_seconds:
            stamps.popleft()
        if len(stamps) >= cps:
            return False
        stamps.append(moment)
        return True

    def retry_after(self, provider: str, *, now: float | None = None) -> float:
        stamps = self._stamps.get(provider)
        if not stamps:
            return 0.0
        moment = time.monotonic() if now is None else now
        return max(0.0, self.window_seconds - (moment - stamps[0]))


# ---------------------------------------------------------------------------
# Persistence helpers
#
# The limit is a row, the series is a table, and both are read by the API. They
# live here rather than in the route module so the dialer can ask the same
# questions without going through HTTP.
# ---------------------------------------------------------------------------

#: How many points the 24 h chart can carry. One per minute is honest: the
#: sampler writes at most that often, and a denser line would be interpolation.
MAX_SERIES_POINTS = 24 * 60


async def load_limit(session, tenant_id) -> tuple["Limit", Any]:
    """Return ``(limit, row)``; ``row`` is ``None`` for a tenant with no row."""
    from sqlalchemy import select

    from app.db.telephony_models import TelephonyConcurrencyLimit

    row = (
        await session.execute(
            select(TelephonyConcurrencyLimit).where(
                TelephonyConcurrencyLimit.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    return Limit.from_row(row), row


async def acquire_call_slot(
    session,
    tenant_id: uuid.UUID | str,
    call_id: uuid.UUID | str,
    *,
    direction: str,
    allow_burst: bool = True,
) -> Lease:
    """Load the tenant entitlement and atomically reserve one production call slot."""
    limit, _row = await load_limit(session, tenant_id)
    return await get_concurrency_manager().acquire(
        tenant_id,
        call_id,
        direction=direction,
        limit=limit,
        allow_burst=allow_burst,
    )


async def release_call_slot(tenant_id: uuid.UUID | str, call_id: uuid.UUID | str) -> bool:
    """Release a call slot at terminal state; idempotent callers may ignore False."""
    return await get_concurrency_manager().release(tenant_id, call_id)


async def save_limit(session, tenant_id, limit: "Limit"):
    """Create or update the tenant's limit row."""
    from sqlalchemy import select

    from app.db.telephony_models import TelephonyConcurrencyLimit

    row = (
        await session.execute(
            select(TelephonyConcurrencyLimit).where(
                TelephonyConcurrencyLimit.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = TelephonyConcurrencyLimit(tenant_id=tenant_id)
        session.add(row)
    row.base = limit.base
    row.reserved_inbound = limit.reserved_inbound
    row.burst_billing = limit.burst_billing
    row.provider_cps = dict(limit.provider_cps)
    await session.flush()
    return row


def minute_bucket(moment: datetime, *, minutes: int = 1) -> datetime:
    """Floor a timestamp to the sampler's bucket (naive UTC, as the column is)."""
    naive = moment.astimezone(tz=None).replace(tzinfo=None) if moment.tzinfo else moment
    return naive.replace(second=0, microsecond=0) - timedelta(minutes=naive.minute % max(1, minutes))


async def record_sample(session, tenant_id, snapshot: dict, *, moment=None):
    """Write one series point for a snapshot from :meth:`ConcurrencyManager.snapshot`."""
    from sqlalchemy import select

    from app.db.telephony_models import TelephonyConcurrencySample

    now = moment or datetime.now(timezone.utc)
    bucket = minute_bucket(now)
    in_use = snapshot.get("in_use", {})
    total = int(in_use.get("total", 0))
    row = (
        await session.execute(
            select(TelephonyConcurrencySample).where(
                TelephonyConcurrencySample.tenant_id == tenant_id,
                TelephonyConcurrencySample.bucket_start == bucket,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = TelephonyConcurrencySample(tenant_id=tenant_id, bucket_start=bucket)
        session.add(row)
    row.captured_at = now.replace(tzinfo=None)
    row.inbound = int(in_use.get("inbound", 0))
    row.outbound = int(in_use.get("outbound", 0))
    row.peak = max(int(row.peak or 0), total)
    await session.flush()
    return row


async def series(session, tenant_id, *, hours: int = 24, now=None) -> list[dict]:
    """The stored series, oldest first, at most one point per minute."""
    from sqlalchemy import select

    from app.db.telephony_models import TelephonyConcurrencySample

    if not 1 <= int(hours) <= 168:
        raise ConcurrencyError("invalid_window", "the series window is 1-168 hours")
    moment = (now or datetime.now(timezone.utc)).replace(tzinfo=None)
    since = moment - timedelta(hours=int(hours))
    rows = (
        await session.execute(
            select(TelephonyConcurrencySample)
            .where(
                TelephonyConcurrencySample.tenant_id == tenant_id,
                TelephonyConcurrencySample.bucket_start >= since,
            )
            .order_by(TelephonyConcurrencySample.bucket_start)
            .limit(MAX_SERIES_POINTS)
        )
    ).scalars().all()
    return [
        {
            "bucket_start": row.bucket_start.isoformat(),
            "inbound": int(row.inbound or 0),
            "outbound": int(row.outbound or 0),
            "total": int(row.inbound or 0) + int(row.outbound or 0),
            "peak": int(row.peak or 0),
        }
        for row in rows
    ]
