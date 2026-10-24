"""Typed error hierarchy and secret-redaction helpers for Integrations v2."""

from __future__ import annotations

import re
from typing import Iterable

_BEARER_RE = re.compile(
    r"(?i)\b(bearer|basic|token|zoho-oauthtoken)\s+[A-Za-z0-9._~+/=-]{6,}"
)
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:api_token|access_token|refresh_token|client_secret|code|signature|sig|token)=)[^&\s\"']+"
)
_JSON_SECRET_RE = re.compile(
    r'(?i)("(?:access_token|refresh_token|client_secret|api_key|api_token|signing_secret|webhook_secret|code_verifier)"\s*:\s*")[^"]*(")'
)


def redact_secrets(text: str, extra_secrets: Iterable[str] = (), max_len: int = 500) -> str:
    """Scrub bearer tokens, query-string credentials, and known secret literals."""
    if not text:
        return ""
    cleaned = _BEARER_RE.sub(r"\1 [REDACTED]", str(text))
    cleaned = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", cleaned)
    cleaned = _JSON_SECRET_RE.sub(r"\1[REDACTED]\2", cleaned)
    for item in extra_secrets:
        if item and len(item) >= 6:
            cleaned = cleaned.replace(item, "[REDACTED]")
    if len(cleaned) > max_len:
        return cleaned[: max_len - 3] + "..."
    return cleaned


class IntegrationV2Error(Exception):
    """Base error for Integrations v2 operations."""

    code: str = "integration_v2_error"
    http_status: int = 400

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        body_excerpt: str = "",
    ) -> None:
        safe = redact_secrets(message)
        super().__init__(safe)
        self.safe_message = safe
        self.provider = provider
        self.upstream_status = status_code
        self.retry_after_seconds = retry_after_seconds
        self.body_excerpt = redact_secrets(body_excerpt, max_len=240)


class ProviderNotConfigured(IntegrationV2Error):
    """Provider credentials or tenant connection are not configured."""

    code = "provider_not_configured"
    http_status = 501


class SSRFBlocked(IntegrationV2Error):
    """Outbound URL failed SSRF or vendor domain allowlist validation."""

    code = "ssrf_blocked"
    http_status = 422


class AuthExpired(IntegrationV2Error):
    """OAuth token or API key was rejected by the upstream vendor (401/403/invalid_grant)."""

    code = "auth_expired"
    http_status = 401


class RateLimited(IntegrationV2Error):
    """Upstream vendor returned HTTP 429 or exhausted tenant rate-limit budget."""

    code = "rate_limited"
    http_status = 429

    def __init__(
        self,
        message: str = "Provider rate limit exceeded",
        *,
        provider: str = "",
        status_code: int | None = 429,
        retry_after: float | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        resolved = retry_after if retry_after is not None else retry_after_seconds
        super().__init__(
            message,
            provider=provider,
            status_code=status_code,
            retry_after_seconds=resolved,
        )

    @property
    def retry_after(self) -> float | None:
        return self.retry_after_seconds


class ProviderRejected(IntegrationV2Error):
    """Upstream vendor rejected the request payload or returned a 4xx/5xx error."""

    code = "provider_rejected"
    http_status = 502

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        status_code: int | None = None,
        body_excerpt: str = "",
    ) -> None:
        super().__init__(
            message,
            provider=provider,
            status_code=status_code,
            body_excerpt=body_excerpt,
        )


class CapabilityUnsupported(IntegrationV2Error):
    """Requested capability is not supported by this provider."""

    code = "capability_unsupported"
    http_status = 501


class ValidationError(IntegrationV2Error):
    """Caller supplied invalid input or exceeded a vendor batch limit."""

    code = "validation_error"
    http_status = 422


class UpstreamTransientError(ProviderRejected):
    """Transient 5xx or network failure eligible for backoff retry."""

    code = "upstream_transient"
    http_status = 502


class WebhookSignatureError(IntegrationV2Error):
    """Inbound webhook failed cryptographic HMAC or timestamp replay validation."""

    code = "webhook_signature_invalid"
    http_status = 401


class CircuitOpenError(IntegrationV2Error):
    """Per-connection circuit breaker is open due to consecutive upstream failures."""

    code = "circuit_open"
    http_status = 503
