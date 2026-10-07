"""Prompt 5: Public Website, Public Catalog, Status, Contact Sales, and SEO Routes.

All endpoints in this router are strictly public-safe and never query or return
private tenant data, private agents, calls, transcripts, or secrets.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limit import allow_identity_action, client_ip as client_ip_from_request
from app.db.session import get_session
from app.domain.public_site_models import (
    PublicAuthBoundaryDecision,
    PublicContactSalesCreate,
    PublicContactSalesRead,
    PublicPricingTier,
    PublicSiteManifest,
    PublicSiteStatusSummary,
)
from app.services.public_auth_boundary_service import evaluate_route_boundary
from app.services.public_origin_service import extract_request_origin
from app.services.public_site_service import (
    create_contact_sales_request,
    generate_robots_txt,
    generate_sitemap_xml,
    get_public_pricing_tiers,
    get_public_site_manifest,
    get_public_status_summary,
)

router = APIRouter(tags=["public-site"])

async def _check_contact_sales_rate_limit(ip: str, limit_per_minute: int = 10) -> None:
    allowed = await allow_identity_action(
        action="public:contact_sales",
        who=ip,
        limit=limit_per_minute,
        window=60.0,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited", "message": "Too many contact sales submissions. Please wait a minute and try again."},
            headers={"Retry-After": "60"},
        )


def _canonical_origin_from_request(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    host = request.headers.get("host") or request.url.netloc or "voxdesk.ai"
    return f"{scheme}://{host}"


@router.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
async def robots_txt(request: Request) -> PlainTextResponse:
    content = generate_robots_txt(_canonical_origin_from_request(request))
    return PlainTextResponse(
        content=content,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml(request: Request) -> Response:
    content = generate_sitemap_xml(_canonical_origin_from_request(request))
    return Response(
        content=content,
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/api/v1/public/site/manifest", response_model=PublicSiteManifest)
async def public_site_manifest(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PublicSiteManifest:
    return await get_public_site_manifest(
        session,
        canonical_origin=_canonical_origin_from_request(request),
        app=request.app,
    )


@router.get("/api/v1/public/site/pricing", response_model=list[PublicPricingTier])
async def public_site_pricing(
    session: AsyncSession = Depends(get_session),
) -> list[PublicPricingTier]:
    return await get_public_pricing_tiers(session)


@router.get("/api/v1/public/site/status", response_model=PublicSiteStatusSummary)
async def public_site_status(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PublicSiteStatusSummary:
    return await get_public_status_summary(session, request.app)


@router.get("/api/v1/public/site/route-boundary", response_model=PublicAuthBoundaryDecision)
async def public_route_boundary_check(
    path: str = Query("/", max_length=512),
    authenticated: bool = Query(False),
    next: str | None = Query(None, max_length=512),
) -> PublicAuthBoundaryDecision:
    return evaluate_route_boundary(
        path=path,
        is_authenticated=authenticated,
        next_param=next,
    )


@router.post(
    "/api/v1/public/contact-sales",
    response_model=PublicContactSalesRead,
    status_code=status.HTTP_201_CREATED,
)
async def submit_contact_sales(
    payload: PublicContactSalesCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PublicContactSalesRead:
    ip = client_ip_from_request(request)
    await _check_contact_sales_rate_limit(ip)
    origin = extract_request_origin(
        origin_header=request.headers.get("origin"),
        referer_header=request.headers.get("referer"),
    )
    return await create_contact_sales_request(
        session,
        payload,
        origin=origin,
        client_ip=ip,
    )
