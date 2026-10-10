"""Kubernetes AppsV1 observer/deployer; absent credentials never become success."""
from __future__ import annotations

import asyncio
from app.deployment.adapters.base import (
    DeploymentObservation,
    DeploymentRequest,
    runtime_image_digest,
)


class KubernetesAdapter:
    name = "kubernetes"

    def __init__(self, *, cluster_reference: str | None = None, apps_api=None, core_api=None):
        self.cluster_reference = cluster_reference
        self._apps_api, self._core_api = apps_api, core_api

    def _apis(self):
        if not self.cluster_reference:
            return None
        if self._apps_api is not None and self._core_api is not None:
            return self._apps_api, self._core_api
        try:
            from kubernetes import client, config
        except ImportError:
            return None
        try:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            return None
        active_host = client.Configuration.get_default_copy().host.rstrip("/")
        if active_host != self.cluster_reference.rstrip("/"):
            return None
        self._apps_api, self._core_api = client.AppsV1Api(), client.CoreV1Api()
        return self._apps_api, self._core_api

    async def inspect(self, request: DeploymentRequest) -> DeploymentObservation:
        if not self.cluster_reference or request.cluster_reference != self.cluster_reference:
            return DeploymentObservation(self.name, False, False, False, "unavailable", reason="server-configured Kubernetes cluster does not match the scoped target")
        apis = self._apis()
        if apis is None:
            return DeploymentObservation(self.name, False, False, False, "unavailable", reason="Kubernetes client/configuration is unavailable")
        apps, core = apis
        namespace, name = request.namespace or "default", request.resource_name or request.target_id
        try:
            deployment = await asyncio.to_thread(apps.read_namespaced_deployment, name, namespace)
            pods = await asyncio.to_thread(core.list_namespaced_pod, namespace, label_selector=f"app={name}")
        except Exception as exc:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            status = getattr(exc, "status", None)
            if status == 404:
                return DeploymentObservation(self.name, True, False, False, "not_found", reference=f"{namespace}/{name}", reason="deployment resource was not found")
            return DeploymentObservation(self.name, False, False, False, "observation_failed", reason="Kubernetes API observation failed")
        spec = deployment.spec
        status = deployment.status
        desired = int(spec.replicas or 0)
        ready = int(status.ready_replicas or 0)
        generation = int(deployment.metadata.generation or 0)
        observed_generation = int(status.observed_generation or 0)
        pod_items = list(pods.items or [])
        expected_labels = {"voxdesk.ai/tenant-id": request.tenant_id, "voxdesk.ai/organization-id": request.organization_id, "voxdesk.ai/environment-id": request.environment_id, "voxdesk.ai/target-id": request.target_id}
        current_labels = spec.template.metadata.labels or {}
        scope_match = all(current_labels.get(key) == value for key, value in expected_labels.items())
        pod_scope_match = not pod_items or all(all((pod.metadata.labels or {}).get(key) == value for key, value in expected_labels.items()) for pod in pod_items)
        if not scope_match or not pod_scope_match:
            return DeploymentObservation(self.name, True, False, False, "scope_mismatch", checks={"resource_scope_match": False}, reason="runtime resource is not bound to the requested tenant/environment scope")
        pod_ready = bool(pod_items) and len(pod_items) >= desired and all((p.status and p.status.phase == "Running") and bool(p.status.container_statuses) and all(c.ready for c in p.status.container_statuses) for p in pod_items)
        image_ids = [str(c.image_id or "") for pod in pod_items for c in (pod.status.container_statuses or [])]
        workload_images = [str(c.image or "") for c in (spec.template.spec.containers or [])]
        artifact_reference_match = bool(workload_images) and all(value == request.artifact_reference for value in workload_images)
        digest_match = bool(image_ids) and all(
            runtime_image_digest(image_id) == request.artifact_digest for image_id in image_ids
        )
        pod_fingerprints = [((pod.metadata.annotations or {}).get("voxdesk.ai/manifest-fingerprint")) for pod in pod_items]
        annotations = spec.template.metadata.annotations or {}
        revision_match = annotations.get("voxdesk.ai/revision-id") == request.revision_id
        fingerprint_match = bool(pod_fingerprints) and all(value == request.manifest_fingerprint for value in pod_fingerprints)
        observed = bool(deployment.metadata.uid)
        generation_ready = generation > 0 and observed_generation >= generation
        deployed = observed and desired > 0 and ready == desired and pod_ready and generation_ready
        health_state = "healthy" if pod_ready and generation_ready else "unhealthy"
        verified = deployed and digest_match and artifact_reference_match and fingerprint_match and revision_match
        return DeploymentObservation(self.name, observed, deployed, verified, "ready" if verified else ("deployed_unverified" if deployed else "not_ready"), reference=f"{namespace}/{name}", observed_digest=request.artifact_digest if digest_match else None, observed_fingerprint=request.manifest_fingerprint if fingerprint_match else None, checks={"resource_exists": observed, "replicas_ready": ready == desired and desired > 0, "observed_generation_current": generation_ready, "pods_ready": pod_ready, "runtime_artifact_digest_match": digest_match, "artifact_reference_match": artifact_reference_match, "manifest_fingerprint_match": fingerprint_match, "revision_match": revision_match}, evidence={"uid": str(deployment.metadata.uid), "generation": str(generation), "observed_generation": str(observed_generation)}, reason=None if verified else "live pod readiness, runtime digest, generation, revision, or manifest fingerprint did not satisfy verification", observed_revision=request.revision_id if revision_match else None, observed_artifact=request.artifact_reference if artifact_reference_match else None, health_state=health_state, observed_tenant_id=request.tenant_id, observed_organization_id=request.organization_id, observed_environment_id=request.environment_id, observed_target_id=request.target_id)

    async def deploy(self, request: DeploymentRequest) -> DeploymentObservation:
        if not self.cluster_reference or request.cluster_reference != self.cluster_reference:
            return DeploymentObservation(self.name, False, False, False, "unavailable", reason="server-configured Kubernetes cluster does not match the scoped target")
        apis = self._apis()
        if apis is None:
            return DeploymentObservation(self.name, False, False, False, "unavailable", reason="Kubernetes client/configuration is unavailable")
        apps, _ = apis
        ns, name = request.namespace or "default", request.resource_name or request.target_id
        try:
            current = await asyncio.to_thread(apps.read_namespaced_deployment, name, ns)
            containers = current.spec.template.spec.containers or []
            if not containers:
                return DeploymentObservation(self.name, True, False, False, "invalid_template", reference=f"{ns}/{name}", reason="deployment has no container template")
            labels = current.spec.template.metadata.labels or {}
            expected_labels = {"voxdesk.ai/tenant-id": request.tenant_id, "voxdesk.ai/organization-id": request.organization_id, "voxdesk.ai/environment-id": request.environment_id, "voxdesk.ai/target-id": request.target_id}
            if not all(labels.get(key) == value for key, value in expected_labels.items()):
                return DeploymentObservation(self.name, True, False, False, "scope_mismatch", checks={"resource_scope_match": False}, reason="runtime resource is not bound to the requested tenant/environment scope")
            current.spec.template.spec.containers[0].image = request.artifact_reference
            annotations = current.spec.template.metadata.annotations or {}
            annotations["voxdesk.ai/manifest-fingerprint"] = request.manifest_fingerprint
            annotations["voxdesk.ai/revision-id"] = request.revision_id
            current.spec.template.metadata.annotations = annotations
            await asyncio.to_thread(apps.replace_namespaced_deployment, name, ns, current)
        except Exception as exc:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            if getattr(exc, "status", None) != 404:
                return DeploymentObservation(self.name, False, False, False, "deploy_failed", reference=f"{ns}/{name}", reason="Kubernetes deployment update failed")
            return DeploymentObservation(self.name, False, False, False, "not_found", reference=f"{ns}/{name}", reason="deployment resource must be provisioned before managed rollout")
        return await self.inspect(request)

    async def preflight(self, request: DeploymentRequest) -> DeploymentObservation:
        return await self.inspect(request)

    async def apply(self, request: DeploymentRequest) -> DeploymentObservation:
        return await self.deploy(request)

    async def status(self, request: DeploymentRequest) -> DeploymentObservation:
        return await self.inspect(request)

    async def verify(self, request: DeploymentRequest) -> DeploymentObservation:
        return await self.inspect(request)
