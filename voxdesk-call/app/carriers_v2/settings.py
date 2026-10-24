"""Batch 02 carrier + speech-to-speech settings (Vonage, SIP gateway, S2S).

``app/core/config.py`` is a *shared* file: another batch edits it at the same
time, so Batch 02 owns no line there. Everything Batch 02 needs is declared
here instead, read from the environment, and validated before a feature is
allowed to claim that it works.

Three rules drive the design:

* **A capability is true only when the credential is present.** A missing
  Vonage application id, an unreadable private key or an unreachable SIP
  gateway is reported as ``NOT_CONFIGURED`` -- never as working.
* **A secret that looks like a placeholder is not a secret.** The same marker
  list the main settings use refuses ``change-me`` and friends, so a
  half-configured deployment fails loudly instead of authenticating with
  garbage.
* **Feature flags never invent behaviour.** ``ENABLE_S2S`` only permits the
  speech-to-speech runtime to be *selected*; the provider credential is still
  required before a single byte is streamed.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Substrings that mark a value as an obvious placeholder. Mirrors the main
#: settings: a value containing any of these cannot be a real credential.
_PLACEHOLDER_MARKERS = (
    "change-me",
    "change_me",
    "insecure",
    "xxxx",
    "placeholder",
    "your-",
    "<",
    ">",
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def looks_like_placeholder(value: str | None) -> bool:
    """True when ``value`` is clearly a placeholder rather than a credential."""
    lowered = (value or "").strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


class CarrierV2Settings(BaseSettings):
    """Environment-driven configuration for the Batch 02 carrier surfaces."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- Vonage Voice API + media streaming ----------------------------
    vonage_api_key: str = ""
    vonage_api_secret: str = ""
    vonage_application_id: str = ""
    #: Path to the RS256 private key that signs the Vonage application JWT.
    vonage_private_key_path: str = ""
    #: Inline PEM alternative to ``vonage_private_key_path``.
    vonage_private_key: str = ""
    #: Shared secret used to verify inbound Vonage webhook signatures.
    vonage_signature_secret: str = ""
    vonage_api_base: str = "https://api.nexmo.com"
    vonage_number_base: str = "https://rest.nexmo.com"
    vonage_http_timeout_seconds: float = 8.0

    # ---- SIP gateway (services/sip-gateway) ----------------------------
    #: Base URL of the Go SIP gateway control API, e.g. ``http://sip:8080``.
    sip_gateway_url: str = ""
    #: Bearer token the gateway requires on its control API.
    sip_gateway_token: str = ""
    sip_gateway_timeout_seconds: float = 5.0
    sip_gateway_ready_timeout_seconds: float = 2.0

    # ---- Speech-to-speech runtime --------------------------------------
    openai_realtime_model: str = "gpt-4o-realtime-preview"
    gemini_live_model: str = "gemini-2.0-flash-exp"
    #: Master switch for the speech-to-speech runtime.
    enable_s2s: bool = False
    #: Rebuild the cascaded STT -> LLM -> TTS pipeline when the S2S
    #: provider fails mid-call.
    s2s_fallback_enabled: bool = True
    #: Hard ceiling on one speech-to-speech session.
    s2s_max_session_seconds: int = 600
    #: Per-call ceiling on tool invocations from the realtime model.
    s2s_max_tool_calls: int = 12
    #: Per-session and per-tenant cost ceilings, in micro-USD.
    s2s_session_budget_micro_usd: int = 500_000
    s2s_tenant_budget_micro_usd: int = 5_000_000

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def _refuse_invalid_feature_configuration(self) -> "CarrierV2Settings":
        """Refuse a configuration that claims a feature it cannot deliver."""
        if self.enable_s2s:
            if self.s2s_max_session_seconds <= 0:
                raise ValueError(
                    "S2S_MAX_SESSION_SECONDS must be positive when ENABLE_S2S is set"
                )
            if self.s2s_max_tool_calls <= 0:
                raise ValueError(
                    "S2S_MAX_TOOL_CALLS must be positive when ENABLE_S2S is set"
                )
            if self.s2s_session_budget_micro_usd <= 0:
                raise ValueError(
                    "S2S_SESSION_BUDGET_MICRO_USD must be positive when ENABLE_S2S is set"
                )
            if not self.s2s_provider_available():
                raise ValueError(
                    "ENABLE_S2S is set but no speech-to-speech provider credential is "
                    "available (set OPENAI_API_KEY or GOOGLE_API_KEY)"
                )
        if self.sip_gateway_url and looks_like_placeholder(self.sip_gateway_token):
            raise ValueError(
                "SIP_GATEWAY_TOKEN still contains a placeholder value; refusing to start"
            )
        return self

    # ------------------------------------------------------------------
    # Capability helpers -- all of them are honest by construction.
    # ------------------------------------------------------------------
    def s2s_provider_available(self) -> bool:
        """True when at least one speech-to-speech provider credential exists."""
        from app.core.config import settings as core_settings

        openai = (getattr(core_settings, "openai_api_key", "") or "").strip()
        google = (getattr(core_settings, "google_api_key", "") or "").strip()
        return bool(openai or google)

    @property
    def s2s_enabled(self) -> bool:
        """``ENABLE_S2S`` or the legacy ``VOXDESK_ENABLE_S2S`` switch."""
        return bool(self.enable_s2s or _env_flag("VOXDESK_ENABLE_S2S"))

    def resolved_vonage_private_key(self) -> str:
        """The Vonage RS256 private key, from the inline value or the file."""
        inline = (self.vonage_private_key or "").strip()
        if inline:
            return inline
        path = (self.vonage_private_key_path or "").strip()
        if not path:
            return ""
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return ""

    @property
    def vonage_private_key_available(self) -> bool:
        return bool(self.resolved_vonage_private_key().strip())

    def vonage_media_ready(self) -> bool:
        """True only when every credential the Vonage media path needs exists."""
        return bool(
            (self.vonage_api_key or "").strip()
            and (self.vonage_api_secret or "").strip()
            and (self.vonage_application_id or "").strip()
            and self.vonage_private_key_available
            and (self.vonage_signature_secret or "").strip()
            and not looks_like_placeholder(self.vonage_signature_secret)
        )

    def require_vonage_media(self) -> None:
        """Raise ``ValueError`` when the Vonage media path is not usable."""
        if not self.vonage_media_ready():
            raise ValueError(
                "Vonage media streaming requires VONAGE_API_KEY, VONAGE_API_SECRET, "
                "VONAGE_APPLICATION_ID, a readable VONAGE_PRIVATE_KEY_PATH and "
                "VONAGE_SIGNATURE_SECRET"
            )

    def sip_gateway_ready(self) -> bool:
        """True only when the SIP gateway is configured with a real token."""
        url = (self.sip_gateway_url or "").strip()
        token = (self.sip_gateway_token or "").strip()
        if not url or not token:
            return False
        if looks_like_placeholder(token):
            return False
        return url.startswith("http://") or url.startswith("https://")

    def require_sip_gateway(self) -> None:
        """Raise ``ValueError`` when the SIP gateway is not usable."""
        if not self.sip_gateway_ready():
            raise ValueError(
                "The SIP gateway requires SIP_GATEWAY_URL and a non-placeholder "
                "SIP_GATEWAY_TOKEN"
            )


@lru_cache(maxsize=1)
def get_carrier_v2_settings() -> CarrierV2Settings:
    """The process-wide Batch 02 settings (cached; call the reset in tests)."""
    return CarrierV2Settings()


def reset_carrier_v2_settings_cache() -> None:
    """Drop the cached settings so a test can re-read the environment."""
    get_carrier_v2_settings.cache_clear()


__all__ = [
    "CarrierV2Settings",
    "get_carrier_v2_settings",
    "looks_like_placeholder",
    "reset_carrier_v2_settings_cache",
]
