"""Tenant-scoped policy, lineage, residency and attestation APIs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.tenancy.isolation import HierarchyError, require_same_tenant, to_http

from app.governance import attestation, residency, service
from app.governance.context import resolve_scope
from app.governance.schemas import (
    AttestationCreate,
    AttestationOut,
    LineageCreate,
    LineageOut,
    PolicyCreate,
    PolicyDecisionOut,
    PolicyDecisionRequest,
    PolicyOut,
    ResidencyIntentCreate,
    ResidencyIntentOut,
    ResidencyVerification,
    RetentionRuleCreate,
    RetentionRuleOut,
)

router = APIRouter(prefix="/api/tenants/{tenant_id}/governance", tags=["governance"])


async def _scope(session, ctx, tenant_id, environment_id=None):
    require_same_tenant(ctx.tenant, tenant_id)
    return await resolve_scope(session, ctx, environment_id)


@router.post("/policies", response_model=PolicyOut, status_code=201)
async def create_policy(
    tenant_id: uuid.UUID,
    payload: PolicyCreate,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id, payload.environment_id)
        if payload.organization_id is not None and payload.organization_id != scope.organization_id:
            raise HierarchyError("Not found", code="not_found", status_code=404)
        row = await service.create_policy(
            session,
            scope,
            name=payload.name,
            policy_type=payload.policy_type,
            rules=payload.rules,
            rationale=payload.rationale,
            actor_user_id=ctx.user_id,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/policies/{policy_id}/publish", response_model=PolicyOut)
async def publish_policy(
    tenant_id: uuid.UUID,
    policy_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id)
        row = await service.publish_policy(session, scope, policy_id, actor_user_id=ctx.user_id)
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/decisions", response_model=PolicyDecisionOut)
async def decide_policy(
    tenant_id: uuid.UUID,
    payload: PolicyDecisionRequest,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id, payload.environment_id)
        row = await service.decide(
            session,
            scope,
            policy_type=payload.policy_type,
            input_payload=payload.input_payload,
            principal_id=ctx.user_id,
            correlation_id=payload.correlation_id,
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/lineage", response_model=LineageOut, status_code=201)
async def capture_lineage(
    tenant_id: uuid.UUID,
    payload: LineageCreate,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id, payload.environment_id)
        row = await service.capture_lineage(
            session,
            scope,
            correlation_id=payload.correlation_id,
            input_payload=payload.input_payload,
            output_payload=payload.output_payload,
            tool_fingerprints=payload.tool_fingerprints,
            source_references=payload.source_references,
            model_registry_id=payload.model_registry_id,
            model_version_id=payload.model_version_id,
            decision_id=payload.decision_id,
            metadata=payload.metadata,
            actor_user_id=ctx.user_id,
            trace_id=payload.trace_id,
            request_id=payload.request_id,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            source_type=payload.source_type,
            source_id=payload.source_id,
            parent_lineage_id=payload.parent_lineage_id,
            tool_name=payload.tool_name,
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/residency/intents", response_model=ResidencyIntentOut, status_code=201)
async def request_residency_intent(
    tenant_id: uuid.UUID,
    payload: ResidencyIntentCreate,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id, payload.environment_id)
        row = await residency.request_intent(
            session, scope, region=payload.region, actor_user_id=ctx.user_id
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/residency/intents/{intent_id}/verify", response_model=ResidencyIntentOut)
async def verify_residency_intent(
    tenant_id: uuid.UUID,
    intent_id: uuid.UUID,
    payload: ResidencyVerification,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, tenant_id)
        row = await residency.verify_intent(
            session,
            scope,
            intent_id,
            verifier=payload.verifier,
            verification_reference=payload.verification_reference,
            verification_metadata=payload.verification_metadata,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/retention", response_model=RetentionRuleOut, status_code=201)
async def create_retention_rule(
    tenant_id: uuid.UUID,
    payload: RetentionRuleCreate,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx)
        row = await service.create_retention_rule(
            session,
            scope,
            evidence_type=payload.evidence_type,
            retention_days=payload.retention_days,
            rationale=payload.rationale,
            legal_hold=payload.legal_hold,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/attestations", response_model=AttestationOut, status_code=201)
async def issue_attestation(
    tenant_id: uuid.UUID,
    payload: AttestationCreate,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx)
        row = await attestation.issue_attestation(
            session,
            scope,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            issuer=payload.issuer,
            claims=payload.claims,
            expires_at=payload.expires_at,
            actor_user_id=ctx.user_id,
            attestation_type=payload.attestation_type,
            period_start=payload.period_start,
            period_end=payload.period_end,
            metadata=payload.metadata,
        )
        await session.commit()
        await session.refresh(row)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/attestations/{attestation_id}", response_model=AttestationOut)
async def get_attestation(
    tenant_id: uuid.UUID,
    attestation_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx)
        row = await attestation.verify_attestation(session, scope, attestation_id)
        return row
    except HierarchyError as exc:
        raise to_http(exc) from None
