"""Authenticated tenant-derived model registry aliases."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.tenancy.isolation import HierarchyError, to_http

from app.governance import model_registry, service
from app.governance.context import resolve_scope
from app.governance.models import ModelRegistry
from app.governance.schemas import (
    ModelRegistryCreate,
    ModelRegistryOut,
    ModelVersionCreate,
    ModelVersionOut,
)

router = APIRouter(prefix="/api/governance/models", tags=["model-registry"])


@router.get("", response_model=list[ModelRegistryOut])
async def list_models(
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await service.list_registries(session, await resolve_scope(session, ctx))
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/approved", response_model=list[ModelVersionOut])
async def approved_models(
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await model_registry.approved_models(session, await resolve_scope(session, ctx))
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/{model_id}", response_model=ModelRegistryOut)
async def get_model(
    model_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx)
        row = await session.scalar(
            select(ModelRegistry).where(
                ModelRegistry.id == model_id,
                ModelRegistry.tenant_id == scope.tenant_id,
                ModelRegistry.organization_id == scope.organization_id,
            )
        )
        if row is None:
            from app.governance.exceptions import GovernanceNotFound

            raise GovernanceNotFound()
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("", response_model=ModelRegistryOut, status_code=201)
async def create_model(
    payload: ModelRegistryCreate,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_MODEL_MANAGE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx)
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


@router.post("/{model_id}/versions", response_model=ModelVersionOut, status_code=201)
async def create_version(
    model_id: uuid.UUID,
    payload: ModelVersionCreate,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_MODEL_MANAGE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx)
        row = await service.add_model_version(
            session,
            scope,
            model_id,
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


async def _transition(model_id, target, ctx, session):
    try:
        scope = await resolve_scope(session, ctx)
        row = await service.transition_registry(
            session, scope, model_id, target, actor_user_id=ctx.user_id
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/{model_id}/approve", response_model=ModelRegistryOut)
async def approve_model(
    model_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    return await _transition(model_id, "active", ctx, session)


@router.post("/{model_id}/suspend", response_model=ModelRegistryOut)
async def suspend_model(
    model_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    return await _transition(model_id, "suspended", ctx, session)


@router.post("/{model_id}/retire", response_model=ModelRegistryOut)
async def retire_model(
    model_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    return await _transition(model_id, "retired", ctx, session)

