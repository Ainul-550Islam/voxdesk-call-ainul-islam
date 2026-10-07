"""CSRF checks for endpoints authenticated by ambient browser cookies.

Bearer-token API calls, machine credentials and provider webhooks do not use
this policy. The only current cookie credential is the HttpOnly refresh cookie;
its mutation endpoints must reject cross-site browser requests.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from starlette.requests import Request

from app.core.config import settings

REFRESH_COOKIE = "voxdesk_refresh"
COOKIE_MUTATION_PATHS = frozenset({
    "/auth/refresh",
    "/auth/logout",
    "/auth/logout-all",
})


def canonical_origin(value: str | None) -> str | None:
    """Normalize a serialized browser origin; reject opaque/malformed origins."""
    if not value or value == "null":
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password or parsed.path not in {"", "/"}:
            return None
        if parsed.query or parsed.fragment:
            return None
        scheme = parsed.scheme.lower()
        host = parsed.hostname.lower()
        port = parsed.port
        if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
            port = None
        rendered_host = f"[{host}]" if ":" in host else host
        return f"{scheme}://{rendered_host}{f':{port}' if port else ''}"
    except (ValueError, TypeError):
        return None


def csrf_origin_rejected(request: Request) -> bool:
    """Whether a cookie-authenticated mutation has an untrusted origin.

    Requests with missing browser metadata remain compatible with non-browser
    clients; SameSite=Lax cookies are not sent on cross-site POSTs. When the
    browser supplies Origin or Fetch Metadata, the origin is checked against
    the same exact allowlist used by credentialed CORS, and cross-site requests
    are unconditionally rejected.
    """
    if (
        request.method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}
        or request.url.path not in COOKIE_MUTATION_PATHS
        or not request.cookies.get(REFRESH_COOKIE)
    ):
        return False
    fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
    if fetch_site == "cross-site":
        return True
    supplied = request.headers.get("origin")
    if supplied is None:
        return fetch_site not in {"", "same-origin", "same-site", "none"}
    canonical = canonical_origin(supplied)
    allowed = {canonical_origin(item) for item in settings.cors_origin_list}
    # The canonical same-origin deployment is trusted even when the CORS
    # allowlist only contains separate frontend origins. Never trust Host.
    allowed.add(canonical_origin(settings.public_base_url))
    return canonical is None or canonical not in allowed
