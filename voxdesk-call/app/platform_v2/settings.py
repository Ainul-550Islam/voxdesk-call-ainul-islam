"""Configuration for the Batch 07 platform surfaces.

Why every value lives here and not in :mod:`app.core.config`
------------------------------------------------------------
The core settings object is the deployment's own vocabulary: database, redis,
secrets, provider keys. Batch 03/05/07 each added a *feature* namespace with its
own validation rules, and duplicating those keys in the core object would mean
two places to audit for "is this deployment actually configured". One settings
object per batch keeps the refusal rules next to the thing they protect.

The two validators that matter
------------------------------
* **An enabled push provider without its credentials is refused.** A mobile
  build that ships, registers a device and then silently never receives a
  notification is worse than one that fails at boot with a message naming the
  missing variable.
* **On-demand TLS without an ask token is refused.** The ``ask`` endpoint is the
  only thing standing between "any hostname on the internet" and "a certificate
  this deployment mints", and it authenticates itself with a shared token. A
  deployment with the token unset would accept every request, so it is a boot
  error rather than a runtime surprise.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PRODUCTION_ENV_NAMES = frozenset({"production", "prod"})
#: The two interval lengths WFM reports in. Anything else is refused rather than
#: rounded: a 20-minute interval silently becoming 15 would make every published
#: schedule describe a day that does not exist.
ALLOWED_INTERVAL_MINUTES = frozenset({15, 30})
PUSH_PLATFORMS = frozenset({"ios", "android"})


def _split_csv(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        items = str(value).split(",")
    return [item.strip() for item in items if item and item.strip()]


class PlatformV2Settings(BaseSettings):
    """Environment-backed settings for WFM, white-label domains and mobile push."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    # ---- deployment -----------------------------------------------------
    env: str = Field(default="development", alias="APP_ENV")
    #: Public origin of this API. Used to build the ACME ``ask`` URL, the CNAME
    #: target and the links inside branded email.
    public_base_url: str = Field(default="http://localhost:8000", alias="PUBLIC_BASE_URL")

    # ---- scheduler ------------------------------------------------------
    #: Batch 07 ticks (WFM rollup/forecast/accuracy/intraday, domain and cert
    #: verification, email-domain verification, push-token GC). Individual
    #: features keep their own flags; this one stops all of them together when a
    #: node is drained.
    scheduler_enabled: bool = Field(default=True, alias="PLAT2_SCHEDULER_ENABLED")

    # ---- WFM ------------------------------------------------------------
    wfm_interval_minutes: int = Field(default=15, alias="WFM_INTERVAL_MINUTES")
    #: Default IANA zone for the interval grid. A tenant that operates in one zone
    #: sets it once; the value is recorded on every forecast and schedule so a
    #: later change cannot silently reinterpret history.
    wfm_timezone: str = Field(default="UTC", alias="WFM_TIMEZONE")
    #: How many days ahead a forecast must cover. 42 days is six weeks: enough for
    #: a monthly bid cycle plus the week of publication lead time a rota needs.
    wfm_forecast_horizon_days: int = Field(default=42, alias="WFM_FORECAST_HORIZON_DAYS")
    #: Minimum history before a forecast is produced at all. Below this the
    #: seasonal model is fitting noise, so :mod:`app.platform_v2.wfm.forecast`
    #: refuses instead of returning a confident-looking guess.
    wfm_min_history_days: int = Field(default=28, alias="WFM_MIN_HISTORY_DAYS")
    #: The working day used to price a leave request that has no shifts attached.
    #: A contact centre is not necessarily 09:00-17:00, which is why this is
    #: configurable; a tenant on 24/7 operation sets 0/1440 and leave then costs
    #: wall-clock time.
    wfm_workday_start_minute: int = Field(default=540, alias="WFM_WORKDAY_START_MINUTE")
    wfm_workday_minutes: int = Field(default=480, alias="WFM_WORKDAY_MINUTES")
    #: Wall-clock budget for one CP-SAT schedule solve. On timeout the solver
    #: returns the best feasible schedule it has found *and* reports that it was
    #: truncated; it never relaxes a constraint to manufacture feasibility.
    wfm_solver_timeout_seconds: float = Field(default=30.0, alias="WFM_SOLVER_TIMEOUT_SECONDS")
    #: Fixed seed so two identical solves produce the same rota. A schedule is a
    #: document people sign; non-determinism would make "diff" meaningless.
    wfm_solver_seed: int = Field(default=20260707, alias="WFM_SOLVER_SEED")
    #: The service being bought, per queue: answer ``wfm_service_level`` of calls
    #: within ``wfm_answer_seconds`` while keeping occupancy under
    #: ``wfm_max_occupancy``. These are settings rather than constants because the
    #: target is a commercial decision and it belongs in the deployment's values.
    wfm_service_level: float = Field(default=0.8, alias="WFM_SERVICE_LEVEL")
    wfm_answer_seconds: float = Field(default=20.0, alias="WFM_ANSWER_SECONDS")
    wfm_max_occupancy: float = Field(default=0.85, alias="WFM_MAX_OCCUPANCY")
    #: Share of paid time that is not available to take contacts. 0 means "measure
    #: it from history instead", which is what the rollup does when it has the data.
    wfm_shrinkage: float = Field(default=0.0, alias="WFM_SHRINKAGE")
    wfm_average_handle_seconds: float = Field(default=240.0, alias="WFM_AHT_SECONDS")
    #: ``erlang_c`` (no abandonment) or ``erlang_a`` (callers give up).
    wfm_staffing_model: str = Field(default="erlang_c", alias="WFM_STAFFING_MODEL")
    wfm_average_patience_seconds: float = Field(default=300.0, alias="WFM_PATIENCE_SECONDS")

    # ---- custom domains / TLS ------------------------------------------
    custom_domain_cname_target: str = Field(
        default="edge.voxdesk.app", alias="CUSTOM_DOMAIN_CNAME_TARGET"
    )
    #: RFC 8555 directory. Defaults to Let's Encrypt *staging*: a misconfigured
    #: deployment should burn staging rate limits, not production ones.
    acme_directory_url: str = Field(
        default="https://acme-staging-v02.api.letsencrypt.org/directory",
        alias="ACME_DIRECTORY_URL",
    )
    acme_contact_email: str = Field(default="", alias="ACME_CONTACT_EMAIL")
    #: DNS-over-HTTPS resolvers used for ownership lookups, comma separated.
    #: Several by default so a poisoned or partitioned resolver is detectable:
    #: the checker requires agreement, and disagreement is reported rather than
    #: resolved by trusting the first answer.
    dns_check_resolvers: str = Field(
        default="https://cloudflare-dns.com/dns-query,https://dns.google/dns-query",
        alias="DNS_CHECK_RESOLVERS",
    )
    #: Public addresses the edge serves tenant domains from, comma separated. When
    #: set, an A record that points somewhere else fails verification — "the name
    #: resolves" is not the same proof as "the name resolves to us".
    custom_domain_edge_ips: str = Field(default="", alias="CUSTOM_DOMAIN_EDGE_IPS")
    dns_check_timeout_seconds: float = Field(default=5.0, alias="DNS_CHECK_TIMEOUT_SECONDS")
    domain_verify_ttl_hours: int = Field(default=72, alias="DOMAIN_VERIFY_TTL_HOURS")
    #: Shared secret Caddy presents to ``/internal/tls-ask``. Refused when empty
    #: in every environment where on-demand TLS is enabled.
    tls_ask_token: str = Field(default="", alias="TLS_ASK_TOKEN")
    tls_ask_enabled: bool = Field(default=True, alias="TLS_ASK_ENABLED")
    #: Re-verification cadence for an active domain. A dangling CNAME is how a
    #: subdomain gets hijacked after the fact, so an active domain is re-checked
    #: instead of being trusted forever.
    domain_reverify_hours: int = Field(default=24, alias="DOMAIN_REVERIFY_HOURS")

    # ---- branding -------------------------------------------------------
    branding_max_asset_bytes: int = Field(default=512_000, alias="BRANDING_MAX_ASSET_BYTES")
    branding_max_theme_bytes: int = Field(default=32_768, alias="BRANDING_MAX_THEME_BYTES")
    branding_asset_dir: str = Field(default="var/branding", alias="BRANDING_ASSET_DIR")

    # ---- email sender domains ------------------------------------------
    email_dkim_selector: str = Field(default="voxdesk", alias="EMAIL_DKIM_SELECTOR")
    email_dkim_public_key: str = Field(default="", alias="EMAIL_DKIM_PUBLIC_KEY")
    email_from_domain: str = Field(default="mail.voxdesk.app", alias="EMAIL_FROM_DOMAIN")
    #: The SPF include a tenant's record must authorise, e.g. ``_spf.voxdesk.app``.
    #: Blank means the platform does not send on the tenant's behalf, and the
    #: generated SPF record is then the safe ``v=spf1 -all``.
    email_spf_include: str = Field(default="", alias="EMAIL_SPF_INCLUDE")

    # ---- mobile push ----------------------------------------------------
    fcm_enabled: bool = Field(default=False, alias="FCM_ENABLED")
    fcm_project_id: str = Field(default="", alias="FCM_PROJECT_ID")
    #: Full service-account JSON. Stored as a secret, never echoed by any route.
    fcm_service_account_json: str = Field(default="", alias="FCM_SERVICE_ACCOUNT_JSON")
    fcm_endpoint: str = Field(
        default="https://fcm.googleapis.com", alias="FCM_ENDPOINT"
    )

    apns_enabled: bool = Field(default=False, alias="APNS_ENABLED")
    apns_key_id: str = Field(default="", alias="APNS_KEY_ID")
    apns_team_id: str = Field(default="", alias="APNS_TEAM_ID")
    apns_private_key_p8: str = Field(default="", alias="APNS_PRIVATE_KEY_P8")
    apns_bundle_id: str = Field(default="", alias="APNS_BUNDLE_ID")
    apns_use_sandbox: bool = Field(default=True, alias="APNS_USE_SANDBOX")
    apns_endpoint: str = Field(default="", alias="APNS_ENDPOINT")

    #: Tokens are deleted (not retried) after this many consecutive permanent
    #: failures. FCM reports ``UNREGISTERED`` explicitly; APNs reports
    #: ``BadDeviceToken``/``Unregistered``. Anything else is retried.
    push_token_gc_days: int = Field(default=30, alias="PUSH_TOKEN_GC_DAYS")
    #: Quiet hours are per-tenant settings; this is the platform ceiling so a
    #: tenant cannot configure "never notify me" and then report undelivered
    #: pages as incidents.
    push_quiet_hours_max_span: int = Field(default=12, alias="PUSH_QUIET_HOURS_MAX_SPAN")

    # ---- rate limits ----------------------------------------------------
    mobile_auth_requests_per_minute: int = Field(default=30, alias="MOBILE_AUTH_PER_MINUTE")

    # ---- validators -----------------------------------------------------
    @field_validator("wfm_interval_minutes")
    @classmethod
    def _interval_is_supported(cls, value: int) -> int:
        if int(value) not in ALLOWED_INTERVAL_MINUTES:
            raise ValueError(
                f"WFM_INTERVAL_MINUTES must be one of {sorted(ALLOWED_INTERVAL_MINUTES)}, got {value}"
            )
        return int(value)

    @field_validator("wfm_forecast_horizon_days")
    @classmethod
    def _horizon_is_positive(cls, value: int) -> int:
        if int(value) < 1 or int(value) > 365:
            raise ValueError("WFM_FORECAST_HORIZON_DAYS must be between 1 and 365")
        return int(value)

    @field_validator("wfm_min_history_days")
    @classmethod
    def _min_history_is_sane(cls, value: int) -> int:
        # 14 days is two full weekly cycles: the shortest span in which a
        # weekday pattern can be told apart from a trend.
        if int(value) < 14:
            raise ValueError("WFM_MIN_HISTORY_DAYS must be at least 14 (two weekly cycles)")
        return int(value)

    @field_validator("wfm_service_level")
    @classmethod
    def _service_level_in_unit_interval(cls, value: float) -> float:
        if not 0.0 < float(value) <= 1.0:
            raise ValueError("WFM_SERVICE_LEVEL must be in (0, 1]")
        return float(value)

    @field_validator("wfm_answer_seconds")
    @classmethod
    def _answer_seconds_non_negative(cls, value: float) -> float:
        if float(value) < 0.0:
            raise ValueError("WFM_ANSWER_SECONDS must be non-negative")
        return float(value)

    @field_validator("wfm_max_occupancy")
    @classmethod
    def _occupancy_in_unit_interval(cls, value: float) -> float:
        if not 0.0 < float(value) <= 1.0:
            raise ValueError("WFM_MAX_OCCUPANCY must be in (0, 1]")
        return float(value)

    @field_validator("wfm_shrinkage")
    @classmethod
    def _shrinkage_below_one(cls, value: float) -> float:
        if not 0.0 <= float(value) < 1.0:
            raise ValueError("WFM_SHRINKAGE must be in [0, 1)")
        return float(value)

    @field_validator("wfm_average_handle_seconds", "wfm_average_patience_seconds")
    @classmethod
    def _seconds_positive(cls, value: float) -> float:
        if float(value) <= 0.0:
            raise ValueError("handle time and patience must be positive")
        return float(value)

    @field_validator("wfm_staffing_model")
    @classmethod
    def _known_staffing_model(cls, value: str) -> str:
        cleaned = str(value or "").strip().lower()
        if cleaned not in ("erlang_c", "erlang_a"):
            raise ValueError("WFM_STAFFING_MODEL must be erlang_c or erlang_a")
        return cleaned

    @field_validator("wfm_workday_start_minute")
    @classmethod
    def _workday_start_in_day(cls, value: int) -> int:
        if not 0 <= int(value) < 24 * 60:
            raise ValueError("WFM_WORKDAY_START_MINUTE must be a minute of the day")
        return int(value)

    @field_validator("wfm_workday_minutes")
    @classmethod
    def _workday_length_is_sane(cls, value: int) -> int:
        # A working day longer than 24 hours would double-count a leave request.
        if not 1 <= int(value) <= 24 * 60:
            raise ValueError("WFM_WORKDAY_MINUTES must be between 1 and 1440")
        return int(value)

    @field_validator("domain_verify_ttl_hours", "domain_reverify_hours", "push_token_gc_days")
    @classmethod
    def _hours_are_positive(cls, value: int) -> int:
        if int(value) < 1:
            raise ValueError("hour/day counts must be positive")
        return int(value)

    @field_validator("push_quiet_hours_max_span")
    @classmethod
    def _quiet_span_is_usable(cls, value: int) -> int:
        # A zero ceiling would silently silence nothing (an empty window is refused
        # by the device rules) and a 25-hour ceiling is not a day.
        if not 1 <= int(value) <= 24:
            raise ValueError("PUSH_QUIET_HOURS_MAX_SPAN must be between 1 and 24 hours")
        return int(value)

    @field_validator("custom_domain_cname_target")
    @classmethod
    def _cname_target_is_a_hostname(cls, value: str) -> str:
        cleaned = str(value or "").strip().lower().rstrip(".")
        if not cleaned:
            raise ValueError("CUSTOM_DOMAIN_CNAME_TARGET must not be empty")
        if "://" in cleaned or "/" in cleaned or " " in cleaned:
            # A CNAME target is a hostname. Pasting the edge URL here produces a DNS
            # record that never resolves, and the failure looks like "white-label is
            # broken" three layers away from the typo.
            raise ValueError("CUSTOM_DOMAIN_CNAME_TARGET must be a hostname, not a URL")
        return cleaned

    @model_validator(mode="after")
    def _enabled_providers_have_credentials(self) -> "PlatformV2Settings":
        problems: list[str] = []
        if self.fcm_enabled:
            missing = [
                name
                for name, value in (
                    ("FCM_PROJECT_ID", self.fcm_project_id),
                    ("FCM_SERVICE_ACCOUNT_JSON", self.fcm_service_account_json),
                )
                if not str(value).strip()
            ]
            if missing:
                problems.append("FCM is enabled but " + ", ".join(missing) + " is empty")
        if self.apns_enabled:
            missing = [
                name
                for name, value in (
                    ("APNS_KEY_ID", self.apns_key_id),
                    ("APNS_TEAM_ID", self.apns_team_id),
                    ("APNS_PRIVATE_KEY_P8", self.apns_private_key_p8),
                    ("APNS_BUNDLE_ID", self.apns_bundle_id),
                )
                if not str(value).strip()
            ]
            if missing:
                problems.append("APNs is enabled but " + ", ".join(missing) + " is empty")
        if self.tls_ask_enabled and self.on_demand_tls_in_use and not self.tls_ask_token.strip():
            problems.append(
                "TLS_ASK_ENABLED is on and custom domains are configured but TLS_ASK_TOKEN is empty; "
                "the ask endpoint would approve any hostname"
            )
        if problems:
            raise ValueError("; ".join(problems))
        return self

    # ---- derived --------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.env.strip().lower() in PRODUCTION_ENV_NAMES

    @property
    def on_demand_tls_in_use(self) -> bool:
        """Whether this deployment terminates TLS for customer domain names.

        On the developer default (a ``localhost`` public URL) it does not: the
        ``ask`` endpoint exists but only the health of the wiring is asserted,
        so the token requirement is not imposed on a laptop.
        """
        base = self.public_base_url.strip().lower()
        if self.is_production:
            return True
        return not base.startswith(("http://localhost", "http://127.0.0.1"))

    @property
    def resolver_urls(self) -> list[str]:
        return _split_csv(self.dns_check_resolvers) or ["https://cloudflare-dns.com/dns-query"]

    @property
    def apns_base_url(self) -> str:
        if self.apns_endpoint.strip():
            return self.apns_endpoint.strip().rstrip("/")
        host = "api.sandbox.push.apple.com" if self.apns_use_sandbox else "api.push.apple.com"
        return f"https://{host}"

    @property
    def dkim_public_key(self) -> str:
        """The DKIM key published in DNS, or the documented placeholder.

        Not a secret: a DKIM public key is meant to be readable by any resolver.
        When the deployment has not been given one, the record text says so
        instead of inventing a key that would never validate.
        """
        return self.email_dkim_public_key.strip()


def _env_is_production() -> bool:
    return os.environ.get("APP_ENV", "development").strip().lower() in PRODUCTION_ENV_NAMES


@lru_cache(maxsize=1)
def get_plat2_settings() -> PlatformV2Settings:
    """The process-wide settings object.

    Constructed eagerly (and cached) because the validators are the point: a
    deployment with half a push provider configured must fail here, at boot,
    rather than on the first notification an on-call engineer was relying on.
    """
    settings = PlatformV2Settings()
    if _env_is_production() and settings.fcm_enabled and settings.apns_enabled:
        # Both providers on in production is legitimate; the check above already
        # proved they have credentials. Nothing else to refuse.
        return settings
    return settings


def reset_plat2_settings_cache() -> None:
    """Drop the cached settings (tests, and a config reload after rotation)."""
    get_plat2_settings.cache_clear()


__all__ = [
    "ALLOWED_INTERVAL_MINUTES",
    "PRODUCTION_ENV_NAMES",
    "PUSH_PLATFORMS",
    "PlatformV2Settings",
    "get_plat2_settings",
    "reset_plat2_settings_cache",
]
