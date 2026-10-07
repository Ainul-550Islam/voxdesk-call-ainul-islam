"""Scoped deployment preflight, apply, status and observation-backed verification routes."""
from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.deployment.models import DeploymentReadiness, DeploymentRevision, DeploymentRuntimeObservation, DeploymentTarget, DeploymentVerification
from app.deployment.runtime import _adapter, _request, enqueue_deployment, registry_adapter
from app.deployment.readiness import evaluate_readiness
from app.deployment.verification import verify_observation
from app.deployment.evidence import record_deployment_observation
from app.governance.audit import record_governance_audit
from app.governance.context import resolve_scope
from app.governance.evidence import append_event
from app.governance.models import GovernancePolicyDecision
from app.review.models import ReviewCase
from app.tenancy.isolation import HierarchyError, to_http

router = APIRouter(prefix="/api/deployment", tags=["deployment-runtime"])


async def _scoped(session: AsyncSession, ctx: TenantContext, environment_id: uuid.UUID, revision_id: uuid.UUID):
    scope = await resolve_scope(session, ctx, environment_id)
    revision = await session.scalar(select(DeploymentRevision).where(
        DeploymentRevision.id == revision_id,
        DeploymentRevision.tenant_id == scope.tenant_id,
        DeploymentRevision.organization_id == scope.organization_id,
        DeploymentRevision.environment_id == scope.environment_id,
    ))
    if revision is None:
        raise HTTPException(status_code=404, detail="deployment revision not found")
    target = await session.scalar(select(DeploymentTarget).where(
        DeploymentTarget.id == revision.target_id,
        DeploymentTarget.tenant_id == scope.tenant_id,
        DeploymentTarget.organization_id == scope.organization_id,
        DeploymentTarget.environment_id == scope.environment_id,
    ))
    if target is None:
        raise HTTPException(status_code=404, detail="deployment target not found")
    return scope, revision, target


def _safe_verification(proof: DeploymentVerification) -> dict:
    return {"id": str(proof.id), "state": proof.state, "authoritative_verifier": proof.authoritative_verifier, "evidence_reference": proof.evidence_reference, "observed_fingerprint": proof.observed_fingerprint, "created_at": proof.created_at.isoformat()}


@router.get("/targets/{target_id}/readiness")
async def authoritative_readiness(target_id: uuid.UUID, environment_id: uuid.UUID, ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)), session: AsyncSession = Depends(get_session)):
    try:
        scope = await resolve_scope(session, ctx, environment_id)
        target = await session.scalar(select(DeploymentTarget).where(DeploymentTarget.id == target_id, DeploymentTarget.tenant_id == scope.tenant_id, DeploymentTarget.organization_id == scope.organization_id, DeploymentTarget.environment_id == scope.environment_id))
        if target is None:
            raise HTTPException(status_code=404, detail="deployment target not found")
        revision = await session.scalar(select(DeploymentRevision).where(DeploymentRevision.target_id == target.id, DeploymentRevision.tenant_id == scope.tenant_id, DeploymentRevision.organization_id == scope.organization_id, DeploymentRevision.environment_id == scope.environment_id).order_by(DeploymentRevision.revision_number.desc()).limit(1))
        registry = None if target.target_type == "air_gapped" else registry_adapter()
        return await evaluate_readiness(target=target, revision=revision, registry=registry, session=session, scope=scope)
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/revisions/{revision_id}/preflight")
async def preflight_deployment(revision_id: uuid.UUID, environment_id: uuid.UUID, ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)), session: AsyncSession = Depends(get_session)):
    try:
        scope, revision, target = await _scoped(session, ctx, environment_id, revision_id)
        registry = None if target.target_type == "air_gapped" else registry_adapter()
        result = await evaluate_readiness(target=target, revision=revision, registry=registry, session=session, scope=scope)
        session.add(DeploymentReadiness(target_id=target.id, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, readiness=result["readiness"], checks=result["check_results"]))
        await append_event(session, scope, event_type="deployment_preflight_completed", payload={"target_id": str(target.id), "revision_id": str(revision.id), "readiness": result["readiness"], "failed_checks": result["missing_prerequisites"]}, actor_user_id=ctx.user_id, subject_type="deployment_revision", subject_id=str(revision.id))
        await record_governance_audit(session, scope, event="deployment_preflight_completed", actor_user_id=ctx.user_id, detail={"revision_id": str(revision.id), "readiness": result["readiness"], "check_count": len(result["check_results"])})
        await session.commit()
        return result
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/revisions/{revision_id}/apply", status_code=202)
async def apply_deployment(revision_id: uuid.UUID, environment_id: uuid.UUID, ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)), session: AsyncSession = Depends(get_session)):
    try:
        scope = await resolve_scope(session, ctx, environment_id)
        job, created = await enqueue_deployment(session, scope, ctx.user_id, revision_id)
        await session.commit()
        return {"job_id": str(job.id), "status": job.status, "created": created, "deployment_observed": False, "runtime_verified": False}
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/revisions/{revision_id}/status")
async def deployment_status(revision_id: uuid.UUID, environment_id: uuid.UUID, ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)), session: AsyncSession = Depends(get_session)):
    try:
        _scope, revision, target = await _scoped(session, ctx, environment_id, revision_id)
        observation = await session.scalar(select(DeploymentRuntimeObservation).where(DeploymentRuntimeObservation.revision_id == revision.id, DeploymentRuntimeObservation.tenant_id == revision.tenant_id, DeploymentRuntimeObservation.organization_id == revision.organization_id, DeploymentRuntimeObservation.environment_id == revision.environment_id).order_by(DeploymentRuntimeObservation.observed_at.desc()).limit(1))
        return {"target_id": str(target.id), "revision_id": str(revision.id), "lifecycle_state": revision.state, "verification_state": revision.verification_state, "observed": bool(observation and observation.observed), "deployed": bool(observation and observation.deployed), "verified": bool(observation and observation.verified and revision.verification_state == "runtime_verified"), "observation": {"adapter": observation.adapter, "adapter_version": observation.adapter_version, "observed_at": observation.observed_at.isoformat(), "observed_revision": observation.observed_revision, "observed_artifact": observation.observed_artifact, "health_state": observation.health_state, "checks": observation.checks, "reason": observation.reason} if observation else None}
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/revisions/{revision_id}/verify")
async def verify_deployment(revision_id: uuid.UUID, environment_id: uuid.UUID, ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)), session: AsyncSession = Depends(get_session)):
    try:
        scope, revision, target = await _scoped(session, ctx, environment_id, revision_id)
        approval = await session.scalar(select(ReviewCase).where(ReviewCase.tenant_id == scope.tenant_id, ReviewCase.organization_id == scope.organization_id, ReviewCase.environment_id == scope.environment_id, ReviewCase.case_type == "deployment", ReviewCase.subject_type == "deployment_revision", ReviewCase.subject_id == str(revision.id), ReviewCase.status == "approved"))
        decision = await session.scalar(select(GovernancePolicyDecision).where(GovernancePolicyDecision.id == revision.policy_decision_id, GovernancePolicyDecision.tenant_id == scope.tenant_id, GovernancePolicyDecision.organization_id == scope.organization_id, GovernancePolicyDecision.environment_id == scope.environment_id, GovernancePolicyDecision.decision == "allow")) if revision.policy_decision_id else None
        if approval is None or decision is None:
            raise HTTPException(status_code=409, detail="persisted approval and governance decision are required")
        adapter = _adapter(target)
        if adapter is None:
            raise HTTPException(status_code=503, detail="no configured adapter supports this target")
        request = _request(target, revision)
        observation = await adapter.verify(request)
        verdict = verify_observation(request, observation)
        await record_deployment_observation(session, scope, ctx.user_id, revision=revision, observation=observation)
        revision.verification_state = verdict["state"]
        if verdict["verified"]:
            revision.state = "deployed"
        elif verdict["deployed"]:
            revision.state = "deployed"
        proof = DeploymentVerification(revision_id=revision.id, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, state=verdict["state"], authoritative_verifier=observation.adapter if verdict["observed"] else None, evidence_reference=observation.reference if verdict["observed"] else None, observed_fingerprint=observation.observed_fingerprint if verdict["observed"] else None)
        session.add(proof)
        await session.commit()
        return {"verification": _safe_verification(proof), "observed": verdict["observed"], "deployed": verdict["deployed"], "verified": verdict["verified"], "checks": verdict["checks"], "reason": verdict["reason"]}
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/revisions/{revision_id}/verification")
async def deployment_verification(revision_id: uuid.UUID, environment_id: uuid.UUID, ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_READ)), session: AsyncSession = Depends(get_session)):
    try:
        scope, revision, _target = await _scoped(session, ctx, environment_id, revision_id)
        proof = await session.scalar(select(DeploymentVerification).where(DeploymentVerification.revision_id == revision.id, DeploymentVerification.tenant_id == scope.tenant_id, DeploymentVerification.organization_id == scope.organization_id, DeploymentVerification.environment_id == scope.environment_id).order_by(DeploymentVerification.created_at.desc()).limit(1))
        return {"revision_id": str(revision.id), "verification_state": revision.verification_state, "verified": bool(proof and proof.state == "runtime_verified" and revision.verification_state == "runtime_verified"), "verification": _safe_verification(proof) if proof else None}
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/revisions/{revision_id}/enqueue", status_code=202, include_in_schema=False)
async def queue_authorized_deployment(revision_id: uuid.UUID, environment_id: uuid.UUID, ctx: TenantContext = Depends(require_permission(Permission.GOVERNANCE_WRITE)), session: AsyncSession = Depends(get_session)):
    return await apply_deployment(revision_id, environment_id, ctx, session)
