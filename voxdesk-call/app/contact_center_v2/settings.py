"""Batch 04 settings; no other shared settings module is modified.

Environment names are prefixed with ``CC2_``. The global abandonment cap can
only be lowered below 3%; a higher campaign cap requires the separate durable,
audited jurisdiction-override flow and remains bounded by a hard safety limit.
"""
from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ContactCenterV2Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CC2_", extra="ignore", validate_default=True
    )

    abandon_rate_cap: Decimal = Field(
        default=Decimal("0.03"), gt=Decimal("0"), le=Decimal("0.03")
    )
    pacing_tick_ms: int = Field(default=2000, ge=100, le=60_000)
    max_dial_ratio: Decimal = Field(default=Decimal("4.0"), ge=Decimal("1"), le=Decimal("20"))
    min_agent_sample: int = Field(default=30, ge=1, le=10_000)
    wrapup_default_seconds: int = Field(default=300, ge=30, le=3600)
    softphone_token_ttl_seconds: int = Field(default=300, ge=60, le=3600)
    agent_assist_model: str = Field(default="", max_length=160)
    agent_assist_max_latency_ms: int = Field(default=6000, ge=500, le=30_000)

    dialer_max_attempts_per_tick: int = Field(default=20, ge=1, le=500)
    dialer_max_abandon_cap_with_override: Decimal = Field(
        default=Decimal("0.10"), gt=Decimal("0.03"), le=Decimal("0.10")
    )
    dialer_trust_max_age_hours: int = Field(default=24, ge=1, le=720)
    dialer_preview_lease_seconds: int = Field(default=300, ge=30, le=3600)
    # Human answers must receive safe-harbor handling within two seconds.
    # This is a hard upper bound, not a tuning knob that can silence callers.
    dialer_agent_join_timeout_seconds: int = Field(default=2, ge=1, le=2)
    dialer_cps: Decimal = Field(default=Decimal("2"), gt=Decimal("0"), le=Decimal("100"))
    dialer_cps_burst: int = Field(default=4, ge=1, le=200)

    softphone_session_ttl_seconds: int = Field(default=3600, ge=300, le=86_400)
    softphone_twilio_api_key_sid: str = ""
    softphone_twilio_api_key_secret: str = ""
    softphone_twilio_twiml_app_sid: str = ""
    softphone_provider_timeout_seconds: float = Field(default=8.0, gt=0, le=30)
    softphone_signaling_max_queue: int = Field(default=128, ge=8, le=4096)

    turn_static_secret: str = ""
    turn_ttl_seconds: int = Field(default=3600, ge=300, le=86_400)
    turn_udp_url: str = ""
    turn_tcp_url: str = ""
    turns_url: str = ""
    stun_url: str = ""

    assist_retrieval_timeout_seconds: float = Field(default=1.5, gt=0, le=10)
    assist_top_k: int = Field(default=3, ge=1, le=8)
    assist_max_context_chars: int = Field(default=5000, ge=500, le=20_000)
    assist_negative_threshold: float = Field(default=-0.25, ge=-1, le=0)
    assist_alert_cooldown_seconds: int = Field(default=120, ge=0, le=3600)
    assist_max_suggestions_per_call: int = Field(default=100, ge=1, le=1000)

    maintenance_enabled: bool = True
    occupancy_bucket_seconds: int = Field(default=300, ge=60, le=3600)
    acd_overflow_sweep_seconds: int = Field(default=5, ge=1, le=300)
    realtime_snapshot_interval_ms: int = Field(default=1000, ge=250, le=10_000)
    callback_max_attempts: int = Field(default=3, ge=1, le=10)
    callback_retry_seconds: int = Field(default=900, ge=60, le=86_400)

    @field_validator("agent_assist_model")
    @classmethod
    def model_name_is_not_a_secret(cls, value: str) -> str:
        value = value.strip()
        if any(token in value.lower() for token in ("key=", "secret", "bearer ", "password")):
            raise ValueError("AGENT_ASSIST_MODEL must be a model identifier, not a credential")
        return value

    @property
    def dialer_tick_seconds(self) -> float:
        return self.pacing_tick_ms / 1000

    @property
    def dialer_default_abandon_cap(self) -> Decimal:
        return self.abandon_rate_cap

    @property
    def dialer_min_abandon_sample(self) -> int:
        return self.min_agent_sample

    @property
    def dialer_max_pacing_ratio(self) -> Decimal:
        return self.max_dial_ratio

    @property
    def assist_provider_timeout_ms(self) -> int:
        return self.agent_assist_max_latency_ms

    # Uppercase aliases make the deployment contract discoverable without
    # duplicating values or permitting a second source of configuration.
    @property
    def ABANDON_RATE_CAP(self) -> Decimal:  # noqa: N802
        return self.abandon_rate_cap

    @property
    def PACING_TICK_MS(self) -> int:  # noqa: N802
        return self.pacing_tick_ms

    @property
    def MAX_DIAL_RATIO(self) -> Decimal:  # noqa: N802
        return self.max_dial_ratio

    @property
    def MIN_AGENT_SAMPLE(self) -> int:  # noqa: N802
        return self.min_agent_sample

    @property
    def WRAPUP_DEFAULT_SECONDS(self) -> int:  # noqa: N802
        return self.wrapup_default_seconds

    @property
    def SOFTPHONE_TOKEN_TTL_SECONDS(self) -> int:  # noqa: N802
        return self.softphone_token_ttl_seconds

    @property
    def AGENT_ASSIST_MODEL(self) -> str:  # noqa: N802
        return self.agent_assist_model

    @property
    def AGENT_ASSIST_MAX_LATENCY_MS(self) -> int:  # noqa: N802
        return self.agent_assist_max_latency_ms


@lru_cache(maxsize=1)
def get_cc2_settings() -> ContactCenterV2Settings:
    return ContactCenterV2Settings()


get_settings = get_cc2_settings
settings = get_cc2_settings()

__all__ = [
    "ContactCenterV2Settings",
    "get_cc2_settings",
    "get_settings",
    "settings",
]
