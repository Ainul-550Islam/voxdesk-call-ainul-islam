"""Prompt 5: Public Website, Public Catalog, Contact Sales, Signup, and SEO Service.

Strictly serves public-safe marketing, catalog, pricing, and status information
without exposing any private tenant data, agents, calls, or secrets.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import password as pw
from app.auth import service as auth_service
from app.billing.plans import SEED_PLANS, list_plans
from app.core.config import settings
from app.domain.public_site_models import PublicDomainError as AppError
from app.db.models import (
    AuditAction,
    PublicContactSalesRequest,
    Tenant,
    User,
    UserRole,
)
from app.domain.public_site_models import (
    PublicContactSalesCreate,
    PublicContactSalesRead,
    PublicNavItem,
    PublicPricingTier,
    PublicSEOMeta,
    PublicSignupRequest,
    PublicSiteManifest,
    PublicSiteStatusSummary,
    PublicStatusComponent,
)
from app.services.public_auth_boundary_service import (
    PROTECTED_PREFIX_ROUTES,
    PUBLIC_EXACT_ROUTES,
    validate_safe_next_path,
)
from app.services.public_use_case_service import get_use_cases


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


PUBLIC_NAVIGATION: list[PublicNavItem] = [
    PublicNavItem(id="home", label="Home", href="/", category="public", requires_auth=False),
    PublicNavItem(id="product", label="Product", href="/product", category="public", requires_auth=False),
    PublicNavItem(id="use_cases", label="Use Cases", href="/use-cases", category="public", requires_auth=False),
    PublicNavItem(id="pricing", label="Pricing", href="/pricing", category="public", requires_auth=False),
    PublicNavItem(id="security", label="Security", href="/security", category="public", requires_auth=False),
    PublicNavItem(id="docs", label="Docs", href="/docs", category="public", requires_auth=False),
    PublicNavItem(id="status", label="Status", href="/status", category="public", requires_auth=False),
    PublicNavItem(id="contact_sales", label="Contact Sales", href="/contact-sales", category="public", requires_auth=False),
    PublicNavItem(id="login", label="Sign In", href="/login", category="auth", requires_auth=False),
    PublicNavItem(id="signup", label="Start Building", href="/signup", category="auth", requires_auth=False),
]

def _public_capabilities(app: Any | None) -> list[dict[str, Any]]:
    """Expose route-backed capability evidence without claiming E2E readiness."""
    if app is None:
        return []
    from app.services import parity_service

    inventory = parity_service.capability_inventory(app)
    return [
        {
            "id": item.key,
            "title": item.label,
            "description": item.summary,
            "status": item.status,
            "evidence_routes": [
                {"method": route.method, "path": route.path}
                for route in item.evidence_routes
            ],
        }
        for item in inventory.capabilities
    ]


def _public_security_highlights(app: Any | None) -> list[dict[str, Any]]:
    """Describe security scope as partial implementation evidence, never certification."""
    if app is None:
        operations: list[tuple[str, str, str]] = []
    else:
        from app.services import parity_service

        operations = parity_service._iter_api_operations(app)
    definitions = (
        (
            "authentication",
            "Authentication and authorization",
            "Protected routes and permission checks are implemented in the application. This summary is not a route-by-route authorization test or an identity-provider configuration check.",
            ("/auth", "/sessions", "/api-keys", "/identity/"),
        ),
        (
            "tenant_scope",
            "Tenant and environment scope",
            "Tenant and environment identifiers are used by supported APIs. This public inventory does not prove every database query, cache key, worker, or module is isolated.",
            ("/organizations", "/environments", "/tenant"),
        ),
        (
            "credential_handling",
            "Provider credentials and secrets",
            "Credential values are not published by this endpoint. A stored credential does not prove provider connectivity, KMS-backed encryption, or a particular compliance certification.",
            ("/integrations", "/provider", "/secrets", "/api-keys"),
        ),
        (
            "audit_scope",
            "Audit and review controls",
            "Audit and review routes are present where listed. This endpoint does not assert an immutable or tamper-evident audit ledger, nor that every mutation is recorded.",
            ("/audit", "/conductor/", "/publish"),
        ),
    )
    result: list[dict[str, Any]] = []
    for identifier, title, description, fragments in definitions:
        paths = sorted({path for _, path, _ in operations if any(fragment in path for fragment in fragments)})
        result.append(
            {
                "id": identifier,
                "title": title,
                "description": description,
                "status": "PARTIAL" if paths else "MISSING",
                "evidence_routes": paths[:8],
                "certification": False,
            }
        )
    return result


async def get_public_pricing_tiers(db: AsyncSession) -> list[PublicPricingTier]:
    """Return public plan terms from the billing catalogue or its one source seed."""
    plans = await list_plans(db, active_only=True)
    catalogue_source = "DATABASE"
    if not plans:
        # The seed catalogue is the same source used by billing bootstrap. Do not
        # maintain a second set of marketing prices or fabricate values on DB errors.
        plans = list(SEED_PLANS)
        catalogue_source = "SEED_DEFAULT"

    tiers: list[PublicPricingTier] = []
    for plan in plans:
        code = str(getattr(plan, "code", "")).strip().lower()
        if not code:
            continue
        entitlements = getattr(plan, "feature_entitlements", {}) or {}
        if not isinstance(entitlements, dict):
            entitlements = {}
        included_minutes = int(getattr(plan, "included_voice_minutes", 0) or 0)
        included_sms_segments = int(getattr(plan, "included_sms_segments", 0) or 0)
        included_llm_tokens = int(getattr(plan, "included_llm_tokens", 0) or 0)
        included_tts_characters = int(getattr(plan, "included_tts_characters", 0) or 0)
        raw_concurrency = entitlements.get("concurrent_calls")
        try:
            max_concurrency = int(raw_concurrency) if raw_concurrency is not None else None
        except (TypeError, ValueError):
            max_concurrency = None
        currency = str(getattr(plan, "currency", "usd") or "usd").lower()
        features = [f"{included_minutes:,} included voice minutes per billing period"]
        if max_concurrency is not None:
            features.append(
                "Unlimited concurrent calls"
                if max_concurrency < 0
                else f"Up to {max_concurrency} concurrent calls"
            )
        if included_sms_segments:
            features.append(f"{included_sms_segments:,} included SMS segments per billing period")
        if included_llm_tokens:
            features.append(f"{included_llm_tokens:,} included LLM tokens per billing period")
        if included_tts_characters:
            features.append(f"{included_tts_characters:,} included TTS characters per billing period")
        if bool(entitlements.get("outbound_campaigns")):
            features.append("Outbound campaign entitlement")
        if bool(entitlements.get("api_access")):
            features.append("API access entitlement")

        is_enterprise = code == "enterprise"
        is_trial = bool(getattr(plan, "trial_days", 0))
        tiers.append(
            PublicPricingTier(
                id=f"plan_{code}",
                code=code,
                name=str(getattr(plan, "name", code.title())),
                catalogue_source=catalogue_source,
                description=str(getattr(plan, "description", "") or ""),
                monthly_price_cents=int(getattr(plan, "monthly_price_cents", 0) or 0),
                annual_price_cents=(
                    int(plan.annual_price_cents)
                    if getattr(plan, "annual_price_cents", None) is not None
                    else None
                ),
                currency=currency,
                included_minutes=included_minutes,
                included_sms_segments=included_sms_segments,
                included_llm_tokens=included_llm_tokens,
                included_tts_characters=included_tts_characters,
                max_concurrency=max_concurrency,
                overage_enabled=bool(getattr(plan, "overage_enabled", False)),
                trial_days=int(getattr(plan, "trial_days", 0) or 0),
                features=features,
                cta_label=("Contact Sales" if is_enterprise else "Start Free Trial" if is_trial else "Start Building"),
                cta_href=("/contact-sales" if is_enterprise else "/signup"),
                is_enterprise=is_enterprise,
            )
        )
    return tiers


async def get_public_status_summary(
    db: AsyncSession,
    app: Any | None = None,
) -> PublicSiteStatusSummary:
    """Probe the database and report other public surfaces as unverified evidence."""
    now = _utcnow()
    try:
        await db.execute(select(1))
        db_ok = True
    except Exception:
        db_ok = False

    if app is None:
        operations: list[tuple[str, str, str]] = []
    else:
        from app.services import parity_service

        operations = parity_service._iter_api_operations(app)
    paths = sorted({path for _, path, _ in operations})
    widget_paths = [
        path for path in paths
        if "/public/widget/" in path or "/public-keys" in path
    ]

    livekit_url = getattr(settings, "livekit_url", None)
    signaling_url = getattr(settings, "webrtc_signaling_url", None)
    voice_endpoint_configured = bool(livekit_url or signaling_url)
    voice_status = "not_verified" if voice_endpoint_configured else "not_configured"
    voice_description = (
        "A signaling URL is present, but this endpoint does not authenticate with or health-check the media provider."
        if voice_endpoint_configured
        else "No WebRTC or LiveKit signaling URL is configured in this environment."
    )

    components = [
        PublicStatusComponent(
            id="api_gateway",
            name="Public API process",
            status="operational",
            description="This status endpoint returned successfully. Other routes, dependencies, and tenant permissions are not probed here.",
            updated_at=now,
        ),
        PublicStatusComponent(
            id="database",
            name="Database connectivity",
            status="operational" if db_ok else "degraded",
            description=(
                "A SELECT 1 connectivity check succeeded; feature-specific queries and transactions are not included."
                if db_ok
                else "The database connectivity check failed."
            ),
            updated_at=now,
        ),
        PublicStatusComponent(
            id="public_widget_chat",
            name="Public widget API surface",
            status="partial" if widget_paths else "not_configured",
            description=(
                f"{len(widget_paths)} matching public widget/key route path(s) are registered. Route presence is not a chat-session or provider health check."
                if widget_paths
                else "No matching public widget/key route path was found in this application."
            ),
            updated_at=now,
        ),
        PublicStatusComponent(
            id="public_widget_voice",
            name="WebRTC / LiveKit signaling configuration",
            status=voice_status,
            description=voice_description,
            updated_at=now,
        ),
    ]

    overall = "degraded" if not db_ok else "partial"
    message = (
        "Database connectivity failed. External providers and the rest of the application were not health-checked."
        if not db_ok
        else "The database connectivity check succeeded; provider, cache, worker, and end-to-end readiness were not verified."
    )
    return PublicSiteStatusSummary(
        overall_status=overall,
        components=components,
        checked_at=now,
        message=message,
    )


async def get_public_site_manifest(
    db: AsyncSession,
    *,
    canonical_origin: str = "https://voxdesk.ai",
    app: Any | None = None,
) -> PublicSiteManifest:
    """Assemble public-safe navigation and evidence-scoped capability information."""
    use_cases_items, _ = get_use_cases(q=None, category=None, page=1, page_size=12)
    use_cases_summary = [
        {
            "id": str(getattr(item, "id", None) or item.slug),
            "slug": item.slug,
            "title": item.title,
            "category": item.category,
            "summary": "Illustrative workflow category; this entry is not a configured tenant agent or a verified production outcome.",
            "status": "PARTIAL",
            "href": f"/use-cases/{item.slug}",
        }
        for item in use_cases_items
    ]
    pricing_tiers = await get_public_pricing_tiers(db)
    status_summary = await get_public_status_summary(db, app)
    clean_origin = canonical_origin.rstrip("/") or "https://voxdesk.ai"

    return PublicSiteManifest(
        site_name="VoxDesk",
        tagline="Voice-agent workspace and platform tools",
        canonical_origin=clean_origin,
        public_routes=sorted(PUBLIC_EXACT_ROUTES),
        protected_route_prefixes=list(PROTECTED_PREFIX_ROUTES),
        navigation=PUBLIC_NAVIGATION,
        capabilities=_public_capabilities(app),
        use_cases=use_cases_summary,
        security_highlights=_public_security_highlights(app),
        pricing_tiers=pricing_tiers,
        status_summary=status_summary,
        seo_meta=PublicSEOMeta(
            title="VoxDesk — Voice-agent workspace",
            description=(
                "Explore VoxDesk voice-agent workflows, public billing-plan data, integration requirements, and workspace tools."
            ),
            canonical_url=f"{clean_origin}/",
            robots="index, follow",
            og_type="website",
        ),
    )


async def create_contact_sales_request(
    db: AsyncSession,
    payload: PublicContactSalesCreate,
    *,
    origin: str | None = None,
    client_ip: str | None = None,
) -> PublicContactSalesRead:
    """Persist a durable Contact Sales request in `public_contact_sales_requests`."""
    row = PublicContactSalesRequest(
        full_name=payload.full_name.strip(),
        work_email=payload.work_email.strip().lower(),
        company_name=payload.company_name.strip(),
        job_title=(payload.job_title or "").strip(),
        phone_number=payload.phone_number.strip() if payload.phone_number else None,
        monthly_call_volume=payload.monthly_call_volume.strip() or "10k-50k",
        primary_use_case=payload.primary_use_case.strip() or "voice_agents",
        message=(payload.message or "").strip(),
        source_path=payload.source_path.strip() or "/contact-sales",
        origin=origin[:255] if origin else None,
        client_ip=client_ip[:64] if client_ip else None,
        status="received",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    return PublicContactSalesRead(
        id=str(row.id),
        full_name=row.full_name,
        work_email=row.work_email,
        company_name=row.company_name,
        job_title=row.job_title,
        phone_number=row.phone_number,
        monthly_call_volume=row.monthly_call_volume,
        primary_use_case=row.primary_use_case,
        status=row.status,
        created_at=row.created_at,
        confirmation_message=(
            f"Thank you, {row.full_name}. Your inquiry for {row.company_name} has been "
            f"recorded (ref #{str(row.id)[:8]})."
        ),
    )


async def register_public_workspace_signup(
    db: AsyncSession,
    payload: PublicSignupRequest,
    *,
    ip_address: str = "",
    user_agent: str = "",
) -> tuple[User, Tenant, dict[str, Any], str]:
    """Create a real isolated Tenant and OWNER User, issue real JWT/refresh tokens, and validate `next_path`."""
    normalized_email = auth_service.normalize_email(payload.email)
    pw.validate_policy(payload.password, email=normalized_email)

    existing = (
        await db.execute(select(User).where(User.email == normalized_email))
    ).scalar_one_or_none()
    if existing is not None:
        raise AppError(
            status_code=409,
            code="email_already_registered",
            message="That email address is already registered.",
        )

    _, sanitized_next = validate_safe_next_path(
        payload.next_path, default_path="/dashboard", strict=True
    )

    tenant = Tenant(
        name=payload.organization_name.strip()[:160],
        twilio_number=f"+1555{uuid.uuid4().int % 10**7:07d}",
        business_open=time(9, 0),
        business_close=time(17, 0),
        outbound_window_open=time(9, 0),
        outbound_window_close=time(20, 0),
    )
    db.add(tenant)
    await db.flush()

    user = User(
        tenant_id=tenant.id,
        email=normalized_email,
        full_name=(payload.full_name or payload.organization_name).strip()[:200],
        password_hash=pw.hash_password(payload.password),
        role=UserRole.OWNER,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    await auth_service.record_audit(
        db,
        action=AuditAction.USER_CREATED,
        tenant_id=tenant.id,
        actor_user_id=user.id,
        target_user_id=user.id,
        actor_email=user.email,
        ip_address=ip_address,
        user_agent=user_agent,
        detail={
            "source": "public_signup",
            "organization_name": tenant.name,
            "industry": payload.industry,
            "role": UserRole.OWNER.value,
        },
        commit=False,
    )
    await db.commit()
    await db.refresh(tenant)
    await db.refresh(user)

    tokens = await auth_service.issue_tokens(
        db, user, ip_address=ip_address, user_agent=user_agent
    )
    return user, tenant, tokens, sanitized_next


def generate_robots_txt(canonical_origin: str = "https://voxdesk.ai") -> str:
    """Generate robots.txt allowing public pages and disallowing protected dashboard/API paths."""
    clean_origin = canonical_origin.rstrip("/") or "https://voxdesk.ai"
    lines = [
        "User-agent: *",
        "Allow: /",
        "Allow: /product",
        "Allow: /use-cases",
        "Allow: /pricing",
        "Allow: /security",
        "Allow: /docs",
        "Allow: /status",
        "Allow: /contact-sales",
        "Disallow: /dashboard",
        "Disallow: /agents",
        "Disallow: /studio",
        "Disallow: /calls",
        "Disallow: /analytics",
        "Disallow: /settings",
        "Disallow: /billing",
        "Disallow: /workspace",
        "Disallow: /api/",
        "Disallow: /auth/",
        f"Sitemap: {clean_origin}/sitemap.xml",
    ]
    return "\n".join(lines) + "\n"


def generate_sitemap_xml(canonical_origin: str = "https://voxdesk.ai") -> str:
    """Generate sitemap.xml containing only public indexable routes (never private tenant/agent routes)."""
    clean_origin = canonical_origin.rstrip("/") or "https://voxdesk.ai"
    public_paths = [
        "/",
        "/product",
        "/product/voice-agents",
        "/product/customer-service",
        "/product/answering-service",
        "/product/appointment-setter",
        "/product/outbound",
        "/product/inbound",
        "/product/analytics",
        "/use-cases",
        "/pricing",
        "/security",
        "/docs",
        "/status",
        "/contact-sales",
        "/login",
        "/signup",
    ]
    url_entries = []
    for path in public_paths:
        loc = f"{clean_origin}{path}"
        priority = "1.0" if path == "/" else ("0.8" if path in {"/product", "/pricing", "/use-cases"} else "0.6")
        url_entries.append(
            f"  <url>\n    <loc>{loc}</loc>\n    <changefreq>weekly</changefreq>\n    <priority>{priority}</priority>\n  </url>"
        )
    body = "\n".join(url_entries)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n"
        "</urlset>\n"
    )
