"""Observation-driven deployment verification; caller claims cannot establish truth."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from app.deployment.adapters.base import DeploymentObservation, DeploymentRequest

_ALLOWED_TRANSITIONS = {
    "not_verified": {"queued", "failed"},
    "approved": {"queued", "failed"},
    "queued": {"applying", "failed"},
    "applying": {"deployment_observed", "failed"},
    "deployment_observed": {"runtime_verified", "failed"},
    "runtime_verified": {"deployment_observed", "failed"},
    "failed": {"queued"},
}


def advance_verification_state(current: str, next_state: str, *, observation: DeploymentObservation | None = None, verdict: dict | None = None) -> str:
    if next_state not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError("invalid deployment verification state transition")
    if next_state == "deployment_observed" and (observation is None or not observation.observed):
        raise ValueError("deployment observation is required for observed state")
    if next_state == "runtime_verified" and not (
        observation is not None
        and observation.observed
        and observation.deployed
        and observation.verified
        and observation.health_state == "healthy"
        and verdict
        and verdict.get("verified") is True
    ):
        raise ValueError("authoritative passing observation is required for runtime_verified")
    return next_state


def verify_observation(
    request: DeploymentRequest,
    observation: DeploymentObservation,
    *,
    now: datetime | None = None,
    max_age_seconds: int = 300,
) -> dict:
    moment = now or datetime.now(timezone.utc)
    observed_at = observation.observed_at
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    fresh = timedelta(0) <= moment - observed_at <= timedelta(seconds=max_age_seconds)
    identity = {
        "tenant": observation.observed_tenant_id == request.tenant_id,
        "organization": observation.observed_organization_id == request.organization_id,
        "environment": observation.observed_environment_id == request.environment_id,
        "target": observation.observed_target_id == request.target_id,
        "revision": observation.observed_revision == request.revision_id,
    }
    digest_matches = observation.observed_digest == request.artifact_digest
    artifact_matches = observation.observed_artifact in {request.artifact_reference, request.artifact_digest}
    fingerprint_matches = observation.observed_fingerprint == request.manifest_fingerprint
    observation_present = bool(observation.observed and observation.reference)
    identity_matches = all(identity.values())
    scoped_fresh_observation = bool(observation_present and fresh and identity_matches)
    healthy = observation.health_state == "healthy"
    checks = {
        "authoritative_observation": observation_present,
        "observation_fresh": fresh,
        "tenant_match": identity["tenant"],
        "organization_match": identity["organization"],
        "environment_match": identity["environment"],
        "target_match": identity["target"],
        "revision_match": identity["revision"],
        "approved_digest_match": digest_matches,
        "approved_artifact_match": artifact_matches,
        "manifest_fingerprint_match": fingerprint_matches,
        "runtime_healthy": healthy,
        "adapter_verified": bool(observation.verified),
        "runtime_deployed": bool(observation.deployed),
    }
    verified = bool(scoped_fresh_observation and digest_matches and artifact_matches and fingerprint_matches and healthy and observation.verified and observation.deployed)
    deployed = bool(scoped_fresh_observation and observation.deployed)
    state = "runtime_verified" if verified else "deployment_observed" if scoped_fresh_observation else "not_verified"
    return {
        "state": state,
        "verified": verified,
        "deployed": deployed,
        "observed": scoped_fresh_observation,
        "observation_present": observation_present,
        "checks": checks,
        "reason": None if verified else observation.reason or "authoritative observation is stale, unhealthy, out of scope, or does not match the approved revision/artifact",
    }
