"""Hardened outbound HTTP client with SSRF + vendor-domain allowlist, token-bucket rate limiter, Retry-After backoff, circuit breaker, and secret scrubbing."""

from __future__ import annotations

import asyncio
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.logging import log
from app.core.ssrf import (
    OutboundUrlError,
    validate_outbound_url,
    validate_resolved_outbound_url,
)
from app.integrations_v2.core.errors import (
    AuthExpired,
    CircuitOpenError,
    ProviderRejected,
    RateLimited,
    SSRFBlocked,
    redact_secrets,
)


def parse_retry_after(
    header_value: str | None,
    *,
    default_seconds: float = 1.0,
    max_seconds: float = 60.0,
) -> float:
    """Parse HTTP ``Retry-After`` (delta-seconds or HTTP-date) into bounded seconds."""
    if not header_value:
        return default_seconds
    raw = header_value.strip()
    if not raw:
        return default_seconds
    try:
        secs = float(raw)
        return max(0.05, min(secs, max_seconds))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.05, min(delta, max_seconds))
    except Exception:
        return default_seconds


@dataclass
class TokenBucket:
    """Async token-bucket limiter per ``(tenant_id, provider)``."""

    rate_per_second: float = 10.0
    capacity: float = 20.0
    tokens: float = field(init=False)
    updated_at: float = field(init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self.tokens = self.capacity
        self.updated_at = time.monotonic()

    async def acquire(self, cost: float = 1.0) -> float:
        """Consume ``cost`` tokens, returning any wait time required (0 if immediate)."""
        async with self._lock:
            now = time.monotonic()
            elapsed = max(0.0, now - self.updated_at)
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_second)
            self.updated_at = now
            if self.tokens >= cost:
                self.tokens -= cost
                return 0.0
            deficit = cost - self.tokens
            wait_s = deficit / max(self.rate_per_second, 0.1)
            self.tokens = 0.0
            self.updated_at = now + wait_s
        await asyncio.sleep(min(wait_s, 2.0))
        return wait_s


@dataclass
class CircuitBreaker:
    """Per-connection consecutive-failure circuit breaker."""

    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0
    consecutive_failures: int = 0
    opened_until: float = 0.0

    def is_open(self) -> bool:
        if self.consecutive_failures < self.failure_threshold:
            return False
        if time.monotonic() >= self.opened_until:
            return False
        return True

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_until = 0.0

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold:
            self.opened_until = time.monotonic() + self.recovery_timeout_seconds


_BUCKETS: dict[tuple[str, str], TokenBucket] = {}
_BREAKERS: dict[str, CircuitBreaker] = {}

PROVIDER_DEFAULT_RATES: dict[str, tuple[float, float]] = {
    "zendesk": (6.0, 15.0),
    "servicenow": (8.0, 16.0),
    "freshdesk": (5.0, 12.0),
    "zoho_desk": (5.0, 12.0),
    "zoho_crm": (6.0, 15.0),
    "freshsales": (5.0, 12.0),
    "dynamics365": (10.0, 25.0),
    "pipedrive": (8.0, 16.0),
    "teams": (8.0, 16.0),
    "zoom": (6.0, 12.0),
    "slack": (5.0, 10.0),
}


def get_token_bucket(tenant_id: str, provider: str) -> TokenBucket:
    key = (tenant_id, provider)
    bucket = _BUCKETS.get(key)
    if bucket is None:
        rate, cap = PROVIDER_DEFAULT_RATES.get(provider, (8.0, 16.0))
        bucket = TokenBucket(rate_per_second=rate, capacity=cap)
        _BUCKETS[key] = bucket
    return bucket


def get_circuit_breaker(connection_id: str) -> CircuitBreaker:
    breaker = _BREAKERS.get(connection_id)
    if breaker is None:
        breaker = CircuitBreaker()
        _BREAKERS[connection_id] = breaker
    return breaker


def reset_rate_limit_state() -> None:
    _BUCKETS.clear()
    _BREAKERS.clear()


VENDOR_HOST_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "zendesk": (re.compile(r"^[a-z0-9][a-z0-9-]{0,62}\.zendesk\.com$"),),
    "servicenow": (re.compile(r"^[a-z0-9][a-z0-9.-]{0,100}\.service-now\.com$"),),
    "freshdesk": (re.compile(r"^[a-z0-9][a-z0-9-]{0,62}\.freshdesk\.com$"),),
    "zoho_desk": (
        re.compile(
            r"^(?:desk|accounts)\.(?:zoho\.(?:com|eu|in|com\.au|jp)|zohocloud\.ca)$"
        ),
    ),
    "zoho_crm": (
        re.compile(
            r"^(?:www\.zohoapis\.(?:com|eu|in|com\.au|jp|ca)|accounts\.(?:zoho\.(?:com|eu|in|com\.au|jp)|zohocloud\.ca))$"
        ),
    ),
    "freshsales": (
        re.compile(r"^[a-z0-9][a-z0-9-]{0,62}\.(?:myfreshworks\.com|freshsales\.io)$"),
    ),
    "dynamics365": (
        re.compile(
            r"^(?:[a-z0-9][a-z0-9-]{0,62}\.crm[0-9]*\.(?:dynamics\.com|microsoftdynamics\.us|dynamics\.cn)|login\.microsoftonline\.(?:com|us))$"
        ),
    ),
    "pipedrive": (
        re.compile(r"^(?:api\.pipedrive\.com|oauth\.pipedrive\.com|[a-z0-9-]+\.pipedrive\.com)$"),
    ),
    "teams": (
        re.compile(
            r"^(?:graph\.microsoft\.com|login\.microsoftonline\.com|smba\.trafficmanager\.net|[a-z0-9.-]+\.botframework\.com)$"
        ),
    ),
    "zoom": (re.compile(r"^(?:api\.zoom\.us|zoom\.us)$"),),
    "slack": (re.compile(r"^(?:slack\.com|[a-z0-9-]+\.slack\.com)$"),),
}


def validate_vendor_url(url: str, *, provider: str) -> str:
    """Validate URL via ``app.core.ssrf.validate_outbound_url(require_https=True)`` + vendor host allowlist."""
    try:
        validate_outbound_url(url, require_https=True)
    except OutboundUrlError as exc:
        raise SSRFBlocked(str(exc), provider=provider) from exc

    parsed = urlsplit(url.strip())
    host = (parsed.hostname or "").rstrip(".").lower()
    patterns = VENDOR_HOST_PATTERNS.get(provider)
    if not patterns:
        raise SSRFBlocked(f"Unknown integration provider {provider!r}", provider=provider)
    if not any(pat.fullmatch(host) for pat in patterns):
        raise SSRFBlocked(
            f"Host {host!r} is not on the allowlist for provider {provider!r}",
            provider=provider,
        )
    return url.strip()


class Int2HttpClient:
    """Outbound HTTP wrapper enforcing SSRF checks, no-redirect, rate limiting, retries, and circuit breaking."""

    def __init__(
        self,
        *,
        timeout_s: float = 10.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep_fn=asyncio.sleep,
        resolve_dns: bool | None = None,
    ) -> None:
        self.timeout_s = timeout_s
        self.max_retries = max(0, max_retries)
        self._transport = transport
        self._sleep = sleep_fn
        self._resolve_dns = (transport is None) if resolve_dns is None else resolve_dns

    async def _observe_rate_headers(self, resp: httpx.Response) -> None:
        rem = resp.headers.get("X-RateLimit-Remaining") or resp.headers.get("x-ratelimit-remaining")
        if rem is not None and str(rem).strip() == "0":
            reset_hdr = resp.headers.get("X-RateLimit-Reset") or resp.headers.get("Retry-After")
            delay = parse_retry_after(reset_hdr, default_seconds=0.05)
            if 0 < delay <= 2.0:
                await self._sleep(delay)

    async def request(
        self,
        method: str,
        url: str,
        *,
        provider: str,
        tenant_id: str = "system",
        connection_id: str = "",
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        data: dict[str, Any] | bytes | str | None = None,
        auth: tuple[str, str] | None = None,
        idempotency_key: str | None = None,
        expected_statuses: tuple[int, ...] = (200, 201, 202, 204),
    ) -> httpx.Response:
        clean_url = validate_vendor_url(url, provider=provider)
        if self._resolve_dns:
            try:
                await validate_resolved_outbound_url(clean_url, require_https=True)
            except OutboundUrlError as exc:
                raise SSRFBlocked(str(exc), provider=provider) from exc

        breaker = get_circuit_breaker(connection_id) if connection_id else None
        if breaker is not None and breaker.is_open():
            raise CircuitOpenError(
                f"Circuit breaker is open for {provider} connection {connection_id}",
                provider=provider,
            )

        bucket = get_token_bucket(tenant_id, provider)
        await bucket.acquire(1.0)

        req_headers = {"Accept": "application/json", "User-Agent": "VoxDesk-IntegrationsV2/1.0"}
        if headers:
            req_headers.update(headers)
        if idempotency_key:
            req_headers.setdefault("Idempotency-Key", idempotency_key)

        attempt = 0
        last_exc: Exception | None = None
        parsed_target = urlsplit(clean_url)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_s),
            follow_redirects=False,
            transport=self._transport,
        ) as client:
            while attempt <= self.max_retries:
                try:
                    resp = await client.request(
                        method.upper(),
                        clean_url,
                        headers=req_headers,
                        params=params,
                        json=json_body,
                        data=data,
                        auth=auth,
                    )
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    last_exc = exc
                    if attempt >= self.max_retries:
                        if breaker is not None:
                            breaker.record_failure()
                        raise ProviderRejected(
                            f"Network error contacting {provider}: {redact_secrets(str(exc))}",
                            provider=provider,
                        ) from exc
                    backoff = min(8.0, (0.25 * (2**attempt)) + random.uniform(0.0, 0.1))
                    await self._sleep(backoff)
                    attempt += 1
                    continue

                status = resp.status_code
                log.debug(
                    "int2.http.exchange",
                    provider=provider,
                    method=method.upper(),
                    host=parsed_target.hostname,
                    path=parsed_target.path,
                    status=status,
                )

                if 300 <= status < 400:
                    raise SSRFBlocked(
                        f"Upstream {provider} returned redirect HTTP {status}; redirects are forbidden",
                        provider=provider,
                        status_code=status,
                    )

                if status in expected_statuses:
                    if breaker is not None:
                        breaker.record_success()
                    await self._observe_rate_headers(resp)
                    return resp

                if status in (401, 403):
                    body_preview = redact_secrets(resp.text, max_len=240)
                    raise AuthExpired(
                        f"{provider} rejected credentials (HTTP {status}): {body_preview}",
                        provider=provider,
                        status_code=status,
                    )

                if status == 429:
                    retry_after = parse_retry_after(
                        resp.headers.get("Retry-After"), default_seconds=0.5 * (2**attempt)
                    )
                    if attempt < self.max_retries:
                        await self._sleep(min(retry_after, 5.0))
                        attempt += 1
                        continue
                    raise RateLimited(
                        f"{provider} rate limit exceeded (HTTP 429)",
                        provider=provider,
                        status_code=429,
                        retry_after=retry_after,
                    )

                if status >= 500:
                    if attempt < self.max_retries:
                        backoff = min(8.0, (0.25 * (2**attempt)) + random.uniform(0.0, 0.1))
                        await self._sleep(backoff)
                        attempt += 1
                        continue
                    if breaker is not None:
                        breaker.record_failure()
                    excerpt = redact_secrets(resp.text, max_len=240)
                    raise ProviderRejected(
                        f"{provider} server error (HTTP {status}): {excerpt}",
                        provider=provider,
                        status_code=status,
                        body_excerpt=excerpt,
                    )

                body_preview = redact_secrets(resp.text, max_len=300)
                log.warning(
                    "int2.http.rejected",
                    provider=provider,
                    status=status,
                    url_host=parsed_target.hostname,
                )
                raise ProviderRejected(
                    f"{provider} rejected request (HTTP {status}): {body_preview}",
                    provider=provider,
                    status_code=status,
                    body_excerpt=body_preview,
                )

        raise ProviderRejected(
            f"Exhausted retries contacting {provider}: {redact_secrets(str(last_exc))}",
            provider=provider,
        )
