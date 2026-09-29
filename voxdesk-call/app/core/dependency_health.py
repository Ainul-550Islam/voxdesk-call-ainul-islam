"""Canonical safe dependency configuration/capability inventory."""
from __future__ import annotations

from app.core.config import settings
from app.providers.compatibility import capability_matrix, detect_provider


def _state(configured: bool, installed: bool | None = None, capable: bool | None = None) -> str:
    if not configured:
        return "not_configured"
    if installed is False:
        return "unavailable"
    if capable is False:
        return "degraded"
    return "not_verified"


def configuration_health() -> dict:
    """Return separated config/install/capability/reachability facts; never return secrets."""
    providers = capability_matrix()
    configured = {
        "database": bool((settings.database_url or "").strip()),
        "redis": bool((settings.redis_url or "").strip()),
        "deepgram": bool((settings.deepgram_api_key or "").strip()),
        "elevenlabs": bool((settings.elevenlabs_api_key or "").strip()),
        "llm": providers["llm"]["configured"],
        "kubernetes_cluster_reference": bool((settings.kubernetes_cluster_reference or "").strip()),
        "container_runtime_enabled": bool(settings.container_runtime_enabled),
        "airgap_manifest_configured": bool(settings.airgap_manifest_directory and settings.airgap_public_key_base64),
        "artifact_registry_configured": bool(settings.artifact_registry_url),
        "object_storage": settings.knowledge_storage_backend == "local" or bool(settings.knowledge_s3_bucket),
    }
    sdk_capabilities = {name: providers[name] for name in ("deepgram", "openai", "anthropic", "google", "elevenlabs", "llm")}
    kube = detect_provider("kubernetes.client", "kubernetes", capabilities={"apps_v1", "core_v1"}, required_symbol="AppsV1Api")
    container = detect_provider("docker", "docker", capabilities={"container_inspection"}, required_symbol="from_env")
    deployment = {
        "kubernetes": {"configured": configured["kubernetes_cluster_reference"], "supported": kube.capable, "sdk_version": kube.sdk_version, "reachable": "not_checked"},
        "container": {"configured": configured["container_runtime_enabled"], "supported": container.capable, "sdk_version": container.sdk_version, "reachable": "not_checked"},
        "airgap": {"configured": configured["airgap_manifest_configured"], "supported": True, "reachable": "not_applicable"},
        "registry": {"configured": configured["artifact_registry_configured"], "supported": True, "reachable": "not_checked"},
    }
    return {
        "configured": configured,
        "sdk_capabilities": sdk_capabilities,
        "states": {
            name: _state(data["configured"], data["installed"], data["capable"])
            for name, data in sdk_capabilities.items()
        },
        "deployment": deployment,
        "provider_reachability_checked": False,
        "storage_reachability_checked": settings.knowledge_storage_backend != "local",
    }
