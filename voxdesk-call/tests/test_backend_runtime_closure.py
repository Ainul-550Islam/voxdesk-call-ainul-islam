from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from app.agent.errors import ProviderConfigurationError
from app.agent import stt
from app.core.config import settings
from app.core.config_validation import runtime_configuration_matrix, validate_runtime_config
from app.core.dependency_health import configuration_health
from app.deployment.adapters.base import DeploymentObservation, DeploymentRequest
from app.deployment.verification import verify_observation
from app.jobs.registry import bootstrap
from app.jobs.models import PermanentJobError, RetryableJobError
from app.jobs.runtime import current_job_context, run_job_handler
from app.providers.compatibility import capability_matrix, require_deepgram


def test_application_routes_preserve_required_backend_surfaces():
    from app.main import app
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/health" in paths
    assert "/health/ready" in paths
    assert "/health/dependencies" in paths
    assert "/api/deployment/revisions/{revision_id}/preflight" in paths
    assert "/api/deployment/revisions/{revision_id}/apply" in paths
    assert "/api/compliance/frameworks" in paths or any(path.startswith("/api/compliance") for path in paths)
    assert any(path.startswith("/api/roi") for path in paths)
    assert any(path.startswith("/api/deployment") for path in paths)
    assert any(path.startswith("/api/review") for path in paths)
    assert any(path.startswith("/api/insight") for path in paths)
    assert any(path.startswith("/api/forecast") for path in paths)
    assert any(path.startswith("/api/specialized-agents") for path in paths)


def test_canonical_registry_bootstrap_and_persisted_job_context():
    handlers = bootstrap()
    assert "deployment" in handlers
    assert {"specialized.translation", "specialized.insight", "specialized.forecast", "specialized.anomaly"} <= handlers.keys()
    job = SimpleNamespace(
        id=__import__("uuid").uuid4(), tenant_id=__import__("uuid").uuid4(),
        organization_id=__import__("uuid").uuid4(), environment_id=__import__("uuid").uuid4(),
        job_type="specialized.translation", payload={"tenant_id": "wrong"}, attempt_count=1,
    )
    async def handler(_job):
        return None
    with pytest.raises(PermanentJobError, match="persisted scope"):
        asyncio.run(run_job_handler(job, handler))
    job.payload = {"request_id": "req-1", "trace_id": "trace-1", "execution_id": "exec-1"}
    async def inspect_context(_job):
        context = current_job_context()
        assert context.tenant_id == job.tenant_id
        assert context.environment_id == job.environment_id
        assert context.request_id == "req-1"
        return "ok"
    assert asyncio.run(run_job_handler(job, inspect_context)) == "ok"
    assert current_job_context() is None


def test_job_runtime_enforces_timeout_and_cleans_context():
    job = SimpleNamespace(id=__import__("uuid").uuid4(), tenant_id=__import__("uuid").uuid4(), organization_id=None, environment_id=None, job_type="test", payload={}, attempt_count=0)
    async def slow(_job):
        await asyncio.sleep(0.05)
    with pytest.raises(RetryableJobError, match="job execution exceeded"):
        asyncio.run(run_job_handler(job, slow, timeout_seconds=0.001))
    assert current_job_context() is None


def test_provider_matrix_separates_config_install_capability_and_reachability():
    matrix = capability_matrix()
    for provider in ("deepgram", "llm", "elevenlabs"):
        report = matrix[provider]
        assert {"configured", "installed", "capable", "reachable", "authenticated"} <= report.keys()
        assert report["reachable"] == "not_checked"
        assert report["authenticated"] == "not_checked"
    # Installed SDKs are not described as connected.
    assert matrix["deepgram"]["reachable"] != "available"


def test_configuration_validation_and_health_are_secret_safe(monkeypatch):
    monkeypatch.setattr(settings, "deepgram_api_key", "prompt7-secret-sentinel")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    report = configuration_health()
    assert report["configured"]["deepgram"] is True
    assert "prompt7-secret-sentinel" not in repr(report)
    matrix = runtime_configuration_matrix(strict=False)
    assert "valid" in matrix and "groups" in matrix and matrix["valid"]
    assert validate_runtime_config(strict=False) == []


def test_stt_missing_credentials_raise_typed_error_not_fake_transcript(monkeypatch):
    monkeypatch.setattr(settings, "deepgram_api_key", "")
    with pytest.raises(ProviderConfigurationError):
        stt.build_stt(SimpleNamespace(language="en-US"))
    service, options = require_deepgram()
    assert service is not None and options is not None


def test_no_fake_deployment_verification_from_client_boolean():
    request = DeploymentRequest("tenant", "org", "env", "target", "revision", "byoc", "image:v1", "sha256:" + "a" * 64, "b" * 64)
    client_claim = DeploymentObservation("client", True, True, True, "ready", reference="client-assertion", observed_digest=request.artifact_digest, observed_fingerprint=request.manifest_fingerprint, observed_revision=request.revision_id, observed_artifact=request.artifact_reference, health_state="healthy")
    result = verify_observation(request, client_claim)
    assert not result["verified"]
    assert result["checks"]["tenant_match"] is False
    assert result["checks"]["environment_match"] is False


def test_frontend_sources_remain_untouched():
    root = Path(__file__).resolve().parents[1]
    changed = subprocess.run(["git", "status", "--short", "--", "dashboard", "dashboard-next"], cwd=root, check=True, capture_output=True, text=True).stdout
    assert not changed.strip()
