"""Per-provider circuit breaker.

Local state is the authority for this process. The key set is the three
supported providers, so caller input cannot grow it. When ``REDIS_URL`` is
set, open/half-open state is also written to the existing cache so another
worker can see an unhealthy provider. A cache miss or cache error does not
open the circuit: a cache blip is not a provider outage.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from app.agent.llm_factory import SUPPORTED_PROVIDERS
from app.core.config import settings

FAILURE_THRESHOLD = 5
RECOVERY_SECONDS = 30.0
PROBE_SUCCESSES = 1
_CACHE_TTL = 120

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


@dataclass
class _State:
    status: str = CLOSED
    failures: int = 0
    opened_at: float = 0.0
    probe_in_flight: bool = False

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "failures": self.failures,
            "opened_at": self.opened_at,
            "probe_in_flight": self.probe_in_flight,
        }


_LOCK = threading.Lock()
_STATES: dict[str, _State] = {provider: _State() for provider in SUPPORTED_PROVIDERS}


def reset() -> None:
    """Test helper. Production callers do not reset a peer's breaker."""
    with _LOCK:
        for provider in SUPPORTED_PROVIDERS:
            _STATES[provider] = _State()


def redis_configured() -> bool:
    return bool((settings.redis_url or "").strip())


def _key(provider: str) -> str:
    return f"ai:breaker:{provider}"


def snapshot(provider: str) -> dict:
    with _LOCK:
        state = _STATES.get(provider)
        if state is None:
            return {"provider": provider, "status": OPEN, "known": False, "shared": False}
        return {
            "provider": provider,
            "status": state.status,
            "failures": state.failures,
            "known": True,
            "shared": redis_configured(),
        }


def _apply_remote(provider: str, remote: dict, moment: float) -> None:
    """Adopt a peer's open state. A missing or closed remote value changes nothing."""
    if not isinstance(remote, dict):
        return
    status = remote.get("status")
    if status not in {OPEN, HALF_OPEN}:
        return
    state = _STATES[provider]
    if state.status == CLOSED:
        state.status = status
        state.failures = int(remote.get("failures") or FAILURE_THRESHOLD)
        state.opened_at = float(remote.get("opened_at") or moment)
        state.probe_in_flight = False


def allow(provider: str, *, now: float | None = None) -> bool:
    """False when the provider is open. Unknown providers fail closed."""
    if provider not in _STATES:
        return False
    moment = time.monotonic() if now is None else now
    with _LOCK:
        state = _STATES[provider]
        if state.status == CLOSED:
            return True
        if state.status == OPEN and (moment - state.opened_at) >= RECOVERY_SECONDS:
            state.status = HALF_OPEN
            state.probe_in_flight = False
        if state.status == HALF_OPEN and not state.probe_in_flight:
            state.probe_in_flight = True
            return True
        return False


def record_success(provider: str) -> None:
    if provider not in _STATES:
        return
    with _LOCK:
        state = _STATES[provider]
        state.failures = 0
        state.probe_in_flight = False
        state.status = CLOSED


def record_failure(provider: str, *, now: float | None = None) -> str:
    if provider not in _STATES:
        return OPEN
    moment = time.monotonic() if now is None else now
    with _LOCK:
        state = _STATES[provider]
        state.failures += 1
        state.probe_in_flight = False
        if state.status == HALF_OPEN or state.failures >= FAILURE_THRESHOLD:
            state.status = OPEN
            state.opened_at = moment
        return state.status


async def _publish(provider: str) -> None:
    if not redis_configured() or provider not in _STATES:
        return
    from app.core.cache import get_cache

    with _LOCK:
        payload = _STATES[provider].as_dict()
    # Cache.set already degrades on a backend error. A failed write does not
    # change the local decision.
    await get_cache().set(_key(provider), payload, _CACHE_TTL)


async def _pull(provider: str, *, now: float | None = None) -> None:
    if not redis_configured() or provider not in _STATES:
        return
    from app.core.cache import get_cache

    remote = await get_cache().get(_key(provider))
    if remote is None:
        return
    moment = time.monotonic() if now is None else now
    with _LOCK:
        _apply_remote(provider, remote, moment)


async def allow_shared(provider: str, *, now: float | None = None) -> bool:
    """Local decision after adopting a peer's open state, when Redis is configured."""
    await _pull(provider, now=now)
    return allow(provider, now=now)


async def consult(provider: str, *, now: float | None = None) -> bool:
    """Runtime check. A cache miss is not an outage; local state still decides."""
    return await allow_shared(provider, now=now)


async def record_failure_shared(provider: str, *, now: float | None = None) -> str:
    status = record_failure(provider, now=now)
    await _publish(provider)
    return status


async def record_success_shared(provider: str) -> None:
    record_success(provider)
    await _publish(provider)
