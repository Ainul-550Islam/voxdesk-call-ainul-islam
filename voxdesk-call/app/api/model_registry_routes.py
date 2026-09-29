"""Tenant-scoped durable model registry and lifecycle API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.tenancy.isolation import HierarchyError, require_same_tenant, to_http

from app.governance import service
from app.governance.context import resolve_scope
from app.governance.schemas import (
    ModelEvaluationRequest,
    ModelRegistryCreate,
    ModelRegistryOut,
    ModelVersionCreate,
    ModelVersionOut,
)

router = APIRouter(
    prefix="/api/tenants/{tenant_id}/governance/models", tags=["model-registry"]
)


async def _scope(session, ctx, tenant_id):
    require_same_tenant(ctx.tenant, tenant_id)
    return await resolve_scope(session, ctx)


@router.get("", response_model=list[ModelRegistryOut])
async def list_models(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id)
        return await service.list_registries(session, scope)
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("", response_model=ModelRegistryOut, status_code=201)
async def register_model(
    tenant_id: uuid.UUID,
    payload: ModelRegistryCreate,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_MODEL_MANAGE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id)
        if payload.organization_id is not None and payload.organization_id != scope.organization_id:
            raise HierarchyError("Not found", code="not_found", status_code=404)
        row = await service.create_registry(
            session,
            scope,
            provider=payload.provider,
            model_name=payload.model_name,
            display_name=payload.display_name,
            risk_tier=payload.risk_tier,
            intended_use=payload.intended_use,
            data_classes=payload.data_classes,
            capabilities=payload.capabilities,
            metadata=payload.metadata,
            actor_user_id=ctx.user_id,
            family=payload.family,
            purpose=payload.purpose,
            approved_for_channels=payload.approved_for_channels,
            approved_for_environments=payload.approved_for_environments,
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/{registry_id}/versions", response_model=ModelVersionOut, status_code=201)
async def register_model_version(
    tenant_id: uuid.UUID,
    registry_id: uuid.UUID,
    payload: ModelVersionCreate,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_MODEL_MANAGE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id)
        row = await service.add_model_version(
            session,
            scope,
            registry_id,
            version=payload.version,
            artifact_uri=payload.artifact_uri,
            artifact_digest=payload.artifact_digest,
            evaluation_summary=payload.evaluation_summary,
            actor_user_id=ctx.user_id,
            fingerprint=payload.fingerprint,
            capabilities=payload.capabilities,
            input_modalities=payload.input_modalities,
            output_modalities=payload.output_modalities,
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/versions/{version_id}/evaluate", response_model=ModelVersionOut)
async def evaluate_model_version(
    tenant_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: ModelEvaluationRequest,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_MODEL_MANAGE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id)
        row = await service.record_model_evaluation(
            session,
            scope,
            version_id,
            verifier=payload.verifier,
            verification_reference=payload.verification_reference,
            passed=payload.passed,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/versions/{version_id}/approve", response_model=ModelVersionOut)
async def approve_model_version(
    tenant_id: uuid.UUID,
    version_id: uuid.UUID,
    approval_reference: str,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id)
        row = await service.approve_model_version(
            session,
            scope,
            version_id,
            approval_reference=approval_reference,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/{registry_id}/transition", response_model=ModelRegistryOut)
async def transition_model(
    tenant_id: uuid.UUID,
    registry_id: uuid.UUID,
    target: str,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_MODEL_MANAGE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id)
        row = await service.transition_registry(
            session, scope, registry_id, target, actor_user_id=ctx.user_id
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/versions/{version_id}/transition", response_model=ModelVersionOut)
async def transition_version(
    tenant_id: uuid.UUID,
    version_id: uuid.UUID,
    target: str,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_MODEL_MANAGE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id)
        row = await service.transition_model_version(
            session, scope, version_id, target, actor_user_id=ctx.user_id
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None
