"""Runtime admission and durable execution over the canonical jobs queue."""
from __future__ import annotations
import uuid
from sqlalchemy import select
from app.deployment.adapters import AirGapAdapter, ContainerAdapter, KubernetesAdapter
from app.deployment.adapters.base import DeploymentRequest
from app.deployment.evidence import record_deployment_observation
from app.deployment.models import DeploymentRevision, DeploymentTarget, DeploymentVerification
from app.deployment.verification import advance_verification_state, verify_observation
from app.db.models import DurableJob, Environment, Organization, Tenant
from app.db.rls import set_tenant_context
from app.db.session import get_sessionmaker
from app.governance.context import GovernanceScope
from app.governance.policy import require_policy
from app.jobs import queue
from app.jobs.types import JobPriority, JobType, register_handler
from app.tenancy.isolation import LifecycleDenied


def registry_adapter():
    from app.core.config import settings
    from app.deployment.adapters.registry import ArtifactRegistryAdapter, OCIRegistryClient
    if not (settings.artifact_registry_url or "").strip():
        return ArtifactRegistryAdapter()
    return ArtifactRegistryAdapter(OCIRegistryClient(settings.artifact_registry_url, bearer_token=settings.artifact_registry_bearer_token or None))


def _airgap_adapter():
    import base64
    from app.core.config import settings
    try:
        key = base64.b64decode(settings.airgap_public_key_base64, validate=True) if settings.airgap_public_key_base64 else None
    except ValueError:
        key = None
    return AirGapAdapter(manifest_directory=settings.airgap_manifest_directory or None, public_key=key)


def _adapter(target):
    from app.core.config import settings
    if target.target_type == "air_gapped":
        return _airgap_adapter()
    if target.target_type in {"byoc", "private_cloud", "hybrid"}:
        configured = (settings.kubernetes_cluster_reference or "").rstrip("/")
        if not configured or (target.cluster_reference or "").rstrip("/") != configured:
            return KubernetesAdapter()
        return KubernetesAdapter(cluster_reference=configured)
    if target.target_type == "on_prem":
        return ContainerAdapter(enabled=settings.container_runtime_enabled)
    return None


def _request(target, revision):
    return DeploymentRequest(str(target.tenant_id), str(target.organization_id), str(target.environment_id), str(target.id), str(revision.id), target.target_type, revision.artifact_reference, revision.artifact_digest, revision.manifest_fingerprint, target.cluster_reference, offline=target.target_type == "air_gapped")


async def enqueue_deployment(session, scope: GovernanceScope, actor_id: uuid.UUID, revision_id: uuid.UUID):
    revision = await session.scalar(select(DeploymentRevision).where(DeploymentRevision.id == revision_id, DeploymentRevision.tenant_id == scope.tenant_id, DeploymentRevision.organization_id == scope.organization_id, DeploymentRevision.environment_id == scope.environment_id).with_for_update())
    if revision is None:
        raise LifecycleDenied("deployment revision is outside the active scope")
    target = await session.scalar(select(DeploymentTarget).where(DeploymentTarget.id == revision.target_id, DeploymentTarget.tenant_id == scope.tenant_id, DeploymentTarget.organization_id == scope.organization_id, DeploymentTarget.environment_id == scope.environment_id))
    if target is None:
        raise LifecycleDenied("deployment target is outside the active scope")
    import hashlib
    key = "deployment:" + hashlib.sha256(f"{scope.tenant_id}:{revision.id}:{revision.manifest_fingerprint}".encode()).hexdigest()
    if revision.state != "validated":
        existing = await session.scalar(select(DurableJob).where(DurableJob.tenant_id == scope.tenant_id, DurableJob.organization_id == scope.organization_id, DurableJob.environment_id == scope.environment_id, DurableJob.idempotency_key == key))
        if existing is not None and (existing.payload or {}).get("target_id") == str(target.id) and (existing.payload or {}).get("revision_id") == str(revision.id) and (existing.payload or {}).get("manifest_fingerprint") == revision.manifest_fingerprint:
            return existing, False
        raise LifecycleDenied("only a validated revision with no existing deployment job may be queued")
    from app.review.models import ReviewCase
    approval = await session.scalar(select(ReviewCase).where(ReviewCase.tenant_id == scope.tenant_id, ReviewCase.organization_id == scope.organization_id, ReviewCase.environment_id == scope.environment_id, ReviewCase.case_type == "deployment", ReviewCase.subject_type == "deployment_revision", ReviewCase.subject_id == str(revision.id), ReviewCase.status == "approved"))
    if approval is None:
        raise LifecycleDenied("a recorded human approval is required before deployment")
    decision = await require_policy(session, scope, policy_type="deployment", context={"state": "queued", "revision_id": str(revision.id), "artifact_digest": revision.artifact_digest}, principal_id=actor_id, correlation_id=str(revision.id))
    revision.policy_decision_id = getattr(decision, "id", None)
    await session.flush()
    from app.deployment.readiness import evaluate_readiness
    registry = None if target.target_type == "air_gapped" else registry_adapter()
    readiness = await evaluate_readiness(target=target, revision=revision, registry=registry, session=session, scope=scope)
    if readiness["readiness"] != "READY":
        raise LifecycleDenied("deployment prerequisites are not all authoritatively verified")
    import hashlib
    key = "deployment:" + hashlib.sha256(f"{scope.tenant_id}:{revision.id}:{revision.manifest_fingerprint}".encode()).hexdigest()
    job, created = await queue.enqueue(session, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, job_type=JobType.DEPLOYMENT, idempotency_key=key, payload={"target_id": str(target.id), "revision_id": str(revision.id), "manifest_fingerprint": revision.manifest_fingerprint}, priority=JobPriority.HIGH)
    revision.state = "queued"
    revision.verification_state = advance_verification_state(revision.verification_state, "queued")
    revision.policy_decision_id = getattr(decision, "id", None)
    from app.governance.evidence import append_event
    from app.governance.audit import record_governance_audit
    await append_event(session, scope, event_type="deployment_job_queued", payload={"revision_id": str(revision.id), "job_id": str(job.id), "manifest_fingerprint": revision.manifest_fingerprint}, actor_user_id=actor_id, subject_type="deployment_revision", subject_id=str(revision.id))
    await record_governance_audit(session, scope, event="deployment_job_queued", actor_user_id=actor_id, detail={"revision_id": str(revision.id), "job_id": str(job.id), "created": created})
    return job, created


@register_handler(JobType.DEPLOYMENT)
async def execute_deployment(job: DurableJob):
    payload = job.payload or {}
    # DurableJob columns, not payload claims, are the authorization scope.
    tenant_id, organization_id, environment_id = job.tenant_id, job.organization_id, job.environment_id
    if not organization_id or not environment_id:
        raise RuntimeError("deployment job is missing persisted tenant/environment scope")
    factory = get_sessionmaker()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        tenant = await session.get(Tenant, tenant_id)
        organization = await session.get(Organization, organization_id)
        environment = await session.scalar(select(Environment).where(Environment.id == environment_id, Environment.tenant_id == tenant_id))
        if not tenant or not organization or not environment:
            raise RuntimeError("deployment scope could not be resolved")
        scope = GovernanceScope(tenant, organization, environment)
        revision = await session.scalar(select(DeploymentRevision).where(DeploymentRevision.id == uuid.UUID(payload["revision_id"]), DeploymentRevision.tenant_id == tenant_id, DeploymentRevision.organization_id == organization_id, DeploymentRevision.environment_id == environment_id).with_for_update())
        target = await session.scalar(select(DeploymentTarget).where(DeploymentTarget.id == uuid.UUID(payload["target_id"]), DeploymentTarget.tenant_id == tenant_id, DeploymentTarget.organization_id == organization_id, DeploymentTarget.environment_id == environment_id))
        if revision is None or target is None or target.id != revision.target_id or revision.manifest_fingerprint != payload.get("manifest_fingerprint"):
            raise RuntimeError("approved deployment identity could not be resolved")
        import hashlib
        expected_job_key = "deployment:" + hashlib.sha256(f"{tenant_id}:{revision.id}:{revision.manifest_fingerprint}".encode()).hexdigest()
        if job.idempotency_key != expected_job_key:
            raise RuntimeError("deployment job idempotency identity is invalid")
        from app.review.models import ReviewCase
        from app.governance.models import GovernancePolicyDecision
        from app.jobs.models import PermanentJobError, RetryableJobError
        approval = await session.scalar(select(ReviewCase).where(ReviewCase.tenant_id == tenant_id, ReviewCase.organization_id == organization_id, ReviewCase.environment_id == environment_id, ReviewCase.case_type == "deployment", ReviewCase.subject_type == "deployment_revision", ReviewCase.subject_id == str(revision.id), ReviewCase.status == "approved"))
        policy_decision = await session.scalar(select(GovernancePolicyDecision).where(GovernancePolicyDecision.id == revision.policy_decision_id, GovernancePolicyDecision.tenant_id == tenant_id, GovernancePolicyDecision.organization_id == organization_id, GovernancePolicyDecision.environment_id == environment_id, GovernancePolicyDecision.decision == "allow")) if revision.policy_decision_id else None
        if approval is None or policy_decision is None:
            raise PermanentJobError("deployment_authorization_missing", "persisted human approval or governance authorization is missing")
        if revision.state not in {"queued", "deploying", "degraded", "deployed"}:
            raise PermanentJobError("deployment_state_invalid", "revision is not in an executable deployment state")
        adapter = _adapter(target)
        if adapter is None:
            raise PermanentJobError("deployment_adapter_unsupported", "no configured runtime adapter supports this target")
        request = _request(target, revision)
        current_verification = revision.verification_state
        if current_verification == "queued":
            revision.verification_state = advance_verification_state(current_verification, "applying")
            revision.state = "deploying"
            await session.flush()
            observation = await adapter.apply(request)
        elif current_verification in {"applying", "deployment_observed", "runtime_verified"}:
            # Retried or already-verified jobs re-observe; they never trust a stale prior boolean.
            observation = await adapter.status(request)
        else:
            raise PermanentJobError("verification_state_invalid", "revision is not durably queued for execution")
        if not observation.observed:
            await record_deployment_observation(session, scope, None, revision=revision, observation=observation)
            await session.commit()
            raise RetryableJobError("deployment_observation_unavailable", "adapter did not provide an authoritative runtime observation")
        verdict = verify_observation(request, observation)
        await record_deployment_observation(session, scope, None, revision=revision, observation=observation)
        if verdict["verified"]:
            if current_verification == "runtime_verified":
                revision.verification_state = "runtime_verified"
            elif current_verification == "deployment_observed":
                revision.verification_state = advance_verification_state(current_verification, "runtime_verified", observation=observation, verdict=verdict)
            else:
                observed_state = advance_verification_state("applying", "deployment_observed", observation=observation)
                revision.verification_state = advance_verification_state(observed_state, "runtime_verified", observation=observation, verdict=verdict)
        elif verdict["observed"]:
            if current_verification == "runtime_verified":
                revision.verification_state = advance_verification_state(current_verification, "deployment_observed", observation=observation)
            elif current_verification == "applying":
                revision.verification_state = advance_verification_state(current_verification, "deployment_observed", observation=observation)
            else:
                revision.verification_state = "deployment_observed"
        else:
            revision.verification_state = "failed"
        revision.state = "deployed" if verdict["deployed"] else "degraded"
        proof = DeploymentVerification(revision_id=revision.id, tenant_id=tenant_id, organization_id=organization_id, environment_id=environment_id, state=revision.verification_state, authoritative_verifier=observation.adapter if verdict["observed"] else None, evidence_reference=observation.reference if verdict["observed"] else None, observed_fingerprint=observation.observed_fingerprint or observation.observed_digest if verdict["observed"] else None)
        session.add(proof)
        await session.commit()
        from app.observability.runtime import observe_runtime_event
        observe_runtime_event("deployment", "verification", "success" if verdict["verified"] else "not_verified", tenant_id=tenant_id, environment_id=environment_id, revision_id=revision.id)
        if not verdict["verified"] and not verdict["deployed"] and observation.state in {"not_ready", "running_unverified", "applying", "pending"}:
            raise RetryableJobError("deployment_rollout_pending", "authoritative observation shows rollout is not yet healthy")
        if not verdict["verified"]:
            raise PermanentJobError("deployment_verification_failed", "runtime observation did not pass identity, health, freshness and artifact checks")
        return {"revision_id": str(revision.id), "state": revision.verification_state, "verified": True}
