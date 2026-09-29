"""Container runtime observer backed by an injected Docker-compatible client."""
from __future__ import annotations
import asyncio
from app.deployment.adapters.base import (
    DeploymentObservation,
    DeploymentRequest,
    repo_digest_matches,
)


class ContainerAdapter:
    name = "container"

    def __init__(self, client=None, *, enabled: bool = False):
        self.client = client
        self.enabled = enabled

    def _client(self):
        if self.client is not None:
            return self.client
        if not self.enabled:
            return None
        try:
            import docker
        except ImportError:
            return None
        try:
            self.client = docker.from_env()
            self.client.ping()
        except Exception:
            return None
        return self.client

    async def inspect(self, request: DeploymentRequest) -> DeploymentObservation:
        client = self._client()
        if client is None:
            return DeploymentObservation(self.name, False, False, False, "unavailable", reason="Docker-compatible runtime is unavailable")
        try:
            container = await asyncio.to_thread(client.containers.get, request.resource_name or request.target_id)
            await asyncio.to_thread(container.reload)
        except Exception as exc:
            if type(exc).__name__ == "NotFound":
                return DeploymentObservation(self.name, True, False, False, "not_found", reason="container was not found")
            return DeploymentObservation(self.name, False, False, False, "observation_failed", reason="container runtime inspection failed")
        try:
            attrs = container.attrs or {}
            labels = ((attrs.get("Config", {}) or {}).get("Labels", {}) or {})
            expected_labels = {"voxdesk.ai/tenant-id": request.tenant_id, "voxdesk.ai/organization-id": request.organization_id, "voxdesk.ai/environment-id": request.environment_id, "voxdesk.ai/target-id": request.target_id}
            if not all(labels.get(key) == value for key, value in expected_labels.items()):
                return DeploymentObservation(self.name, False, False, False, "scope_mismatch", checks={"resource_scope_match": False}, reason="runtime resource is not bound to the requested tenant/environment scope")
            state = attrs.get("State", {})
            image_id = str(attrs.get("Image", ""))
            image = getattr(container, "image", None)
            image_attrs = getattr(image, "attrs", {}) or {}
            repo_digests = image_attrs.get("RepoDigests", []) or []
            digest_match = any(
                repo_digest_matches(request.artifact_reference, str(value), request.artifact_digest)
                for value in repo_digests
            )
            fingerprint = labels.get("voxdesk.ai/manifest-fingerprint")
            revision = labels.get("voxdesk.ai/revision-id")
            health = state.get("Health", {}) or {}
            health_state = "healthy" if health.get("Status") == "healthy" else "unhealthy"
        except Exception:
            return DeploymentObservation(self.name, False, False, False, "observation_failed", reason="container metadata could not be safely observed")
        fingerprint_match = fingerprint == request.manifest_fingerprint
        running = state.get("Running") is True and state.get("Status") == "running"
        observed = bool(getattr(container, "id", None))
        revision_match = revision == request.revision_id
        verified = observed and running and health_state == "healthy" and digest_match and fingerprint_match and revision_match
        return DeploymentObservation(self.name, observed, observed and running, verified, "ready" if verified else ("running_unverified" if running else "not_running"), reference=str(container.id), observed_digest=request.artifact_digest if digest_match else None, observed_fingerprint=fingerprint if fingerprint_match else None, checks={"container_exists": observed, "container_running": running, "container_healthy": health_state == "healthy", "runtime_artifact_digest_match": digest_match, "manifest_fingerprint_match": fingerprint_match, "revision_match": revision_match}, evidence={"container_id": str(container.id), "image_id": image_id}, reason=None if verified else "running state, health, immutable runtime digest, revision, or manifest fingerprint could not be verified", observed_revision=revision if revision_match else None, observed_artifact=request.artifact_reference if digest_match else None, health_state=health_state, observed_tenant_id=request.tenant_id, observed_organization_id=request.organization_id, observed_environment_id=request.environment_id, observed_target_id=request.target_id)

    async def deploy(self, request: DeploymentRequest) -> DeploymentObservation:
        client = self._client()
        if client is None:
            return DeploymentObservation(self.name, False, False, False, "unavailable", reason="Docker-compatible runtime is unavailable")
        # A safe, generic container replacement cannot be performed without deployment-specific
        # ports, mounts, secret references and restart policy. Inspection is not application.
        return DeploymentObservation(self.name, False, False, False, "not_supported", reason="generic container apply requires an approved deployment specification")

    async def preflight(self, request: DeploymentRequest) -> DeploymentObservation:
        return await self.inspect(request)

    async def apply(self, request: DeploymentRequest) -> DeploymentObservation:
        return await self.deploy(request)

    async def status(self, request: DeploymentRequest) -> DeploymentObservation:
        return await self.inspect(request)

    async def verify(self, request: DeploymentRequest) -> DeploymentObservation:
        return await self.inspect(request)
