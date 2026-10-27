"""Configuration for the Batch 03 compliance controls.

Every value here is read from the environment with an explicit, bounded default.
Two validators exist for a reason that is worth stating rather than implying:

* **The development KMS is refused in production.** ``KMS_PROVIDER=local_dev``
  keeps a file-based master key on the application host. That is fine on a
  laptop and unacceptable in production, where the whole point of envelope
  encryption is that the key-wrapping key never sits next to the ciphertext.
  The refusal happens at construction, so a misconfigured deployment fails at
  boot instead of encrypting one record with a host-local key.
* **An enabled SIEM sink without its secret is refused.** A sink with no
  token would fail on first send; failing at configuration time surfaces the
  operator mistake where it can still be fixed.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PRODUCTION_ENV_NAMES = frozenset({"production", "prod"})
KMS_PROVIDERS = frozenset({"aws", "gcp", "azure", "local_dev"})
SIEM_SINK_KINDS = frozenset({"splunk_hec", "datadog_logs", "elastic_bulk", "syslog_cef", "s3_jsonl"})


def _split_csv(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        items = str(value).split(",")
    return [item.strip() for item in items if item and item.strip()]


class ComplianceV2Settings(BaseSettings):
    """Environment-backed settings for SIEM, KMS, residency, evidence and HIPAA."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    # ---- deployment -----------------------------------------------------
    #: Mirrors ``APP_ENV``. Duplicated here (instead of importing the core
    #: settings object) so this module can be constructed in a test with an
    #: explicit environment and no global state.
    env: str = Field(default="development", alias="APP_ENV")
    #: Where signed evidence bundles, DSAR exports and evidence snapshots live.
    evidence_dir: str = Field(default="evidence", alias="COMPLIANCE_EVIDENCE_DIR")

    # ---- scheduler -------------------------------------------------------
    # BATCH06: kill switch for the compliance maintenance cycles wired into
    # scripts/scheduler.py. A drained node can switch the whole package's
    # periodic passes off without touching the API.
    scheduler_enabled: bool = Field(default=True, alias="COMPLIANCE_V2_SCHEDULER_ENABLED")

    # ---- SIEM -----------------------------------------------------------
    siem_enabled: bool = Field(default=True, alias="SIEM_ENABLED")
    #: How many audit rows one dispatch tick may hand to one sink.
    siem_batch_size: int = Field(default=200, ge=1, le=2000, alias="SIEM_BATCH_SIZE")
    #: Maximum delivery attempts per batch before the row dead-letters.
    siem_max_attempts: int = Field(default=5, ge=1, le=20, alias="SIEM_MAX_ATTEMPTS")
    #: Consecutive failed batches that open a sink's circuit breaker.
    siem_circuit_threshold: int = Field(default=3, ge=1, le=50, alias="SIEM_CIRCUIT_THRESHOLD")
    siem_circuit_cooldown_seconds: int = Field(
        default=60, ge=1, le=3600, alias="SIEM_CIRCUIT_COOLDOWN_SECONDS"
    )
    #: Base retry delay; doubled per attempt, capped at the value below.
    siem_retry_base_seconds: int = Field(default=5, ge=1, le=600, alias="SIEM_RETRY_BASE_SECONDS")
    siem_retry_max_seconds: int = Field(default=900, ge=1, le=86400, alias="SIEM_RETRY_MAX_SECONDS")
    siem_http_timeout_seconds: float = Field(
        default=10.0, gt=0, le=120, alias="SIEM_HTTP_TIMEOUT_SECONDS"
    )
    #: Splunk HEC channel identifier sent as ``X-Splunk-Request-Channel``.
    siem_splunk_channel: str = Field(default="voxdesk-audit", alias="SIEM_SPLUNK_CHANNEL")

    # ---- KMS / envelope encryption --------------------------------------
    kms_provider: str = Field(default="local_dev", alias="KMS_PROVIDER")
    kms_key_id: str = Field(default="", alias="KMS_KEY_ID")
    #: AWS region / GCP location / Azure vault URL, depending on the provider.
    kms_region: str = Field(default="", alias="KMS_REGION")
    kms_azure_vault_url: str = Field(default="", alias="KMS_AZURE_VAULT_URL")
    #: File-based master key for the development provider only.
    kms_local_master_key_path: str = Field(
        default="var/compliance/local_master.key", alias="KMS_LOCAL_MASTER_KEY_PATH"
    )
    kms_max_attempts: int = Field(default=4, ge=1, le=10, alias="KMS_MAX_ATTEMPTS")
    # Rows per batch for the rekey job. A rekey walks every encrypted column in
    # the tenant's tables, so the batch size is what keeps a large deployment
    # from holding one transaction open for minutes.
    kms_rekey_batch_size: int = Field(
        default=100, ge=1, le=1000, alias="KMS_REKEY_BATCH_SIZE"
    )

    # ---- audit signing ---------------------------------------------------
    audit_signing_key_path: str = Field(
        default="var/compliance/audit_signing_ed25519.pem", alias="AUDIT_SIGNING_KEY_PATH"
    )

    # ---- residency -------------------------------------------------------
    data_regions: str = Field(default="", alias="DATA_REGIONS")
    default_region: str = Field(default="", alias="DEFAULT_REGION")
    residency_guard_enabled: bool = Field(default=True, alias="RESIDENCY_GUARD_ENABLED")
    #: Path prefixes whose storage keys carry a region segment.
    residency_protected_prefixes: str = Field(
        default="/api/v1/recordings,/api/v1/transcripts,/api/v1/exports,/api/v1/calls/",
        alias="RESIDENCY_PROTECTED_PREFIXES",
    )

    # ---- HIPAA / session -------------------------------------------------
    break_glass_max_minutes: int = Field(default=60, ge=5, le=1440, alias="BREAK_GLASS_MAX_MINUTES")
    break_glass_dual_approval: bool = Field(
        default=True, alias="BREAK_GLASS_DUAL_APPROVAL"
    )
    session_idle_timeout_minutes: int = Field(
        default=30, ge=1, le=10080, alias="SESSION_IDLE_TIMEOUT_MINUTES"
    )
    session_absolute_timeout_minutes: int = Field(
        default=720, ge=1, le=525600, alias="SESSION_ABSOLUTE_TIMEOUT_MINUTES"
    )
    sensitive_reauth_window_minutes: int = Field(
        default=15, ge=1, le=1440, alias="SENSITIVE_REAUTH_WINDOW_MINUTES"
    )

    # ---- evidence automation ---------------------------------------------
    evidence_github_repo: str = Field(default="", alias="EVIDENCE_GITHUB_REPO")
    evidence_github_token: str = Field(default="", alias="EVIDENCE_GITHUB_TOKEN")
    evidence_dr_max_age_hours: int = Field(
        default=24 * 30, ge=1, le=24 * 400, alias="EVIDENCE_DR_MAX_AGE_HOURS"
    )
    evidence_vuln_max_age_hours: int = Field(
        default=24 * 14, ge=1, le=24 * 400, alias="EVIDENCE_VULN_MAX_AGE_HOURS"
    )
    evidence_slo_target: float = Field(default=0.9995, gt=0, lt=1, alias="EVIDENCE_SLO_TARGET")

    # ---- DSAR / incidents -------------------------------------------------
    dsar_due_days: int = Field(default=30, ge=1, le=365, alias="DSAR_DUE_DAYS")

    @field_validator("kms_provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        provider = (value or "").strip().lower()
        if provider not in KMS_PROVIDERS:
            raise ValueError(
                f"KMS_PROVIDER must be one of {sorted(KMS_PROVIDERS)}, got {value!r}"
            )
        return provider

    @field_validator("env")
    @classmethod
    def _known_env(cls, value: str) -> str:
        return (value or "development").strip().lower()

    @field_validator("data_regions")
    @classmethod
    def _region_letters(cls, value: str) -> str:
        for region in _split_csv(value):
            if not region.replace("-", "").replace("_", "").isalnum():
                raise ValueError(f"DATA_REGIONS entry {region!r} is not a region label")
        return value

    @model_validator(mode="after")
    def _refuse_unsafe_combinations(self) -> "ComplianceV2Settings":
        if self.is_production and self.kms_provider == "local_dev":
            raise ValueError(
                "KMS_PROVIDER=local_dev is a development-only provider and is refused "
                "when APP_ENV=production; configure aws, gcp or azure"
            )
        if self.kms_provider == "azure" and not self.kms_azure_vault_url:
            raise ValueError("KMS_PROVIDER=azure requires KMS_AZURE_VAULT_URL")
        if self.kms_provider in {"aws", "gcp"} and not self.kms_key_id:
            raise ValueError(f"KMS_PROVIDER={self.kms_provider} requires KMS_KEY_ID")
        if self.default_region and self.default_region not in self.regions:
            raise ValueError("DEFAULT_REGION must be one of DATA_REGIONS")
        if self.session_absolute_timeout_minutes < self.session_idle_timeout_minutes:
            raise ValueError(
                "SESSION_ABSOLUTE_TIMEOUT_MINUTES must be at least SESSION_IDLE_TIMEOUT_MINUTES"
            )
        return self

    # ---- derived views ----------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.env in PRODUCTION_ENV_NAMES

    @property
    def regions(self) -> tuple[str, ...]:
        return tuple(_split_csv(self.data_regions))

    @property
    def protected_prefixes(self) -> tuple[str, ...]:
        return tuple(_split_csv(self.residency_protected_prefixes))

    @property
    def evidence_path(self):
        from pathlib import Path

        return Path(self.evidence_dir)

    def refuse_sink_without_secret(self, *, kind: str, secret_ref: str) -> None:
        """Fail closed when an enabled sink of a secret-bearing kind has no secret."""
        if kind not in SIEM_SINK_KINDS:
            raise ValueError(f"unknown SIEM sink kind {kind!r}")
        needs_secret = kind in {"splunk_hec", "datadog_logs", "elastic_bulk"}
        if needs_secret and not (secret_ref or "").strip():
            raise ValueError(
                f"SIEM sink kind {kind!r} requires a stored credential; "
                "configure one before enabling the sink"
            )


@lru_cache(maxsize=1)
def get_compliance_v2_settings() -> ComplianceV2Settings:
    """Process-wide settings instance (tests may call ``cache_clear()``)."""
    return ComplianceV2Settings()


def reset_compliance_v2_settings() -> None:
    """Drop the cached instance so the next call re-reads the environment."""
    get_compliance_v2_settings.cache_clear()


def environment_name() -> str:
    """The deployment environment, from the same variable the core settings read."""
    return os.environ.get("APP_ENV", "development").strip().lower()
