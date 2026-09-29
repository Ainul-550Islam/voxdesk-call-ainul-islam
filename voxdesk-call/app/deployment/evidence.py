"""Safe, scope-bound deployment evidence projected into existing governance ledger."""
from __future__ import annotations

from app.governance.audit import record_governance_audit
from app.governance.evidence import append_event
from app.deployment.models import DeploymentRuntimeObservation


def safe_observation(observation) -> dict:
    """Allowlisted evidence fields only; adapter exception text and credentials never persist."""
    return {
        "adapter": observation.adapter,
        "adapter_version": observation.adapter_version,
        "observation_timestamp": observation.observed_at.isoformat(),
        "observed_revision": observation.observed_revision,
        "observed_artifact": observation.observed_artifact,
        "health_state": observation.health_state,
        "observed": bool(observation.observed),
        "deployed": bool(observation.deployed),
        "verified": bool(observation.verified),
        "state": observation.state,
        "reference": observation.reference,
        "observed_digest": observation.observed_digest,
        "observed_fingerprint": observation.observed_fingerprint,
        "checks": dict(observation.checks),
        "evidence": dict(observation.evidence),
        "reason": observation.reason,
    }


async def record_deployment_observation(session, scope, actor_id, *, revision, observation):
    proof = safe_observation(observation)
    payload = {"revision_id": str(revision.id), "target_id": str(revision.target_id), "manifest_fingerprint": revision.manifest_fingerprint, "observation": proof}
    row = DeploymentRuntimeObservation(target_id=revision.target_id, revision_id=revision.id, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, adapter=observation.adapter, adapter_version=observation.adapter_version, observed_at=observation.observed_at, observed_revision=observation.observed_revision, observed_artifact=observation.observed_artifact, health_state=observation.health_state, observed=bool(observation.observed), deployed=bool(observation.deployed), verified=bool(observation.verified), state=observation.state, observed_digest=observation.observed_digest, observed_fingerprint=observation.observed_fingerprint, checks=proof["checks"], evidence=proof["evidence"], reason=observation.reason)
    session.add(row)
    await session.flush()
    event = await append_event(session, scope, event_type="deployment_runtime_observed", payload=payload, actor_user_id=actor_id, actor_type="system" if actor_id is None else "user", subject_type="deployment_revision", subject_id=str(revision.id))
    await record_governance_audit(session, scope, event="deployment_runtime_observed", actor_user_id=actor_id, detail={"revision_id": str(revision.id), "adapter": observation.adapter, "state": observation.state, "verified": bool(observation.verified)})
    return event
