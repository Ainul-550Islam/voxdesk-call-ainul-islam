from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
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
from app.providers.compatibility import capability_matrix, detect_deepgram, require_deepgram
from app.providers.errors import ProviderCompatibilityError, ProviderDependencyMissingError


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


def test_dependency_health_module_imports_in_a_clean_process():
    """The dependency health module must not rely on app.main import order."""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from typing import get_type_hints; "
            "from app.observability.health import dependency_health; "
            "from app.jobs.types import register_handler_function; "
            "from app.tenancy.isolation import require_same_tenant; "
            "assert callable(dependency_health); "
            "assert get_type_hints(register_handler_function)['handler']; "
            "assert get_type_hints(require_same_tenant)['actor']; "
            "print('IMPORT_OK')",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().endswith("IMPORT_OK")


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


def test_provider_matrix_separates_config_install_capability_and_reachability(monkeypatch):
    from app.providers import compatibility

    def unexpected_sdk_probe(*args, **kwargs):
        raise AssertionError("an inventory must not import provider SDKs")

    monkeypatch.setattr(compatibility, "capabilities_for", unexpected_sdk_probe)
    matrix = capability_matrix()
    for provider in ("deepgram", "llm", "elevenlabs"):
        report = matrix[provider]
        assert {"configured", "installed", "capable", "reachable", "authenticated"} <= report.keys()
        assert report["reachable"] == "not_checked"
        assert report["authenticated"] == "not_checked"
    # An installed SDK is not equivalent to API-surface proof, reachability,
    # or successful authentication. If the distribution is absent, inability
    # to use that provider is already known; otherwise capability is unknown.
    expected_capable = None if matrix["deepgram"]["installed"] else False
    assert matrix["deepgram"]["capable"] is expected_capable
    assert matrix["deepgram"]["api_surface"] == "not_checked"
    assert matrix["deepgram"]["reachable"] != "available"


def test_configuration_validation_and_health_are_secret_safe(monkeypatch):
    monkeypatch.setattr(settings, "deepgram_api_key", "prompt7-secret-sentinel")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    report = configuration_health()
    assert report["configured"]["deepgram"] is True
    assert report["sdk_capabilities"]["deepgram"]["capable"] is None
    assert report["provider_reachability_checked"] is False
    assert "prompt7-secret-sentinel" not in repr(report)
    matrix = runtime_configuration_matrix(strict=False)
    assert "valid" in matrix and "groups" in matrix and matrix["valid"]
    assert validate_runtime_config(strict=False) == []


def test_dependency_health_does_not_import_optional_sdk_surfaces(monkeypatch):
    import builtins
    from app.providers import compatibility

    def unexpected_probe(*args, **kwargs):
        raise AssertionError("the request-safe health inventory must not import SDK surfaces")

    real_import = builtins.__import__

    def reject_provider_import(name, *args, **kwargs):
        if name == "deepgram" or name.startswith("deepgram.") or name == "pipecat" or name.startswith("pipecat."):
            raise AssertionError(f"provider SDK import attempted by health configuration: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_provider_import)
    monkeypatch.setattr(compatibility, "capability_matrix", unexpected_probe)
    monkeypatch.setattr(compatibility, "capabilities_for", unexpected_probe)
    distribution = compatibility.distribution_state("deepgram").as_dict()
    assert distribution["provider"] == "deepgram"
    assert distribution["runtime_probe"] == "not_performed"
    report = configuration_health()
    assert report["sdk_capabilities"]["deepgram"]["api_surface"] == "not_checked"
    assert report["sdk_capabilities"]["deepgram"]["capable"] is None
    assert report["sdk_capabilities"]["deepgram"]["reachable"] == "not_checked"
    assert report["sdk_capabilities"]["deepgram"]["authenticated"] == "not_checked"


def test_stt_missing_credentials_raise_typed_error_not_fake_transcript(monkeypatch):
    monkeypatch.setattr(settings, "deepgram_api_key", "")
    with pytest.raises(ProviderConfigurationError):
        stt.build_stt(SimpleNamespace(language="en-US"))
    report = detect_deepgram()
    if report.capable:
        service, options = require_deepgram()
        assert service is not None and options is not None
    elif report.installed:
        with pytest.raises(ProviderCompatibilityError):
            require_deepgram()
    else:
        with pytest.raises(ProviderDependencyMissingError):
            require_deepgram()


def test_no_fake_deployment_verification_from_client_boolean():
    request = DeploymentRequest("tenant", "org", "env", "target", "revision", "byoc", "image:v1", "sha256:" + "a" * 64, "b" * 64)
    client_claim = DeploymentObservation("client", True, True, True, "ready", reference="client-assertion", observed_digest=request.artifact_digest, observed_fingerprint=request.manifest_fingerprint, observed_revision=request.revision_id, observed_artifact=request.artifact_reference, health_state="healthy")
    result = verify_observation(request, client_claim)
    assert not result["verified"]
    assert result["checks"]["tenant_match"] is False
    assert result["checks"]["environment_match"] is False


def test_dashboard_security_settings_is_wired_to_live_identity_apis():
    root = Path(__file__).resolve().parents[1]
    app_source = (root / "dashboard/src/App.jsx").read_text(encoding="utf-8")
    page_source = (root / "dashboard/src/pages/security/EnterpriseSecuritySettings.jsx").read_text(encoding="utf-8")
    api_source = (root / "dashboard/src/lib/api.js").read_text(encoding="utf-8")

    assert "pattern: '/security-settings'" in app_source
    assert "getIdentityPolicy" in page_source
    assert "listSecuritySessions" in page_source
    assert "listSecurityApiKeys" in page_source
    assert "updateIdentityPolicy" in page_source
    assert "revokeSecuritySession" in page_source
    assert "rotateSecurityApiKey" in page_source
    assert "/api/v1/enterprise-security/posture" in api_source
    assert "No certification" in page_source
    assert "compliance status is inferred" in page_source
