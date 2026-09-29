from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.deployment.adapters.base import DeploymentObservation, DeploymentRequest
from app.deployment.adapters.registry import ArtifactRegistryAdapter, OCIRegistryClient
from app.deployment.readiness import evaluate_readiness
from app.deployment.verification import advance_verification_state, verify_observation


class Registry:
    async def resolve_digest(self, _ref):
        return "sha256:" + "a" * 64


def _target(**changes):
    values = {"id": "target", "environment_id": "env", "status": "active", "target_type": "byoc", "prerequisites": {}}
    values.update(changes)
    return SimpleNamespace(**values)


def _revision(target=None, **changes):
    values = {"id": "revision", "target_id": getattr(target, "id", "target"), "artifact_digest": "sha256:" + "a" * 64, "artifact_reference": "registry/app:v1", "runtime_version": "1.2.3", "migration_revision": "head", "policy_decision_id": None}
    values.update(changes)
    return SimpleNamespace(**values)


def _request():
    return DeploymentRequest("tenant", "org", "env", "target", "revision", "byoc", "registry/app:v1", "sha256:" + "a" * 64, "b" * 64)


def _observation(req=None, **changes):
    req = req or _request()
    values = dict(
        adapter="kubernetes", observed=True, deployed=True, verified=True, state="ready",
        reference="namespace/workload", observed_digest=req.artifact_digest,
        observed_fingerprint=req.manifest_fingerprint, observed_revision=req.revision_id,
        observed_artifact=req.artifact_reference, health_state="healthy",
        observed_tenant_id=req.tenant_id, observed_organization_id=req.organization_id,
        observed_environment_id=req.environment_id, observed_target_id=req.target_id,
    )
    values.update(changes)
    return DeploymentObservation(**values)


def test_readiness_keeps_registry_result_distinct_from_runtime_verification():
    target = _target()
    revision = _revision(target)
    report = asyncio.run(evaluate_readiness(target=target, revision=revision, registry=ArtifactRegistryAdapter(Registry())))
    assert report["check_results"]["artifact_registry"]["status"] == "PASS"
    assert report["readiness"] == "NOT_VERIFIED"
    assert report["runtime_verified"] is False
    assert report["check_results"]["residency"]["status"] == "NOT_VERIFIED"


def test_readiness_detects_missing_environment_revision_and_artifact():
    target = _target(environment_id=None)
    revision = _revision(target, target_id="other", artifact_digest="bad", artifact_reference="", runtime_version="", migration_revision="")
    report = asyncio.run(evaluate_readiness(target=target, revision=revision))
    checks = report["check_results"]
    assert checks["environment"]["status"] == "FAIL"
    assert checks["revision_target_match"]["status"] == "FAIL"
    assert checks["artifact_digest_format"]["status"] == "FAIL"
    assert checks["artifact_reference"]["status"] == "FAIL"
    assert report["readiness"] == "NOT_READY"


def test_readiness_reports_approval_residency_monitoring_and_backup_unverified_without_authority():
    target = _target(prerequisites={"network_rules": True, "monitoring": True, "backup_restore": True, "secret_references": ["vault:ref"]})
    report = asyncio.run(evaluate_readiness(target=target, revision=_revision(target), registry=ArtifactRegistryAdapter(Registry())))
    checks = report["check_results"]
    assert checks["secret_references"]["status"] == "NOT_VERIFIED"
    assert checks["network_rules"]["status"] == "NOT_VERIFIED"
    assert checks["monitoring"]["status"] == "NOT_VERIFIED"
    assert checks["backup_restore"]["status"] == "NOT_VERIFIED"
    assert checks["governance"]["status"] == "NOT_VERIFIED"
    assert checks["approval"]["status"] == "NOT_VERIFIED"


def test_oci_registry_client_is_pinned_to_configured_host():
    class Response:
        status_code = 200
        headers = {"Docker-Content-Digest": "sha256:" + "a" * 64}
        def raise_for_status(self): return None
    class Client:
        async def head(self, url, *, headers):
            assert url == "https://registry.example/v2/team/app/manifests/v1"
            assert headers["Authorization"] == "Bearer private-token"
            return Response()
    client = OCIRegistryClient("https://registry.example", bearer_token="private-token", client=Client())
    assert asyncio.run(client.resolve_digest("registry.example/team/app:v1")) == "sha256:" + "a" * 64
    with pytest.raises(ValueError):
        asyncio.run(client.resolve_digest("attacker.invalid/team/app:v1"))


def test_verification_requires_fresh_scoped_healthy_artifact_observation():
    req = _request()
    valid = _observation(req)
    assert verify_observation(req, valid)["verified"] is True
    assert verify_observation(req, _observation(req, observed_at=datetime.now(timezone.utc) - timedelta(minutes=20)))["checks"]["observation_fresh"] is False
    wrong_revision = verify_observation(req, _observation(req, observed_revision="other"))
    assert wrong_revision["verified"] is False and wrong_revision["checks"]["revision_match"] is False
    wrong_artifact = verify_observation(req, _observation(req, observed_digest="sha256:" + "c" * 64))
    assert wrong_artifact["verified"] is False and wrong_artifact["checks"]["approved_digest_match"] is False
    unhealthy = verify_observation(req, _observation(req, health_state="unhealthy"))
    assert unhealthy["verified"] is False and unhealthy["checks"]["runtime_healthy"] is False
    wrong_tenant = verify_observation(req, _observation(req, observed_tenant_id="other"))
    assert wrong_tenant["verified"] is False and wrong_tenant["checks"]["tenant_match"] is False
    wrong_environment = verify_observation(req, _observation(req, observed_environment_id="other"))
    assert wrong_environment["verified"] is False and wrong_environment["checks"]["environment_match"] is False


def test_no_observation_or_boolean_claim_cannot_advance_verification():
    req = _request()
    fake = _observation(req, observed=False, reference=None)
    assert verify_observation(req, fake)["verified"] is False
    with pytest.raises(ValueError):
        advance_verification_state("applying", "deployment_observed", observation=fake)
    with pytest.raises(ValueError):
        advance_verification_state("deployment_observed", "runtime_verified", verdict={"verified": True})
    valid = _observation(req)
    verdict = verify_observation(req, valid)
    assert advance_verification_state("applying", "deployment_observed", observation=valid) == "deployment_observed"
    assert advance_verification_state("deployment_observed", "runtime_verified", observation=valid, verdict=verdict) == "runtime_verified"
    with pytest.raises(ValueError):
        advance_verification_state("queued", "runtime_verified", observation=valid, verdict=verdict)
