"""Bounded lead import and export."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, scoped_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.leads import service
from app.leads.importers import MAX_BYTES
from app.tenancy.isolation import HierarchyError, to_http

router = APIRouter(tags=["lead-import"])


def _fail(exc: HierarchyError):
    raise to_http(exc) from None


@router.post("/api/tenants/{tenant_id}/lead-imports")
async def post_import(
    tenant_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_CREATE)),
    session: AsyncSession = Depends(get_session),
):
    claimed = request.query_params.get("tenant_id")
    claimed_id = uuid.UUID(claimed) if claimed else None
    body = await request.body()
    if len(body) > MAX_BYTES:
        from app.leads.exceptions import ImportTooLarge

        _fail(ImportTooLarge())
    try:
        result = await service.import_body(
            session,
            tenant_id=ctx.tenant_id,
            body=body,
            content_type=request.headers.get("content-type", ""),
            claimed_tenant_id=claimed_id,
            actor_id=ctx.user_id,
            idempotency_key=request.headers.get("idempotency-key"),
            organization_id=getattr(ctx.tenant, "organization_id", None),
            environment_id=_uuid_or_none(request.query_params.get("environment_id")),
        )
        await session.commit()
    except HierarchyError as exc:
        _fail(exc)
    return result


@router.get("/api/tenants/{tenant_id}/lead-exports")
async def get_export(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    limit: int = 1000,
    ctx: TenantContext = Depends(scoped_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        text = await service.export_leads(
            session,
            tenant_id=ctx.tenant_id,
            environment_id=environment_id,
            limit=limit,
        )
    except HierarchyError as exc:
        _fail(exc)
    return Response(content=text, media_type="text/csv")


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    return uuid.UUID(value)
