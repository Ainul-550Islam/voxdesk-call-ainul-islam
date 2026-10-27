"""One process-safe lock per periodic tick.

The scheduler runs as a separate process and is deployed more than once: a
restart overlap, two replicas, or a blue/green switch all mean two schedulers
are awake at the same moment. Every periodic pass in this repository therefore
has to assume a competitor for the same work.

Two backends, one contract:

* **Redis** when ``REDIS_URL`` is configured. ``SET key token NX PX ttl`` is the
  acquire; the release is the usual compare-and-delete Lua script so a lock that
  already expired and was re-taken by someone else cannot be released by the
  process that lost it.
* **A per-process ``asyncio.Lock``** when Redis is not configured. That is the
  development and test case, and it still does the one thing that matters
  locally: two ticks with the same name never overlap inside one event loop.

``yield``s ``True`` when the lock was acquired and ``False`` when another worker
holds it. Callers treat ``False`` as "somebody else is doing this pass" and do
nothing -- never as an error, and never by running the pass anyway.

This module holds no state beyond the client it memoises and the local lock
table, and it never raises for a Redis outage: an unreachable Redis means the
pass does not run, which is the safe direction for a maintenance loop that
would otherwise double-execute against a database that is still up.
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator

import structlog

log = structlog.get_logger(__name__)

#: Default lease. A tick that holds a lock longer than this is already stuck;
#: the lease expiring is what lets the next pass make progress.
DEFAULT_TTL_SECONDS = 900

_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""

_LOCAL_LOCKS: dict[str, asyncio.Lock] = {}
_REDIS_CLIENT: object | None = None
_REDIS_URL = ""
_REDIS_LOOP: object | None = None


def _redis(client: object | None = None) -> object | None:
    """Return a Redis client, or ``None`` in explicit local mode."""
    global _REDIS_CLIENT, _REDIS_URL, _REDIS_LOOP
    if client is not None:
        return client
    from app.core.config import settings

    if not getattr(settings, "redis_url", ""):
        return None
    loop = asyncio.get_running_loop()
    if (
        _REDIS_CLIENT is None
        or _REDIS_URL != settings.redis_url
        or _REDIS_LOOP is not loop
    ):
        import redis.asyncio as redis

        _REDIS_CLIENT = redis.from_url(settings.redis_url)
        _REDIS_URL = settings.redis_url
        _REDIS_LOOP = loop
    return _REDIS_CLIENT


@contextlib.asynccontextmanager
async def tick_lock(
    name: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    client: object | None = None,
) -> AsyncIterator[bool]:
    """Hold ``name`` for the duration of the block, or yield ``False``.

    ``name`` is the *logical* tick, not the process: every replica must use the
    same string for the same work, which is why the scheduler builds it from its
    own tick table instead of accepting one from the caller.
    """
    if not name:
        raise ValueError("tick_lock requires a non-empty name")

    redis_client = _redis(client)
    if redis_client is None:
        lock = _LOCAL_LOCKS.setdefault(name, asyncio.Lock())
        if lock.locked():
            yield False
            return
        async with lock:
            yield True
        return

    key = f"voxdesk:tick:{name}"
    token = uuid.uuid4().hex
    try:
        acquired = await redis_client.set(key, token, nx=True, px=max(1, ttl_seconds) * 1000)
    except Exception as exc:  # noqa: BLE001 - an unreachable Redis must not run the pass
        log.warning("tick_lock.unavailable", tick=name, error=f"{type(exc).__name__}: {exc}"[:200])
        yield False
        return
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        try:
            await redis_client.eval(_RELEASE_SCRIPT, 1, key, token)
        except Exception as exc:  # noqa: BLE001 - the lease will expire on its own
            log.warning(
                "tick_lock.release_failed",
                tick=name,
                error=f"{type(exc).__name__}: {exc}"[:200],
            )


__all__ = ["DEFAULT_TTL_SECONDS", "tick_lock"]
