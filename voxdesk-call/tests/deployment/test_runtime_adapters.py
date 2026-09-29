from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.deployment.adapters.airgap import AirGapAdapter
from app.deployment.adapters.base import DeploymentAdapter, DeploymentObservation, DeploymentRequest
from app.deployment.adapters.container import ContainerAdapter
from app.deployment.adapters.kubernetes import KubernetesAdapter
from app.deployment.adapters.registry import ArtifactRegistryAdapter, OCIRegistryClient
from app.deployment.verification import verify_observation


def request(**changes):
    values = dict(
        tenant_id="t", organization_id="o", environment_id="e", target_id="app",
        revision_id="r", target_type="air_gapped", artifact_reference="registry.example/team/app:v1",
        artifact_digest="sha256:" + "a" * 64, manifest_fingerprint="b" * 64,
        cluster_reference="https://cluster.example", offline=True,
    )
    values.update(changes)
    return DeploymentRequest(**values)


def _scope_labels(req):
    return {
        "voxdesk.ai/tenant-id": req.tenant_id,
        "voxdesk.ai/organization-id": req.organization_id,
        "voxdesk.ai/environment-id": req.environment_id,
        "voxdesk.ai/target-id": req.target_id,
    }


def _airgap_payload(req, root):
    files = {"dependency_bundle": ("dependency.tar", b"dependency archive"), "offline_package": ("runtime.whl", b"offline package"), "update_bundle": ("update.tar", b"update archive")}
    entries = {}
    for name, (filename, content) in files.items():
        (root / filename).write_bytes(content)
        entries[name] = {"file": filename, "digest": "sha256:" + __import__("hashlib").sha256(content).hexdigest()}
    return {
        "schema_version": "voxdesk.airgap.v1",
        "tenant_id": req.tenant_id,
        "organization_id": req.organization_id,
        "environment_id": req.environment_id,
        "target_id": req.target_id,
        "revision_id": req.revision_id,
        "artifact_digest": req.artifact_digest,
        "manifest_fingerprint": req.manifest_fingerprint,
        "dependency_bundle": entries["dependency_bundle"],
        "offline_packages": [{"name": "runtime.whl", **entries["offline_package"]}],
        "network_restrictions": {"mode": "isolated", "egress_allowed": False},
        "update_bundle": entries["update_bundle"],
        "provider_mode": "offline",
    }


def _write_signed_manifest(path, payload, key):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    manifest = payload | {"signature": base64.b64encode(key.sign(canonical)).decode()}
    path.write_text(json.dumps(manifest))


def test_adapter_protocol_and_observation_contract():
    assert all(hasattr(DeploymentAdapter, name) for name in ("preflight", "apply", "status", "verify"))
    item = DeploymentObservation("test", False, False, False, "unavailable")
    assert item.status == "NOT_AVAILABLE"
    assert item.as_dict()["verified"] is False


def test_signed_airgap_manifest_is_integrity_only(tmp_path):
    req = request()
    key = Ed25519PrivateKey.generate()
    payload = _airgap_payload(req, tmp_path)
    path = tmp_path / "manifest.json"
    _write_signed_manifest(path, payload, key)
    public_key = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    result = asyncio.run(AirGapAdapter(manifest_path=str(path), public_key=public_key).inspect(req))
    assert result.state == "package_verified"
    assert result.checks["manifest_schema_valid"] is True
    assert result.checks["runtime_deployment_observed"] is False
    assert result.status != "VERIFIED"
    assert not result.deployed and not result.verified


def test_airgap_invalid_signature_and_wrong_identity_fail_closed(tmp_path):
    req = request()
    key, other = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    payload = _airgap_payload(req, tmp_path)
    path = tmp_path / "manifest.json"
    _write_signed_manifest(path, payload, key)
    public_key = other.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    invalid_signature = asyncio.run(AirGapAdapter(manifest_path=str(path), public_key=public_key).inspect(req))
    assert invalid_signature.checks["signature_valid"] is False
    assert invalid_signature.verified is False

    wrong_scope = payload | {"environment_id": "another-environment"}
    _write_signed_manifest(path, wrong_scope, key)
    public_key = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    mismatch = asyncio.run(AirGapAdapter(manifest_path=str(path), public_key=public_key).inspect(req))
    assert mismatch.checks["deployment_scope_match"] is False
    assert mismatch.state == "package_unverified"


def test_airgap_missing_offline_prerequisite_and_invalid_mode(tmp_path):
    req = request()
    key = Ed25519PrivateKey.generate()
    payload = _airgap_payload(req, tmp_path)
    payload["network_restrictions"] = {"mode": "online", "egress_allowed": True}
    path = tmp_path / "manifest.json"
    _write_signed_manifest(path, payload, key)
    public_key = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    result = asyncio.run(AirGapAdapter(manifest_path=str(path), public_key=public_key).inspect(req))
    assert result.checks["network_restrictions_valid"] is False
    assert result.verified is False
    wrong_mode = asyncio.run(AirGapAdapter(manifest_path=str(path), public_key=public_key).inspect(request(offline=False)))
    assert wrong_mode.state == "invalid_mode"


def test_container_digest_mismatch_or_missing_health_never_verifies():
    req = request(target_type="on_prem", offline=False)
    attrs = {"RepoDigests": ["registry.example/team/app@" + req.artifact_digest]}
    labels = _scope_labels(req) | {"voxdesk.ai/manifest-fingerprint": req.manifest_fingerprint, "voxdesk.ai/revision-id": req.revision_id}
    container = SimpleNamespace(id="container-1", image=SimpleNamespace(attrs=attrs), attrs={"Image": "image-id", "State": {"Running": True, "Status": "running", "Health": {"Status": "healthy"}}, "Config": {"Labels": labels}}, reload=lambda: None)
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container))
    result = asyncio.run(ContainerAdapter(client).inspect(req))
    assert result.deployed and result.verified and result.health_state == "healthy"

    attrs["RepoDigests"] = ["registry.example/team/app@sha256:" + "d" * 64]
    mismatch = asyncio.run(ContainerAdapter(client).inspect(req))
    assert mismatch.deployed and not mismatch.verified
    assert mismatch.checks["runtime_artifact_digest_match"] is False

    # A valid digest appearing only as a prefix of a malformed value is not
    # authoritative image identity. Repository identity is exact as well.
    attrs["RepoDigests"] = ["registry.example/team/app@" + req.artifact_digest + "suffix"]
    malformed = asyncio.run(ContainerAdapter(client).inspect(req))
    assert malformed.deployed and not malformed.verified
    attrs["RepoDigests"] = ["attacker.example/team/app@" + req.artifact_digest]
    wrong_repository = asyncio.run(ContainerAdapter(client).inspect(req))
    assert wrong_repository.deployed and not wrong_repository.verified

    attrs["RepoDigests"] = ["registry.example/team/app@" + req.artifact_digest]
    container.attrs["State"].pop("Health")
    unhealthy = asyncio.run(ContainerAdapter(client).inspect(req))
    assert unhealthy.deployed and not unhealthy.verified
    assert unhealthy.health_state == "unhealthy"


def test_container_tenant_environment_revision_and_apply_are_fail_closed():
    req = request(target_type="on_prem", offline=False)
    labels = _scope_labels(req) | {"voxdesk.ai/manifest-fingerprint": req.manifest_fingerprint, "voxdesk.ai/revision-id": req.revision_id}
    container = SimpleNamespace(id="container-1", image=SimpleNamespace(attrs={"RepoDigests": ["x@" + req.artifact_digest]}), attrs={"Image": "img", "State": {"Running": True, "Status": "running", "Health": {"Status": "healthy"}}, "Config": {"Labels": labels}}, reload=lambda: None)
    client = SimpleNamespace(containers=SimpleNamespace(get=lambda _name: container))
    labels["voxdesk.ai/environment-id"] = "other"
    mismatch = asyncio.run(ContainerAdapter(client).inspect(req))
    assert mismatch.state == "scope_mismatch" and not mismatch.verified
    apply_result = asyncio.run(ContainerAdapter(client).apply(req))
    assert apply_result.state == "not_supported" and not apply_result.deployed


def test_kubernetes_missing_client_reports_unavailable():
    result = asyncio.run(KubernetesAdapter().inspect(request(target_type="byoc", offline=False)))
    assert result.state == "unavailable"
    assert not result.deployed and not result.verified


def test_kubernetes_live_observation_requires_generation_pods_digest_revision_and_scope():
    req = request(target_type="byoc", offline=False)
    labels = _scope_labels(req)
    pod = SimpleNamespace(
        metadata=SimpleNamespace(labels=labels, annotations={"voxdesk.ai/manifest-fingerprint": req.manifest_fingerprint}),
        status=SimpleNamespace(phase="Running", container_statuses=[SimpleNamespace(ready=True, image_id="image@" + req.artifact_digest)]),
    )
    deployment = SimpleNamespace(
        metadata=SimpleNamespace(uid="uid-1", generation=3),
        spec=SimpleNamespace(replicas=1, template=SimpleNamespace(metadata=SimpleNamespace(labels=labels, annotations={"voxdesk.ai/revision-id": req.revision_id}), spec=SimpleNamespace(containers=[SimpleNamespace(image=req.artifact_reference)]))),
        status=SimpleNamespace(ready_replicas=1, observed_generation=3),
    )
    apps = SimpleNamespace(read_namespaced_deployment=lambda *_: deployment)
    core = SimpleNamespace(list_namespaced_pod=lambda *_args, **_kwargs: SimpleNamespace(items=[pod]))
    result = asyncio.run(KubernetesAdapter(cluster_reference=req.cluster_reference, apps_api=apps, core_api=core).inspect(req))
    assert result.observed and result.deployed and result.verified
    assert result.observed_revision == req.revision_id
    assert result.health_state == "healthy"

    pod.status.container_statuses[0].image_id += "suffix"
    malformed = asyncio.run(KubernetesAdapter(cluster_reference=req.cluster_reference, apps_api=apps, core_api=core).inspect(req))
    assert malformed.deployed and not malformed.verified
    assert malformed.checks["runtime_artifact_digest_match"] is False


def test_registry_declared_versus_verified_and_unavailable():
    req = request()
    no_registry = asyncio.run(ArtifactRegistryAdapter().resolve(req.artifact_reference, req.artifact_digest))
    assert no_registry.available is False
    assert no_registry.immutable is False

    class Registry:
        async def resolve_digest(self, _reference):
            return req.artifact_digest

    match = asyncio.run(ArtifactRegistryAdapter(Registry()).resolve(req.artifact_reference, req.artifact_digest))
    assert match.exists and match.immutable

    class Missing:
        async def resolve_digest(self, _reference):
            return None

    missing = asyncio.run(ArtifactRegistryAdapter(Missing()).resolve(req.artifact_reference, req.artifact_digest))
    assert missing.available and not missing.exists and not missing.immutable


def test_oci_client_pins_host_and_digest_header():
    class Response:
        status_code = 200
        headers = {"Docker-Content-Digest": "sha256:" + "a" * 64}
        def raise_for_status(self):
            return None
    class Client:
        async def head(self, url, *, headers):
            assert url == "https://registry.example/v2/team/app/manifests/v1"
            assert headers["Authorization"] == "Bearer private-token"
            return Response()
    client = OCIRegistryClient("https://registry.example", bearer_token="private-token", client=Client())
    assert asyncio.run(client.resolve_digest("registry.example/team/app:v1")) == "sha256:" + "a" * 64
    try:
        asyncio.run(client.resolve_digest("attacker.invalid/team/app:v1"))
    except ValueError:
        pass
    else:
        raise AssertionError("cross-registry reference was accepted")


def test_client_boolean_alone_cannot_establish_verified_state():
    req = request(target_type="byoc", offline=False)
    fake = DeploymentObservation("fake", True, True, True, "ready", reference="runtime", observed_digest=req.artifact_digest, observed_fingerprint=req.manifest_fingerprint, observed_revision=req.revision_id, observed_artifact=req.artifact_reference, health_state="healthy")
    result = verify_observation(req, fake)
    assert result["verified"] is False
    assert result["checks"]["tenant_match"] is False
    assert result["checks"]["environment_match"] is False
