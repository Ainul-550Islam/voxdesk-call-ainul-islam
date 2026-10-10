"""Custom Analytics Dashboard & Aggregation Routes (Part 6 / Gate G7).

Provides tenant-scoped CRUD for custom dashboards, server-side metric/dimension
aggregation queries, shareable links, and CSV export.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Permission, TenantContext, require_permission
from app.core.errors import AppError
from app.db.session import get_session
from app.services import analytics_service, custom_dashboard_service

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics-dashboards"])


def _handle_app_error(exc: AppError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


class WidgetSpecRequest(BaseModel):
    id: str | None = Field(default=None, max_length=80)
    title: str = Field(..., min_length=1, max_length=120)
    metric: str = Field(..., min_length=1, max_length=64)
    dimension: str = Field(default="day", max_length=64)
    chart_type: str = Field(default="bar", max_length=32)
    filters: dict[str, Any] = Field(default_factory=dict)


class CreateDashboardRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    widgets: list[WidgetSpecRequest] = Field(default_factory=list)
    global_filters: dict[str, Any] = Field(default_factory=dict)
    share_enabled: bool = False


class UpdateDashboardRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=1000)
    widgets: list[WidgetSpecRequest] | None = None
    global_filters: dict[str, Any] | None = None
    share_enabled: bool | None = None


class AnalyticsQueryRequest(BaseModel):
    group_by: str = Field(default="day", max_length=64)
    start: datetime | None = None
    end: datetime | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    max_groups: int = Field(default=100, ge=1, le=200)


class EvaluateDashboardRequest(BaseModel):
    start: datetime | None = None
    end: datetime | None = None
    override_filters: dict[str, Any] = Field(default_factory=dict)
    max_groups: int = Field(default=100, ge=1, le=200)


class ShareDashboardRequest(BaseModel):
    enabled: bool = True
    rotate: bool = False


@router.post("/query")
async def run_analytics_query(
    body: AnalyticsQueryRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Run a tenant-scoped server-side analytics aggregation query."""
    try:
        return await analytics_service.compute_metrics_and_breakdown(
            session,
            ctx.tenant_id,
            start=body.start,
            end=body.end,
            group_by=body.group_by,
            filters=body.filters,
            max_groups=body.max_groups,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "INVALID_ANALYTICS_QUERY", "message": str(exc)},
        ) from exc
    except AppError as exc:
        raise _handle_app_error(exc) from exc


@router.get("/dashboards")
async def list_custom_dashboards(
    limit: int = Query(default=50, ge=1, le=50),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """List saved custom dashboards for the authenticated tenant."""
    try:
        return await custom_dashboard_service.list_dashboards(
            session, ctx.tenant_id, limit=limit, offset=offset
        )
    except AppError as exc:
        raise _handle_app_error(exc) from exc


@router.post("/dashboards", status_code=201)
async def create_custom_dashboard(
    body: CreateDashboardRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Create a saved custom dashboard for the authenticated tenant."""
    try:
        dash = await custom_dashboard_service.create_dashboard(
            session,
            ctx.tenant_id,
            name=body.name,
            description=body.description,
            widgets=[w.model_dump() for w in body.widgets],
            global_filters=body.global_filters,
            share_enabled=body.share_enabled,
            actor_id=ctx.user_id,
        )
        await session.commit()
        return dash
    except AppError as exc:
        await session.rollback()
        raise _handle_app_error(exc) from exc


@router.get("/dashboards/shared/{share_token}")
async def get_shared_custom_dashboard(
    share_token: str,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Resolve and evaluate a shared dashboard via its shareable token."""
    try:
        return await custom_dashboard_service.get_shared_dashboard(
            session, share_token, start=start, end=end
        )
    except AppError as exc:
        raise _handle_app_error(exc) from exc


@router.get("/dashboards/{dashboard_id}")
async def get_custom_dashboard(
    dashboard_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Retrieve a saved custom dashboard by ID."""
    try:
        return await custom_dashboard_service.get_dashboard(
            session, ctx.tenant_id, dashboard_id
        )
    except AppError as exc:
        raise _handle_app_error(exc) from exc


@router.patch("/dashboards/{dashboard_id}")
async def update_custom_dashboard(
    dashboard_id: uuid.UUID,
    body: UpdateDashboardRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Update a saved custom dashboard."""
    try:
        dash = await custom_dashboard_service.update_dashboard(
            session,
            ctx.tenant_id,
            dashboard_id,
            name=body.name,
            description=body.description,
            widgets=[w.model_dump() for w in body.widgets]
            if body.widgets is not None
            else None,
            global_filters=body.global_filters,
            share_enabled=body.share_enabled,
        )
        await session.commit()
        return dash
    except AppError as exc:
        await session.rollback()
        raise _handle_app_error(exc) from exc


@router.delete("/dashboards/{dashboard_id}")
async def delete_custom_dashboard(
    dashboard_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Delete a saved custom dashboard."""
    try:
        res = await custom_dashboard_service.delete_dashboard(
            session, ctx.tenant_id, dashboard_id
        )
        await session.commit()
        return res
    except AppError as exc:
        await session.rollback()
        raise _handle_app_error(exc) from exc


@router.post("/dashboards/{dashboard_id}/evaluate")
async def evaluate_custom_dashboard(
    dashboard_id: uuid.UUID,
    body: EvaluateDashboardRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Evaluate all widgets on a saved custom dashboard using server-side aggregation."""
    req = body or EvaluateDashboardRequest()
    try:
        return await custom_dashboard_service.evaluate_dashboard_widgets(
            session,
            ctx.tenant_id,
            dashboard_id,
            start=req.start,
            end=req.end,
            override_filters=req.override_filters,
            max_groups=req.max_groups,
        )
    except AppError as exc:
        raise _handle_app_error(exc) from exc


@router.post("/dashboards/{dashboard_id}/share")
async def share_custom_dashboard(
    dashboard_id: uuid.UUID,
    body: ShareDashboardRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Enable, disable, or rotate a shareable link for a custom dashboard."""
    try:
        res = await custom_dashboard_service.create_or_rotate_share_link(
            session,
            ctx.tenant_id,
            dashboard_id,
            enabled=body.enabled,
            rotate=body.rotate,
        )
        await session.commit()
        return res
    except AppError as exc:
        await session.rollback()
        raise _handle_app_error(exc) from exc


@router.get("/dashboards/{dashboard_id}/export.csv")
async def export_custom_dashboard_csv(
    dashboard_id: uuid.UUID,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """Export evaluated dashboard widgets as a CSV file."""
    try:
        csv_text = await custom_dashboard_service.export_dashboard_csv(
            session,
            ctx.tenant_id,
            dashboard_id,
            start=start,
            end=end,
        )
        return PlainTextResponse(
            content=csv_text,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="dashboard-{dashboard_id}.csv"'
            },
        )
    except AppError as exc:
        raise _handle_app_error(exc) from exc
