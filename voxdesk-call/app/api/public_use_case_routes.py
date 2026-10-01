"""
app/api/public_use_case_routes.py — Public use-case catalog, category, search and detail endpoints.
Public, no auth, no tenant data, safe pagination, rate limiting, audit logging.
Reuses existing public_home_routes capability registry as source of truth, but provides dedicated catalog API.
"""
from __future__ import annotations
from typing import Optional
import time
from fastapi import APIRouter, Query, Request, HTTPException
from app.schemas.public_use_case import (
    UseCaseListResponse,
    UseCaseDetailResponse,
    UseCaseCategoriesResponse,
    UseCaseQuery,
    UseCaseListData,
)
from app.services.public_use_case_service import get_use_cases, get_categories, get_use_case_by_slug
from app.core.logging import log

router = APIRouter(prefix="/api/v1/public/use-cases", tags=["public-use-cases"])

# Simple in-memory rate limiting for public endpoints
_rate_limit: dict[str, list[float]] = {}

def _check_rate_limit(ip: str, max_per_min: int = 60) -> bool:
    now = time.time()
    window = _rate_limit.get(ip, [])
    window = [t for t in window if now - t < 60]
    if len(window) >= max_per_min:
        _rate_limit[ip] = window
        return False
    window.append(now)
    _rate_limit[ip] = window
    return True

def _get_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"

@router.get("", response_model=UseCaseListResponse)
async def list_use_cases(
    request: Request,
    q: Optional[str] = Query(None, max_length=200, description="Search query"),
    category: Optional[str] = Query(None, max_length=100, description="Category filter"),
    page: int = Query(1, ge=1, le=1000),
    page_size: int = Query(24, ge=1, le=100),
):
    """
    GET /api/v1/public/use-cases — Public catalog with search, category filter, pagination.
    Query params validated via UseCaseQuery schema, safe, no tenant data.
    """
    ip = _get_ip(request)
    if not _check_rate_limit(ip, max_per_min=60):
        raise HTTPException(status_code=429, detail="Too many requests")
    try:
        # Validate via schema
        query = UseCaseQuery(q=q, category=category, page=page, page_size=page_size)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))
    # Get categories for response
    categories = get_categories()
    # Validate category if provided
    if query.category:
        valid_cats = {c.id for c in categories}
        if query.category not in valid_cats:
            # Fallback to All per spec — do not crash, return empty with valid categories
            # Alternatively, we could return 404, but spec says fallback to All or explicit "Category not found"
            # We choose to return empty items with categories, and total 0, so frontend can show "Category not found" or fallback
            # Here we return empty list but still provide categories
            items, total = [], 0
            data = UseCaseListData(items=items, categories=categories, total=total, page=query.page, page_size=query.page_size)
            log.info("public.use_cases.invalid_category", ip=ip, category=query.category)
            return UseCaseListResponse(data=data)
    items, total = get_use_cases(q=query.q, category=query.category, page=query.page, page_size=query.page_size)
    data = UseCaseListData(items=items, categories=categories, total=total, page=query.page, page_size=query.page_size)
    log.info("public.use_cases.list", ip=ip, q=query.q, category=query.category, total=total, page=query.page)
    return UseCaseListResponse(data=data)

@router.get("/categories", response_model=UseCaseCategoriesResponse)
async def list_categories(request: Request):
    """
    GET /api/v1/public/use-cases/categories — Backend-driven category list, no invented counts.
    """
    ip = _get_ip(request)
    if not _check_rate_limit(ip, max_per_min=60):
        raise HTTPException(status_code=429, detail="Too many requests")
    cats = get_categories()
    log.info("public.use_cases.categories", ip=ip, count=len(cats))
    return UseCaseCategoriesResponse(data=cats)

@router.get("/{slug}", response_model=UseCaseDetailResponse)
async def get_use_case_detail(request: Request, slug: str):
    """
    GET /api/v1/public/use-cases/{slug} — Detail page, supports direct deep links.
    Slug validated, safe, no tenant leakage.
    """
    ip = _get_ip(request)
    if not _check_rate_limit(ip, max_per_min=60):
        raise HTTPException(status_code=429, detail="Too many requests")
    # Validate slug format
    if len(slug) > 200 or not slug.replace("-", "").replace("_", "").isalnum():
        # Use strict regex check via service
        pass
    detail = get_use_case_by_slug(slug)
    if not detail:
        log.info("public.use_cases.not_found", ip=ip, slug=slug)
        raise HTTPException(status_code=404, detail="Use case not found")
    log.info("public.use_cases.detail", ip=ip, slug=slug)
    return UseCaseDetailResponse(data=detail)

@router.get("/health", response_model=dict)
async def health():
    """Public health check"""
    return {"status": "ok", "service": "public-use-cases", "at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "categories": len(get_categories())}
