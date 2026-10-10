"""Rate limiting (fail-closed when enabled, zero-dependency when disabled).

Two backends behind one interface:

* Redis (fixed-window INCR + EXPIRE) when ``RATE_LIMIT_*`` and ``REDIS_URL`` are
  configured -- the correct choice for multi-worker deployments.
* In-process sliding window otherwise, so a single-process dev/staging box is
  still protected without adding infrastructure.

The middleware is OFF by default (``RATE_LIMIT_ENABLED=false``) so the test
suite and local development are unaffected; production deployments enable it in
the environment. When the limiter itself errors it fails CLOSED -- the request
is rejected with 429 rather than allowed through unprotected.

Login gets a separate, tighter budget so credential stuffing is throttled
before it ever reaches bcrypt.
"""
from __future__ import annotations

import hashlib
import inspect
import ipaddress
import time
from collections import defaultdict, deque
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import log

_RATE_LIMIT_BODY = {"error": {"code": "rate_limited", "message": "Too many requests"}}

# Paths never rate limited: liveness/readiness for the orchestrator, the
# metrics scrape endpoint for Prometheus.
_EXEMPT_PREFIXES = ("/health", "/metrics")


def client_ip(request: Request) -> str:
    """Return the ASGI peer address after trusted-proxy middleware processing.

    Do not parse ``X-Forwarded-For`` here: a direct caller can forge it. The
    production entrypoint's ``FORWARDED_ALLOW_IPS`` is the sole trust boundary
    that permits Uvicorn to replace ``request.client`` with a forwarded client.
    """
    peer = request.client.host if request.client else "unknown"
    try:
        return str(ipaddress.ip_address(peer))
    except ValueError:
        return peer[:128] if peer else "unknown"


def _bucket_for_path(path: str, method: str = "GET") -> str:
    """Select a bounded rate tier without embedding user-controlled path IDs."""
    normalized = path.lower()
    verb = method.upper()
    if "/webhook" in normalized or "/webhooks" in normalized or "/inbound/" in normalized:
        return "webhook"
    if normalized.startswith("/auth/password-reset/"):
        return "password_reset"
    if normalized.startswith("/auth/"):
        return "auth"
    if normalized.startswith(("/telephony/", "/channels/")) or "/media-stream" in normalized:
        return "telephony"
    if any(part in normalized for part in ("/upload", "/documents", "/file", "/import")) and verb in {"POST", "PUT", "PATCH"}:
        return "upload"
    if any(part in normalized for part in (
        "/api-keys", "/security", "/identity", "/sessions", "/members",
        "/roles", "/billing", "/organization", "/tenant-admin",
    )):
        return "admin"
    if any(part in normalized for part in (
        "/calls", "/campaign", "/workflow", "/agents/execute", "/api-tools", "/mcp",
    )):
        return "execution"
    if normalized.startswith(("/api/", "/api/v1/")):
        return "api"
    return "other"


def _limit_for_bucket(bucket: str) -> int:
    limits = {
        "auth": settings.rate_limit_login_per_minute,
        "password_reset": settings.rate_limit_password_reset_per_minute,
        "admin": settings.rate_limit_admin_per_minute,
        "telephony": settings.rate_limit_telephony_per_minute,
        "webhook": settings.rate_limit_webhook_per_minute,
        "upload": settings.rate_limit_upload_per_minute,
        "execution": settings.rate_limit_execution_per_minute,
        "api": settings.rate_limit_burst,
        "other": settings.rate_limit_burst,
    }
    return max(1, int(limits[bucket]))


def _make_key(request: Request) -> str:
    ip = client_ip(request)
    bucket = _bucket_for_path(request.url.path, request.method)
    digest = hashlib.sha256(ip.encode("utf-8", "replace")).hexdigest()
    return f"voxdesk:rate:v1:{bucket}:{digest}"


def authenticated_rate_limit_bucket(request: Request) -> tuple[str, int]:
    """Return the path tier and configured budget for principal-scoped checks."""
    bucket = _bucket_for_path(request.url.path, request.method)
    return bucket, _limit_for_bucket(bucket)


async def allow_authenticated_request(
    request: Request,
    *,
    tenant_id: str,
    principal_id: str,
) -> bool:
    """Apply a second distributed budget after authentication resolved identity."""
    if not settings.rate_limit_enabled:
        return True
    bucket, limit = authenticated_rate_limit_bucket(request)
    return await allow_identity_action(
        action=f"api:{bucket}",
        who=f"{tenant_id}:{principal_id}",
        limit=limit,
        window=60.0,
    )


class _SlidingWindow:
    """In-process limiter: allow N requests per `window` seconds per key."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window: float, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        queue = self._events[key]
        while queue and queue[0] <= now - window:
            queue.popleft()
        if len(queue) >= limit:
            return False
        queue.append(now)
        return True


class _RedisLimiter:
    """Redis fixed-window limiter. Lazily imports redis so the dependency is
    optional (in-process fallback is used when Redis is absent)."""

    def __init__(self, url: str, *, fail_closed_in_prod: bool = True) -> None:
        self._url = url
        self.fail_closed_in_prod = fail_closed_in_prod
        self._client = None

    def _get(self):
        if self._client is None:
            import redis.asyncio as redis

            self._client = redis.from_url(self._url, decode_responses=True)
        return self._client

    async def allow(self, key: str, limit: int, window: float) -> bool:
        try:
            client = self._get()
            # Increment and TTL assignment are one server-side transaction. The
            # former split INCR/EXPIRE sequence could strand an immortal counter
            # if a worker died between commands, permanently rate-limiting a key.
            script = (
                "local current = redis.call('INCR', KEYS[1]); "
                "if current == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]); end; "
                "return current"
            )
            current = await client.eval(script, 1, key, max(1, int(window)))
            return int(current) <= limit
        except Exception as exc:  # noqa: BLE001 - fail closed, never propagate
            # Fail closed: a broken limiter must not silently allow traffic.
            # The refusal itself is correct, but a Redis outage would otherwise
            # turn every request into an unexplained 429 — log the TYPE only
            # (a Redis exception can carry the URL, password included).
            log.warning(
                "rate_limit.backend_unavailable",
                error_type=type(exc).__name__,
                backend="redis",
            )
            return False


class RateLimiter:
    def __init__(self, redis_url: str, *, fail_closed_in_prod: bool = True) -> None:
        self.fail_closed_in_prod = fail_closed_in_prod
        self._backend = (
            _RedisLimiter(redis_url, fail_closed_in_prod=fail_closed_in_prod)
            if redis_url
            else _SlidingWindow()
        )

    async def allow(self, key: str, limit: int, window: float) -> bool:
        # The in-process backend is synchronous, the Redis one is async; both
        # implement the same allow() contract. Normalise on the result.
        result = self._backend.allow(key, limit, window)
        if inspect.isawaitable(result):
            return await result
        return result


_limiter: RateLimiter | None = None
_limiter_url: str | None = None


def _get_limiter() -> RateLimiter:
    global _limiter, _limiter_url
    if _limiter is None or _limiter_url != settings.redis_url:
        _limiter = RateLimiter(settings.redis_url)
        _limiter_url = settings.redis_url
    return _limiter


def _budget_for(request: Request) -> tuple[int, float]:
    bucket = _bucket_for_path(request.url.path, request.method)
    return _limit_for_bucket(bucket), 60.0


def add_rate_limit_middleware(app: FastAPI) -> None:
    if not settings.rate_limit_enabled:
        return

    @app.middleware("http")
    async def _rate_limit_middleware(request: Request, call_next):
        if request.url.path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        limit, window = _budget_for(request)
        allowed = await _get_limiter().allow(_make_key(request), limit, window)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content=_RATE_LIMIT_BODY,
                headers={"Retry-After": str(int(window))},
            )
        return await call_next(request)


# ------------------------------------------------------------ identity sinks ---
#
# The middleware above is deployment-wide and off by default. Identity has
# endpoints that must be throttled in *every* deployment -- a second-factor
# verification, a password-reset request, an invitation or email-verification
# send -- because each guess or each send costs something real. They therefore
# use their own limiter instance rather than the middleware: still the same
# `RateLimiter` contract (so Redis is used exactly when it is configured), but
# independent of `RATE_LIMIT_ENABLED`, and separately resettable in tests.

_identity_limiter: RateLimiter | None = None
_identity_limiter_url: str | None = None


def identity_limiter() -> RateLimiter:
    global _identity_limiter, _identity_limiter_url
    if _identity_limiter is None or _identity_limiter_url != settings.redis_url:
        _identity_limiter = RateLimiter(settings.redis_url)
        _identity_limiter_url = settings.redis_url
    return _identity_limiter


def reset_identity_limiter() -> None:
    """Drop the identity limiter's state (tests, and after a Redis failover)."""
    global _identity_limiter, _identity_limiter_url
    _identity_limiter = None
    _identity_limiter_url = None


async def allow_identity_action(
    *, action: str, who: str, limit: int, window: float = 60.0
) -> bool:
    """One call, one budget: `action` names the sink, `who` the subject.

    Returns ``False`` when the budget is spent. Callers respond 429 and never
    say whether the refusal was about the account or the address.
    """
    subject = hashlib.sha256((who or "unknown").strip().lower().encode("utf-8", "replace")).hexdigest()
    key = f"voxdesk:rate:v1:identity:{str(action)[:64]}:{subject}"
    return await identity_limiter().allow(key, limit, window)


def allow_for_test(key: str, limit: int, window: float) -> Callable[[], bool]:
    """Synchronous helper so tests can drive the in-process backend directly."""
    backend = _SlidingWindow()
    return lambda: backend.allow(key, limit, window)


async def rate_limit(key: str, limit: int, window: float = 60.0) -> bool:
    """Strict distributed limiter for control-plane writes; never local fallback.

    The connection is not authoritative state. Redis owns the counter and TTL.
    Missing Redis fails closed; callers expose NOT_CONFIGURED, not success.
    """
    if not settings.redis_url:
        raise RuntimeError("Redis rate limiting is not configured")
    backend = _RedisLimiter(settings.redis_url)
    try:
        return await backend.allow(key, limit, window)
    finally:
        if backend._client is not None:
            await backend._client.aclose()


async def enforce_tenant_rate_limit(
    tenant_id: object,
    action: str,
    limit: int,
    *,
    window: float = 60.0,
) -> None:
    """Shared tenant-scoped rate limiter (Redis when configured, sliding window fallback)."""
    from fastapi import HTTPException

    allowed = await allow_identity_action(
        action=f"tenant:{action}",
        who=str(tenant_id),
        limit=int(limit),
        window=window,
    )
    if not allowed:
        raise HTTPException(status_code=429, detail=f"rate limit {action} {limit}/min")
