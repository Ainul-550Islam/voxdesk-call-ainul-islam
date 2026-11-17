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


def test_console_security_settings_is_wired_to_live_identity_apis():
    """The workspace security surface, asserted against the shipped console.

    PART 17A retired the Vite app, and this test used to read three files inside
    it. The capability it guards did not disappear with those files, so the test
    follows the capability: identity policy, live sessions, API keys and the
    server-resolved posture are all reachable from
    `/dashboard/settings/identity`, through one typed client. A retirement that
    quietly dropped a page would fail here instead of passing by deletion.
    """
    root = Path(__file__).resolve().parents[1]
    identity_page = (root / "dashboard-next/app/dashboard/settings/identity/page.tsx").read_text(
        encoding="utf-8"
    )
    posture_panel = (root / "dashboard-next/components/identity/posture-panel.tsx").read_text(
        encoding="utf-8"
    )
    api_keys_page = (
        root / "dashboard-next/app/dashboard/settings/identity/api-keys/page.tsx"
    ).read_text(encoding="utf-8")
    identity_client = (root / "dashboard-next/lib/identity.ts").read_text(encoding="utf-8")

    # The page composes the panels that own workspace-wide controls.
    assert "PolicyPanel" in identity_page
    assert "PosturePanel" in identity_page

    # Endpoints, at the client where they are declared.
    assert "/api/identity/policy" in identity_client
    assert "/api/sessions" in identity_client
    assert "/api/api-keys" in identity_client
    assert "/security/posture" in identity_client

    # Rotation and revocation are wired, not merely listed.
    assert "rotateApiKey" in identity_client
    assert "revokeApiKey" in identity_client
    assert "revokeSession" in identity_client

    # The posture read is tenant-scoped from the server's own answer, never from
    # anything the client typed.
    assert "identityApi.posture(tenantId" in posture_panel
    assert "api.me()" in posture_panel

    # No certification is claimed. A config boolean is not an attestation.
    assert "not a compliance certification" in posture_panel
    assert "no attestation is claimed" in posture_panel

    # The API-key surface says what the server can actually do with a secret.
    assert "never read back" in api_keys_page

    # And the Vite tree these assertions used to read is gone for good.
    assert not (root / "dashboard/src").exists()


def test_retired_vite_dashboard_is_not_referenced_by_shipped_console():
    """The shipped console may only touch the vendored tree through a generator."""
    root = Path(__file__).resolve().parents[1]
    for path in (root / "dashboard-next" / "app").rglob("*.tsx"):
        text = path.read_text(encoding="utf-8", errors="replace")
        relative = path.relative_to(root).as_posix()
        # A comment that records provenance ("Source: dashboard/src/...") is
        # documentation and is welcome. An *import* is a dependency on a tree that
        # no longer exists, which is the thing that must be impossible.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            assert "dashboard/src" not in stripped, (
                f"{relative} imports from the retired Vite tree: {stripped[:80]}"
            )
        if "@/vite-port/src" in text:
            assert "Generated by scripts/port_vite_pages.py" in text, (
                f"{relative} imports the vendored tree without the generator header"
            )
