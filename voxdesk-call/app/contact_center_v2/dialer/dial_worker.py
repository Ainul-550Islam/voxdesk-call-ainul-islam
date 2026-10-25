"""Single-claim dialer ticks and the distributed calls-per-second guard."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import ContactCenterV2Error, NotFound
from app.contact_center_v2.repository import utcnow

from .dialer_service import policy_for, run_tick

_CPS_LUA = """
local values = redis.call('HMGET', KEYS[1], 'tokens', 'updated')
local burst = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local tokens = tonumber(values[1]) or burst
local updated = tonumber(values[2]) or now
local elapsed = math.max(0, now - updated) / 1000
tokens = math.min(burst, tokens + elapsed * rate)
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HMSET', KEYS[1], 'tokens', tokens, 'updated', now)
redis.call('PEXPIRE', KEYS[1], 120000)
return allowed
"""


class CpsTokenBucket:
    """Atomic per-policy CPS limiter with no provider side effect on refusal."""

    def __init__(self, redis_client: Any = None) -> None:
        self._redis = redis_client
        self._redis_injected = redis_client is not None
        self._redis_url = ""
        self._redis_loop: Any = None
        self._memory: dict[str, tuple[float, float]] = {}
        self._memory_lock = asyncio.Lock()

    def _client(self) -> Any:
        if self._redis_injected:
            return self._redis
        from app.core.config import settings as core_settings

        if not core_settings.redis_url:
            self._redis = None
            self._redis_url = ""
            self._redis_loop = None
        if core_settings.redis_url:
            loop = asyncio.get_running_loop()
            if (
                self._redis is None
                or self._redis_url != core_settings.redis_url
                or self._redis_loop is not loop
            ):
                import redis.asyncio as redis

                self._redis = redis.from_url(core_settings.redis_url)
                self._redis_url = core_settings.redis_url
                self._redis_loop = loop
        return self._redis

    async def acquire(
        self,
        key: str,
        *,
        rate: Decimal,
        burst: int,
        now: datetime | None = None,
    ) -> bool:
        if rate <= 0 or burst < 1:
            raise ValueError("CPS rate must be positive and burst at least one")
        moment = now or utcnow()
        timestamp_ms = int(moment.timestamp() * 1000)
        client = self._client()
        if client is not None:
            try:
                return bool(
                    await client.eval(
                        _CPS_LUA, 1, key, int(burst), float(rate), timestamp_ms
                    )
                )
            except Exception as exc:
                raise ContactCenterV2Error(
                    "cps_guard_unavailable",
                    "The configured Redis CPS guard is unavailable; dialing is paused",
                ) from exc

        # The repository supports an explicitly Redis-free development mode.
        # This path is process-local by construction and is never represented as
        # a distributed limiter. Production configurations use the Lua branch.
        async with self._memory_lock:
            tokens, updated = self._memory.get(key, (float(burst), float(timestamp_ms)))
            elapsed = max(0, timestamp_ms - int(updated)) / 1000
            tokens = min(float(burst), tokens + elapsed * float(rate))
            if tokens < 1:
                self._memory[key] = (tokens, float(timestamp_ms))
                return False
            self._memory[key] = (tokens - 1, float(timestamp_ms))
            return True


_bucket = CpsTokenBucket()


async def acquire_cps_permit(
    *,
    tenant_id: uuid.UUID,
    policy_id: uuid.UUID,
    rate: Decimal,
    burst: int,
    now: datetime | None = None,
) -> bool:
    return await _bucket.acquire(
        f"cc2:dialer:cps:{tenant_id}:{policy_id}", rate=rate, burst=burst, now=now
    )


async def execute_dialer_tick(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    policy_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
):
    """Verify job scope before executing one non-overlapping campaign tick."""
    policy = await policy_for(session, tenant_id, policy_id)
    if environment_id is not None and policy.environment_id != environment_id:
        raise NotFound("dialer policy")
    return await run_tick(session, tenant_id=tenant_id, policy_id=policy_id)


__all__ = ["CpsTokenBucket", "acquire_cps_permit", "execute_dialer_tick"]
