"""Prompt 5: Public / Auth / Protected / Widget Boundary & Return-Path Security Service.

Enforces strict separation between:
1. PUBLIC WEBSITE (`PUBLIC`): marketing, catalog, docs, pricing, security, status, contact-sales
2. AUTHENTICATION (`AUTH`): `/login`, `/signup`, `/auth/*`
3. PROTECTED APP (`PROTECTED`): `/dashboard/*`, `/agents/*`, `/studio/*`, `/calls/*`, `/analytics/*`, `/settings/*`, `/billing/*`, and private `/api/*`
4. PUBLIC WIDGET (`PUBLIC_WIDGET`): `/api/v1/public/widget/*`

Also enforces open-redirect prevention on `next` / `return_path` parameters and prevents
public widget keys (`vdpk_*`) or widget session tokens (`vdws_*`) from authenticating
against private endpoints.
"""

from __future__ import annotations

from urllib.parse import quote, unquote, urlsplit

from app.domain.public_site_models import (
    PublicAuthBoundaryDecision,
    PublicDomainError as AppError,
    PublicRouteCategory,
)

PUBLIC_EXACT_ROUTES: frozenset[str] = frozenset(
    {
        "/",
        "/home",
        "/product",
        "/product/voice-agents",
        "/product/customer-service",
        "/product/answering-service",
        "/product/appointment-setter",
        "/product/telemarketing",
        "/product/outbound",
        "/product/inbound",
        "/product/analytics",
        "/product/voice-cloning",
        "/solutions",
        "/use-cases",
        "/industries",
        "/integrations",
        "/pricing",
        "/security",
        "/trust",
        "/compliance",
        "/developers",
        "/docs",
        "/status",
        "/resources",
        "/blog",
        "/about",
        "/careers",
        "/team",
        "/contact",
        "/contact-sales",
        "/book-demo",
        "/privacy",
        "/terms",
        "/dpa",
        "/sla",
        "/robots.txt",
        "/sitemap.xml",
        "/.well-known/security.txt",
        "/health",
        "/health/live",
        "/health/ready",
    }
)

PUBLIC_PREFIX_ROUTES: tuple[str, ...] = (
    "/use-cases/",
    "/solutions/",
    "/industries/",
    "/integrations/",
    "/docs/",
    "/blog/",
    "/api/v1/public/home",
    "/api/v1/public/use-cases",
    "/api/v1/public/site",
    "/api/v1/public/contact-sales",
    "/api/v1/public/analytics/summary",
    "/api/v1/public/voice-demo",
    "/api/v1/public/health",
)

AUTH_ROUTES: frozenset[str] = frozenset({"/login", "/signup"})
AUTH_PREFIX_ROUTES: tuple[str, ...] = ("/auth/",)

PUBLIC_WIDGET_PREFIX_ROUTES: tuple[str, ...] = ("/api/v1/public/widget",)

PROTECTED_EXACT_ROUTES: frozenset[str] = frozenset(
    {
        "/dashboard",
        "/agents",
        "/agent",
        "/studio",
        "/calls",
        "/analytics",
        "/settings",
        "/billing",
        "/workspace",
        "/phone-numbers",
        "/product/playground",
        "/product/simulations",
        "/product/qa-scorecards",
        "/product/conductor",
    }
)

PROTECTED_PREFIX_ROUTES: tuple[str, ...] = (
    "/dashboard/",
    "/agents/",
    "/studio/",
    "/calls/",
    "/analytics/",
    "/settings/",
    "/billing/",
    "/workspace/",
    "/phone-numbers/",
)

FORBIDDEN_OPEN_REDIRECT_SCHEMES: tuple[str, ...] = (
    "http:",
    "https:",
    "javascript:",
    "data:",
    "vbscript:",
    "file:",
    "ftp:",
    "mailto:",
)


def validate_safe_next_path(
    raw_next: str | None,
    *,
    default_path: str = "/dashboard",
    strict: bool = False,
) -> tuple[bool, str]:
    """Validate that `raw_next` is a safe local application path.

    Rejects open-redirect payloads (`http://`, `https://`, `//`, `/\\`, `javascript:`,
    control characters, or external hosts). When `strict=True`, raises `AppError(422)`
    on an unsafe `raw_next` value; otherwise returns `(False, default_path)`.
    """
    if raw_next is None or not str(raw_next).strip():
        return True, default_path

    candidate = str(raw_next).strip()
    decoded = unquote(candidate).strip()

    for check in (candidate, decoded):
        lower = check.lower()
        if any(ch in check for ch in ("\r", "\n", "\x00", "\t")):
            if strict:
                raise AppError(
                    status_code=422,
                    code="unsafe_redirect_path",
                    message="Redirect path contains forbidden control characters.",
                )
            return False, default_path

        if any(lower.startswith(scheme) for scheme in FORBIDDEN_OPEN_REDIRECT_SCHEMES):
            if strict:
                raise AppError(
                    status_code=422,
                    code="unsafe_redirect_path",
                    message="External or script redirect URLs are forbidden.",
                )
            return False, default_path

        if not check.startswith("/") or check.startswith("//") or check.startswith("/\\") or "\\" in check:
            if strict:
                raise AppError(
                    status_code=422,
                    code="unsafe_redirect_path",
                    message="Redirect path must be a relative local path starting with a single '/'.",
                )
            return False, default_path

        parsed = urlsplit(check)
        if parsed.scheme or parsed.netloc:
            if strict:
                raise AppError(
                    status_code=422,
                    code="unsafe_redirect_path",
                    message="Redirect path must not include a scheme or host.",
                )
            return False, default_path

        if "@" in parsed.path:
            if strict:
                raise AppError(
                    status_code=422,
                    code="unsafe_redirect_path",
                    message="Redirect path must not contain userinfo '@' segments.",
                )
            return False, default_path

    clean_path = urlsplit(candidate).path or "/"
    if clean_path in {"/login", "/signup"} or clean_path.startswith(("/login/", "/signup/")):
        return True, default_path

    return True, candidate


def classify_path(path: str) -> PublicRouteCategory:
    """Classify a request path into PUBLIC, AUTH, PUBLIC_WIDGET, or PROTECTED."""
    clean = (path or "/").split("?", 1)[0].rstrip("/") or "/"

    if clean.startswith(PUBLIC_WIDGET_PREFIX_ROUTES):
        return PublicWidgetRouteCategory
    if clean in AUTH_ROUTES or clean.startswith(AUTH_PREFIX_ROUTES):
        return PublicRouteCategory.AUTH
    if clean in PROTECTED_EXACT_ROUTES or clean.startswith(PROTECTED_PREFIX_ROUTES):
        return PublicRouteCategory.PROTECTED
    if clean in PUBLIC_EXACT_ROUTES or clean.startswith(PUBLIC_PREFIX_ROUTES):
        return PublicRouteCategory.PUBLIC
    if clean.startswith("/api/"):
        if clean.startswith(("/api/public/webhooks/", "/api/crm/webhooks/", "/api/calendar/webhooks/")):
            return PublicRouteCategory.PUBLIC
        return PublicRouteCategory.PROTECTED
    return PublicRouteCategory.PUBLIC


PublicWidgetRouteCategory = PublicRouteCategory.PUBLIC_WIDGET


def evaluate_route_boundary(
    *,
    path: str,
    is_authenticated: bool = False,
    next_param: str | None = None,
) -> PublicAuthBoundaryDecision:
    """Evaluate route access, redirect target, and cache policy for a path."""
    category = classify_path(path)
    is_safe, sanitized_next = validate_safe_next_path(next_param, default_path="/dashboard", strict=False)

    if category == PublicRouteCategory.PROTECTED:
        if not is_authenticated:
            encoded_target = quote(path if path.startswith("/") else "/dashboard", safe="")
            return PublicAuthBoundaryDecision(
                path=path,
                classification=category,
                requires_auth=True,
                is_safe_next_path=is_safe,
                sanitized_next_path=sanitized_next,
                redirect_to=f"/login?next={encoded_target}",
                cache_control="no-store, private",
                reason="unauthenticated_protected_route",
            )
        return PublicAuthBoundaryDecision(
            path=path,
            classification=category,
            requires_auth=True,
            is_safe_next_path=is_safe,
            sanitized_next_path=sanitized_next,
            redirect_to=None,
            cache_control="no-store, private",
            reason="authenticated_protected_route",
        )

    if category == PublicRouteCategory.AUTH:
        if is_authenticated:
            return PublicAuthBoundaryDecision(
                path=path,
                classification=category,
                requires_auth=False,
                is_safe_next_path=is_safe,
                sanitized_next_path=sanitized_next,
                redirect_to=sanitized_next,
                cache_control="no-store, private",
                reason="authenticated_user_on_auth_page",
            )
        return PublicAuthBoundaryDecision(
            path=path,
            classification=category,
            requires_auth=False,
            is_safe_next_path=is_safe,
            sanitized_next_path=sanitized_next,
            redirect_to=None,
            cache_control="no-store, private",
            reason="public_auth_page",
        )

    if category == PublicRouteCategory.PUBLIC_WIDGET:
        return PublicAuthBoundaryDecision(
            path=path,
            classification=category,
            requires_auth=False,
            is_safe_next_path=is_safe,
            sanitized_next_path=sanitized_next,
            redirect_to=None,
            cache_control="no-store, private",
            reason="public_widget_endpoint",
        )

    return PublicAuthBoundaryDecision(
        path=path,
        classification=PublicRouteCategory.PUBLIC,
        requires_auth=False,
        is_safe_next_path=is_safe,
        sanitized_next_path=sanitized_next,
        redirect_to=None,
        cache_control="public, max-age=60",
        reason="public_site_route",
    )


def reject_public_credential_on_private_route(
    *,
    path: str,
    authorization_header: str | None = None,
    public_key_header: str | None = None,
) -> None:
    """Fail closed if a public widget key (`vdpk_`) or widget session token (`vdws_`)
    is presented on any non-widget private API route.
    """
    category = classify_path(path)
    if category == PublicRouteCategory.PUBLIC_WIDGET:
        return

    token = ""
    if authorization_header and authorization_header.lower().startswith("bearer "):
        token = authorization_header[7:].strip()
    elif authorization_header:
        token = authorization_header.strip()

    if token.startswith(("vdpk_", "vdws_")) or (
        public_key_header and category == PublicRouteCategory.PROTECTED
    ):
        raise AppError(
            status_code=403,
            code="public_credential_forbidden_on_private_api",
            message=(
                "Public widget keys and widget session tokens are restricted to "
                "/api/v1/public/widget/* and cannot access private or admin APIs."
            ),
        )
