"""Bounded export of one environment's resources.

The export id is not a secret and the URL does not carry a token. Each page is
authorized again. A page never includes another environment's rows.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_human_session
from app.auth.identity.events import emit
from app.db.models import AuditAction
from app.db.session import get_session
from app.environments.resource_types import EnvironmentResourceType
from app.resources.access import authorize, require_known_type
from app.resources.serialization import page_payload, scope_fields
from app.resources.service import read_page
from app.tenancy.isolation import HierarchyError, client_ip, to_http

router = APIRouter(
    prefix="/api/tenants/{tenant_id}/environments/{environment_id}/exports",
    tags=["environment-exports"],
)

_PAGE_MAX = 100


def _http(exc: HierarchyError):
    raise to_http(exc)


@router.get("")
async def export_catalog(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    try:
        _tenant, environment = await authorize(
            session, ctx.user, tenant_id=tenant_id, environment_id=environment_id,
            resource_type=EnvironmentResourceType.CALL, action="read", scopes=ctx.scopes,
        )
    except HierarchyError as exc:
        _http(exc)
    return {
        "environment_id": str(environment.id),
        "resource_types": [item.value for item in EnvironmentResourceType],
        "page_max": _PAGE_MAX,
    }


@router.get("/{resource_type}")
async def export_page(
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    resource_type: str,
    request: Request,
    limit: int = Query(default=50, ge=1, le=_PAGE_MAX),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_human_session),
    session: AsyncSession = Depends(get_session),
):
    try:
        kind = require_known_type(resource_type)
        _tenant, environment = await authorize(
            session, ctx.user, tenant_id=tenant_id, environment_id=environment_id,
            resource_type=kind, action="read", scopes=ctx.scopes,
        )
        rows, total = await read_page(
            session, kind, tenant_id=tenant_id, environment_id=environment_id,
            limit=limit, offset=offset,
        )
        await emit(
            session, AuditAction.RESOURCE_EXPORTED,
            tenant_id=tenant_id, actor_user_id=ctx.user.id, actor_email=ctx.user.email,
            ip_address=client_ip(request), user_agent=request.headers.get("user-agent", "")[:300],
            detail={
                "organization_id": str(ctx.tenant.organization_id),
                "tenant_id": str(tenant_id),
                "environment_id": str(environment.id),
                "resource_type": kind.value,
                "action": "export",
                "count": len(rows),
                "offset": offset,
            },
            commit=False,
        )
        await session.commit()
    except HierarchyError as exc:
        await session.rollback()
        _http(exc)
    return page_payload(
        resource_type=kind.value,
        items=[scope_fields(row, environment) for row in rows],
        limit=limit,
        offset=offset,
        total=total,
    )
