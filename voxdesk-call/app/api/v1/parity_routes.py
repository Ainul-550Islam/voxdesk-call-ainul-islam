"""Authenticated, read-only APIs for the final parity dashboard."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.environments.resource_scope import resolve_scope
from app.tenancy.isolation import HierarchyError, to_http
from app.schemas.parity import (
    CapabilityInventoryResponse,
    E2EInspectionResponse,
    IntegrationInventoryResponse,
)
from app.services import e2e_orchestrator, parity_service

router = APIRouter(prefix="/api/v1/parity", tags=["parity"])


@router.get("/capabilities", response_model=CapabilityInventoryResponse)
async def get_capability_inventory(
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
) -> CapabilityInventoryResponse:
    """List capabilities backed by routes in this running application.

    Route registration is labelled as implementation evidence, not verification.
    """
    # The tenant context is intentionally consumed by the permission dependency;
    # the route inventory itself is deployment-wide and contains no tenant data.
    del ctx
    return parity_service.capability_inventory(request.app)


@router.get("/integrations", response_model=IntegrationInventoryResponse)
async def get_integration_inventory(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
) -> IntegrationInventoryResponse:
    return await parity_service.integration_inventory(session, ctx.tenant_id)


@router.get("/e2e/inspect", response_model=E2EInspectionResponse)
async def inspect_e2e_flow(
    agent_id: UUID = Query(...),
    call_id: UUID | None = Query(default=None),
    environment_id: UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
) -> E2EInspectionResponse:
    if ctx.environment_id is not None and environment_id not in (None, ctx.environment_id):
        raise HTTPException(status_code=403, detail="Credential is bound to another environment.")
    try:
        scope = await resolve_scope(
            session,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            explicit_environment_id=environment_id or ctx.environment_id,
            for_write=False,
        )
        return await e2e_orchestrator.inspect_agent_call_flow(
            session,
            tenant_id=ctx.tenant_id,
            environment_id=scope.id,
            agent_id=agent_id,
            call_id=call_id,
        )
    except e2e_orchestrator.E2EResourceNotFound as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc
    except HierarchyError as exc:
        raise to_http(exc) from None
