"""Prompt 5: Server-side Origin Allowlist Policy & Validation Service.

Enforces strict origin validation for embeddable public keys and widget sessions:
- Exact origin matching (`scheme://host[:port]`)
- Rejects wildcards (`*`) in production
- Rejects `null`, `javascript:`, `data:`, `file:`, and malformed origins
- Localhost (`http://localhost`, `http://127.0.0.1`) allowed only in development/test or when explicitly listed in non-production
- Server-side authoritative (never trusts browser-only checks)
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.core.config import settings
from app.domain.public_site_models import PublicDomainError as AppError
from app.domain.public_widget_models import OriginValidationResult

LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})
FORBIDDEN_SCHEMES = frozenset({"javascript", "data", "file", "vbscript", "about", "blob"})


def normalize_origin(raw_origin: str | None, *, allow_wildcard_dev: bool = False) -> str:
    """Normalize an origin string to `scheme://host[:port]` or raise AppError(422)."""
    if raw_origin is None:
        raise AppError(
            status_code=422,
            code="invalid_origin",
            message="Origin cannot be empty.",
        )
    candidate = raw_origin.strip()
    if not candidate or candidate.lower() == "null":
        raise AppError(
            status_code=422,
            code="invalid_origin",
            message="Origin 'null' or empty string is not allowed.",
        )

    if candidate == "*":
        if settings.is_production or not allow_wildcard_dev:
            raise AppError(
                status_code=422,
                code="wildcard_origin_forbidden",
                message="Wildcard '*' origin is forbidden for public widget keys.",
            )
        return "*"

    if "*" in candidate:
        raise AppError(
            status_code=422,
            code="wildcard_origin_forbidden",
            message="Wildcard patterns in origins are not allowed; specify exact origins.",
        )

    parsed = urlparse(candidate)
    scheme = (parsed.scheme or "").lower()
    if scheme in FORBIDDEN_SCHEMES or scheme not in {"http", "https"}:
        raise AppError(
            status_code=422,
            code="invalid_origin_scheme",
            message=f"Origin scheme '{scheme or 'missing'}' is not allowed. Use https:// (or http://localhost in development).",
        )

    if not parsed.hostname:
        raise AppError(
            status_code=422,
            code="invalid_origin_host",
            message="Origin must include a valid hostname.",
        )

    if parsed.username or parsed.password:
        raise AppError(
            status_code=422,
            code="invalid_origin_credentials",
            message="Origin must not contain userinfo credentials.",
        )

    if (parsed.path and parsed.path not in {"", "/"}) or parsed.params or parsed.query or parsed.fragment:
        raise AppError(
            status_code=422,
            code="invalid_origin_format",
            message="Origin must only specify scheme, host, and optional port (no path, query, or fragment).",
        )

    hostname = parsed.hostname.lower()
    is_localhost = hostname in LOCALHOST_HOSTS

    if scheme == "http" and not is_localhost and settings.is_production:
        raise AppError(
            status_code=422,
            code="insecure_origin_scheme",
            message="Production origins must use https://.",
        )

    if is_localhost and settings.is_production:
        raise AppError(
            status_code=422,
            code="localhost_origin_forbidden_in_production",
            message="Localhost origins are not permitted in production.",
        )

    try:
        port = parsed.port
    except ValueError as exc:
        raise AppError(
            status_code=422,
            code="invalid_origin_port",
            message="Origin contains an invalid port.",
        ) from exc

    host_expr = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
    if port is None or (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        return f"{scheme}://{host_expr}"
    return f"{scheme}://{host_expr}:{port}"


def normalize_origin_list(raw_origins: list[str] | None) -> list[str]:
    """Validate and deduplicate a list of allowed origins."""
    if not raw_origins:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_origins:
        norm = normalize_origin(item, allow_wildcard_dev=False)
        if norm not in seen:
            seen.add(norm)
            normalized.append(norm)
    return normalized


def extract_request_origin(
    *,
    origin_header: str | None = None,
    referer_header: str | None = None,
    explicit_origin: str | None = None,
) -> str | None:
    """Extract the caller's origin from HTTP `Origin`, `Referer`, or explicit body/query parameter.

    Note: If `Origin` header is present on the HTTP request, it is authoritative and
    cannot be overridden by `explicit_origin`.
    """
    if origin_header and origin_header.strip():
        return origin_header.strip()
    if referer_header and referer_header.strip():
        parsed = urlparse(referer_header.strip())
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"
    if explicit_origin and explicit_origin.strip():
        return explicit_origin.strip()
    return None


def validate_origin_against_allowlist(
    *,
    request_origin: str | None,
    allowed_origins: list[str] | None,
) -> OriginValidationResult:
    """Server-side check that `request_origin` matches one of `allowed_origins`."""
    allowlist = [o for o in (allowed_origins or []) if isinstance(o, str) and o.strip()]
    if not allowlist:
        return OriginValidationResult(
            allowed=False,
            normalized_origin=None,
            matched_origin=None,
            reason="Public key has no allowed origins configured.",
            error_code="FORBIDDEN_ORIGIN",
        )

    if not request_origin or not request_origin.strip():
        return OriginValidationResult(
            allowed=False,
            normalized_origin=None,
            matched_origin=None,
            reason="Missing Origin header on public widget request.",
            error_code="FORBIDDEN_ORIGIN",
        )

    try:
        normalized_req = normalize_origin(request_origin, allow_wildcard_dev=False)
    except AppError as exc:
        return OriginValidationResult(
            allowed=False,
            normalized_origin=None,
            matched_origin=None,
            reason=exc.message,
            error_code="FORBIDDEN_ORIGIN",
        )

    normalized_allowlist: list[str] = []
    for item in allowlist:
        try:
            normalized_allowlist.append(normalize_origin(item, allow_wildcard_dev=not settings.is_production))
        except AppError:
            continue

    if normalized_req in normalized_allowlist:
        return OriginValidationResult(
            allowed=True,
            normalized_origin=normalized_req,
            matched_origin=normalized_req,
            reason="Origin matched allowlist.",
            error_code=None,
        )

    if not settings.is_production and "*" in normalized_allowlist:
        return OriginValidationResult(
            allowed=True,
            normalized_origin=normalized_req,
            matched_origin="*",
            reason="Origin allowed by non-production wildcard.",
            error_code=None,
        )

    return OriginValidationResult(
        allowed=False,
        normalized_origin=normalized_req,
        matched_origin=None,
        reason=f"Origin '{normalized_req}' is not in the public key's allowed_origins list.",
        error_code="FORBIDDEN_ORIGIN",
    )
