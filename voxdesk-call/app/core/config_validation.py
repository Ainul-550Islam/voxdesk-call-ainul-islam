"""Secret-safe startup validation and explicit configuration-state reporting."""
from __future__ import annotations

from dataclasses import dataclass
import base64
from pathlib import Path
from urllib.parse import urlsplit

from app.core.config import settings
from app.core.dependency_health import configuration_health
from app.providers.compatibility import capabilities_for, detect_provider


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    component: str
    message: str


def _state(configured: bool, *, required: bool) -> str:
    if configured:
        return "configured"
    return "not_configured" if required else "optional_unconfigured"


def runtime_configuration_matrix(*, strict: bool | None = None) -> dict:
    """Report config status without values; `unverified` is not treated as healthy."""
    production = settings.is_production if strict is None else strict
    groups = {
        "identity_security": _state(bool(settings.jwt_secret and settings.secret_key), required=production),
        "database": _state(bool(settings.database_url.strip()), required=True),
        "cache": _state(bool(settings.redis_url), required=False),
        "queue_jobs": _state(bool(settings.database_url.strip()), required=True),
        "object_storage": _state(settings.knowledge_storage_backend == "local" or bool(settings.knowledge_s3_bucket), required=settings.knowledge_storage_backend == "s3"),
        "llm": _state(any(bool((getattr(settings, f"{p}_api_key", "") or "").strip()) for p in ("openai", "anthropic", "google")), required=production),
        "stt": _state(bool((settings.deepgram_api_key or "").strip()), required=production),
        "tts": _state(bool((settings.elevenlabs_api_key or "").strip()), required=production),
        "deployment": _state(bool(settings.kubernetes_cluster_reference or settings.container_runtime_enabled or settings.airgap_manifest_directory or settings.artifact_registry_url), required=False),
        "observability": _state(bool(settings.sentry_dsn or settings.metrics_enabled), required=False),
    }
    reports = configuration_health()
    groups["provider_sdks"] = {
        name: {
            "state": "configured" if data["configured"] else "not_configured",
            "installed": data["installed"],
            "capable": data["capable"],
            "reachable": "not_checked",
            "authenticated": "not_checked",
            "sdk_version": data["sdk_version"],
            "api_surface": data["api_surface"],
        }
        for name, data in reports["sdk_capabilities"].items()
    }
    invalid = [issue.code for issue in validate_runtime_config(strict=strict)]
    return {
        "valid": not invalid,
        "invalid": invalid,
        "groups": groups,
        "unverified": ["provider_reachability", "deployment_runtime", "external_storage_reachability"],
    }


def validate_runtime_config(*, strict: bool | None = None) -> list[ValidationIssue]:
    production = settings.is_production if strict is None else strict
    issues: list[ValidationIssue] = []
    if not settings.database_url.strip():
        issues.append(ValidationIssue("database_url_missing", "database", "DATABASE_URL is required"))
    if settings.voice_provider_max_retries < 0 or settings.voice_provider_max_retries > 5:
        issues.append(ValidationIssue("retry_limit_invalid", "voice", "voice provider retry limit must be between 0 and 5"))
    if settings.voice_provider_timeout_seconds <= 0:
        issues.append(ValidationIssue("provider_timeout_invalid", "voice", "provider timeout must be positive"))
    if settings.knowledge_storage_backend not in {"local", "s3"}:
        issues.append(ValidationIssue("storage_backend_invalid", "storage", "knowledge storage backend must be local or s3"))
    if settings.knowledge_storage_backend == "s3" and not settings.knowledge_s3_bucket.strip():
        issues.append(ValidationIssue("s3_bucket_missing", "storage", "S3 storage requires a configured bucket"))
    if settings.kubernetes_cluster_reference.strip():
        parsed = urlsplit(settings.kubernetes_cluster_reference)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            issues.append(ValidationIssue("kubernetes_cluster_reference_invalid", "kubernetes", "Kubernetes cluster reference must be an HTTPS API origin"))
        report = detect_provider("kubernetes.client", "kubernetes", capabilities={"apps_v1", "core_v1"}, required_symbol="AppsV1Api")
        if not report.capable:
            issues.append(ValidationIssue("kubernetes_sdk_unavailable", "kubernetes", "configured Kubernetes adapter dependency is unavailable"))
    if settings.container_runtime_enabled:
        report = detect_provider("docker", "docker", capabilities={"container_inspection"}, required_symbol="from_env")
        if not report.capable:
            issues.append(ValidationIssue("container_sdk_unavailable", "container", "enabled container adapter dependency is unavailable"))
    if settings.airgap_manifest_directory or settings.airgap_public_key_base64:
        try:
            key = base64.b64decode(settings.airgap_public_key_base64, validate=True)
            key_valid = len(key) == 32
        except Exception:
            key_valid = False
        if not settings.airgap_manifest_directory or not key_valid or not Path(settings.airgap_manifest_directory).is_dir():
            issues.append(ValidationIssue("airgap_verification_config_invalid", "airgap", "air-gap verification needs an existing manifest directory and a valid Ed25519 public key"))
    if settings.artifact_registry_bearer_token and not settings.artifact_registry_url:
        issues.append(ValidationIssue("artifact_registry_token_without_url", "artifact_registry", "registry credentials require a configured registry URL"))
    if settings.artifact_registry_url:
        try:
            from app.deployment.adapters.registry import OCIRegistryClient
            OCIRegistryClient(settings.artifact_registry_url)
        except (TypeError, ValueError):
            issues.append(ValidationIssue("artifact_registry_url_invalid", "artifact_registry", "artifact registry URL must be a trusted HTTPS origin"))
    if settings.cost_unit_prices_json and not settings.cost_unit_prices:
        issues.append(ValidationIssue("cost_price_table_invalid", "observability", "configured cost price table is invalid"))
    if production:
        configured = configuration_health()["configured"]
        for component in ("deepgram", "elevenlabs", "llm"):
            if not configured[component]:
                issues.append(ValidationIssue(f"{component}_configuration_missing", component, f"required {component} configuration is missing"))
        provider_keys = {"deepgram": settings.deepgram_api_key, "elevenlabs": settings.elevenlabs_api_key, "openai": settings.openai_api_key, "anthropic": settings.anthropic_api_key, "google": settings.google_api_key}
        for provider, key in provider_keys.items():
            if key and key.strip() and not capabilities_for(provider).capable:
                issues.append(ValidationIssue(f"{provider}_sdk_incompatible", provider, f"configured {provider} SDK is unavailable or incompatible"))
        # Existing security validation remains the authority for identity secrets,
        # webhook verification, encryption keyrings, CORS and trusted hosts.
        for issue in settings.validate_security():
            issues.append(ValidationIssue("security_configuration_invalid", "identity_security", "production security configuration is invalid"))
    return issues


def require_valid_runtime_config(*, strict: bool | None = None) -> None:
    issues = validate_runtime_config(strict=strict)
    if issues:
        summary = ", ".join(issue.code for issue in issues)
        raise RuntimeError(f"runtime configuration validation failed: {summary}")
