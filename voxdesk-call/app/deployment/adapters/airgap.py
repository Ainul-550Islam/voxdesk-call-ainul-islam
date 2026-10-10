"""Ed25519-signed offline package manifest verification; never claims installation."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from app.deployment.adapters.base import DeploymentObservation, DeploymentRequest


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 71 and value.startswith("sha256:") and all(ch in "0123456789abcdef" for ch in value[7:])


def _verify_bundle_file(root: Path, item: object) -> bool:
    if not isinstance(item, dict) or not isinstance(item.get("file"), str) or not _valid_digest(item.get("digest")):
        return False
    try:
        root = root.resolve(strict=True)
        path = (root / item["file"]).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            return False
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() == item["digest"]
    except (OSError, ValueError):
        return False


class AirGapAdapter:
    name = "airgap"
    version = "1"

    def __init__(self, *, manifest_path: str | None = None, manifest_directory: str | None = None, public_key: bytes | None = None):
        self.manifest_path = manifest_path
        self.manifest_directory = manifest_directory
        self.public_key = public_key

    def _manifest(self, request: DeploymentRequest) -> Path | None:
        if self.manifest_path:
            return Path(self.manifest_path)
        if not self.manifest_directory:
            return None
        try:
            import uuid
            revision_id = uuid.UUID(request.revision_id)
            root = Path(self.manifest_directory).resolve(strict=True)
            candidate = (root / f"{revision_id}.json").resolve(strict=True)
            if candidate.parent != root or not candidate.is_file():
                return None
            return candidate
        except (OSError, ValueError):
            return None

    def _verify_signature(self, payload: dict, encoded_signature: str) -> bool:
        if self.public_key is None:
            return False
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            signature = base64.b64decode(encoded_signature, validate=True)
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            Ed25519PublicKey.from_public_bytes(self.public_key).verify(signature, canonical)
            return True
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            return False

    async def inspect(self, request: DeploymentRequest) -> DeploymentObservation:
        if not request.offline:
            return DeploymentObservation(self.name, False, False, False, "invalid_mode", reason="air-gap adapter requires explicit offline mode", adapter_version=self.version)
        if (not self.manifest_path and not self.manifest_directory) or self.public_key is None:
            return DeploymentObservation(self.name, False, False, False, "unavailable", reason="local package manifest and configured Ed25519 public key are required", adapter_version=self.version)
        path = self._manifest(request)
        if path is None:
            return DeploymentObservation(self.name, False, False, False, "manifest_invalid", reason="offline manifest path is missing or outside its configured directory", adapter_version=self.version)
        try:
            raw = path.read_bytes()
            manifest = json.loads(raw)
        except (OSError, ValueError):
            return DeploymentObservation(self.name, False, False, False, "manifest_invalid", reason="offline manifest is unavailable or invalid", adapter_version=self.version)
        if not isinstance(manifest, dict):
            return DeploymentObservation(self.name, False, False, False, "manifest_invalid", reason="offline manifest must contain a JSON object", adapter_version=self.version)

        signature = manifest.get("signature")
        payload = {key: value for key, value in manifest.items() if key != "signature"}
        signature_valid = isinstance(signature, str) and self._verify_signature(payload, signature)
        digest = payload.get("artifact_digest")
        digest_matches = digest == request.artifact_digest
        fingerprint_matches = payload.get("manifest_fingerprint") == request.manifest_fingerprint
        scope_matches = all(payload.get(key) == value for key, value in {
            "tenant_id": request.tenant_id,
            "organization_id": request.organization_id,
            "environment_id": request.environment_id,
            "target_id": request.target_id,
            "revision_id": request.revision_id,
        }.items())
        dependency = payload.get("dependency_bundle")
        update_bundle = payload.get("update_bundle")
        packages = payload.get("offline_packages")
        network = payload.get("network_restrictions")
        provider_mode = payload.get("provider_mode")
        schema_valid = payload.get("schema_version") == "voxdesk.airgap.v1"
        dependency_valid = _verify_bundle_file(path.parent, dependency)
        package_files_valid = isinstance(packages, list) and bool(packages) and all(
            isinstance(item, dict) and isinstance(item.get("name"), str) and _verify_bundle_file(path.parent, item)
            for item in packages
        )
        network_valid = isinstance(network, dict) and network.get("mode") in {"isolated", "offline"} and network.get("egress_allowed") is False
        update_valid = _verify_bundle_file(path.parent, update_bundle)
        provider_mode_valid = provider_mode in {"offline", "local_only", "included", "disabled"}
        integrity_verified = bool(
            signature_valid and schema_valid and dependency_valid and package_files_valid
            and network_valid and update_valid and provider_mode_valid
            and digest_matches and fingerprint_matches and scope_matches
        )
        package_count = len(packages) if isinstance(packages, list) else 0
        return DeploymentObservation(
            self.name, True, False, False,
            "package_verified" if integrity_verified else "package_unverified",
            reference=path.name,
            observed_digest=digest if digest_matches else None,
            observed_fingerprint=payload.get("manifest_fingerprint") if fingerprint_matches else None,
            checks={
                "signature_valid": signature_valid,
                "manifest_schema_valid": schema_valid,
                "dependency_bundle_valid": dependency_valid,
                "offline_packages_valid": package_files_valid,
                "network_restrictions_valid": network_valid,
                "update_bundle_valid": update_valid,
                "provider_mode_valid": provider_mode_valid,
                "artifact_digest_match": digest_matches,
                "manifest_fingerprint_match": fingerprint_matches,
                "deployment_scope_match": scope_matches,
                "runtime_deployment_observed": False,
                "installed": False,
            },
            evidence={"manifest_sha256": hashlib.sha256(raw).hexdigest(), "offline_package_count": str(package_count)},
            reason="offline package integrity is not proof of installation or runtime state" if integrity_verified else "offline manifest signature, schema, bundle, scope, or artifact identity did not verify",
            adapter_version=self.version,
            observed_revision=request.revision_id if scope_matches else None,
            observed_artifact=str(digest) if digest_matches else None,
            health_state="not_applicable",
            observed_tenant_id=request.tenant_id if scope_matches else None,
            observed_organization_id=request.organization_id if scope_matches else None,
            observed_environment_id=request.environment_id if scope_matches else None,
            observed_target_id=request.target_id if scope_matches else None,
        )

    async def deploy(self, request: DeploymentRequest) -> DeploymentObservation:
        return DeploymentObservation(self.name, False, False, False, "manual_install_required", reason="air-gap adapter validates package evidence only; a human must install and provide authoritative runtime observations", adapter_version=self.version)

    async def preflight(self, request: DeploymentRequest) -> DeploymentObservation:
        return await self.inspect(request)

    async def apply(self, request: DeploymentRequest) -> DeploymentObservation:
        return await self.deploy(request)

    async def status(self, request: DeploymentRequest) -> DeploymentObservation:
        return await self.inspect(request)

    async def verify(self, request: DeploymentRequest) -> DeploymentObservation:
        return await self.inspect(request)
