"""Settings must validate the environment at construction, not at first use.

Every field in this package is read on a request path. A settings object that
constructs with an unusable value turns a configuration mistake into a 500 in the
middle of a rota publish, so the validators are tested here directly — against the
real environment variable names, so a test cannot pass while production reads a
different variable.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.platform_v2 import settings as plat_settings

ENV_NAMES = (
    "APP_ENV",
    "PUBLIC_BASE_URL",
    "PLAT2_SCHEDULER_ENABLED",
    "WFM_INTERVAL_MINUTES",
    "WFM_TIMEZONE",
    "WFM_FORECAST_HORIZON_DAYS",
    "WFM_MIN_HISTORY_DAYS",
    "WFM_SOLVER_TIMEOUT_SECONDS",
    "CUSTOM_DOMAIN_CNAME_TARGET",
    "CUSTOM_DOMAIN_EDGE_IPS",
    "PUSH_QUIET_HOURS_MAX_SPAN",
    "PUSH_TOKEN_GC_DAYS",
    "TLS_ASK_ENABLED",
    "TLS_ASK_TOKEN",
    "FCM_ENABLED",
    "APNS_ENABLED",
)


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    """Each test builds its own settings object from a clean environment."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    plat_settings.reset_plat2_settings_cache()
    yield
    plat_settings.reset_plat2_settings_cache()


def test_defaults_are_usable_without_any_environment():
    """A developer running the API with no `.env` must still boot."""
    cfg = plat_settings.get_plat2_settings()
    assert cfg.wfm_interval_minutes in plat_settings.ALLOWED_INTERVAL_MINUTES
    assert cfg.wfm_forecast_horizon_days >= 1
    assert cfg.custom_domain_cname_target
    assert cfg.push_quiet_hours_max_span >= 1


def test_unsupported_interval_is_refused_rather_than_rounded(monkeypatch):
    monkeypatch.setenv("WFM_INTERVAL_MINUTES", "20")
    plat_settings.reset_plat2_settings_cache()
    with pytest.raises(ValidationError):
        plat_settings.get_plat2_settings()


def test_quiet_hours_span_ceiling_is_bounded(monkeypatch):
    for bad in ("0", "25", "-3"):
        monkeypatch.setenv("PUSH_QUIET_HOURS_MAX_SPAN", bad)
        plat_settings.reset_plat2_settings_cache()
        with pytest.raises(ValidationError):
            plat_settings.get_plat2_settings()


def test_custom_domain_target_must_be_a_hostname(monkeypatch):
    monkeypatch.setenv("CUSTOM_DOMAIN_CNAME_TARGET", "https://edge.example.com/")
    plat_settings.reset_plat2_settings_cache()
    with pytest.raises(ValidationError):
        plat_settings.get_plat2_settings()


def test_custom_domain_target_is_normalised(monkeypatch):
    monkeypatch.setenv("CUSTOM_DOMAIN_CNAME_TARGET", "Edge.Example.COM.")
    plat_settings.reset_plat2_settings_cache()
    assert plat_settings.get_plat2_settings().custom_domain_cname_target == "edge.example.com"


def test_history_window_must_cover_two_weekly_cycles(monkeypatch):
    monkeypatch.setenv("WFM_MIN_HISTORY_DAYS", "7")
    plat_settings.reset_plat2_settings_cache()
    with pytest.raises(ValidationError):
        plat_settings.get_plat2_settings()


def test_scheduler_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("PLAT2_SCHEDULER_ENABLED", "false")
    plat_settings.reset_plat2_settings_cache()
    assert plat_settings.get_plat2_settings().scheduler_enabled is False


def test_enabling_a_push_provider_without_credentials_is_refused(monkeypatch):
    """A half-configured provider must fail at boot, not on the first notification."""
    monkeypatch.setenv("FCM_ENABLED", "true")
    plat_settings.reset_plat2_settings_cache()
    with pytest.raises(ValidationError):
        plat_settings.get_plat2_settings()

    monkeypatch.setenv("APNS_ENABLED", "true")
    plat_settings.reset_plat2_settings_cache()
    with pytest.raises(ValidationError):
        plat_settings.get_plat2_settings()


def test_production_with_a_public_host_requires_the_ask_token(monkeypatch):
    """Without a token, Caddy's ask hook would approve any hostname pointed at us."""
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://api.voxdesk.example")
    monkeypatch.setenv("TLS_ASK_ENABLED", "true")
    plat_settings.reset_plat2_settings_cache()
    with pytest.raises(ValidationError):
        plat_settings.get_plat2_settings()

    monkeypatch.setenv("TLS_ASK_TOKEN", "ask-token-for-tests")
    plat_settings.reset_plat2_settings_cache()
    assert plat_settings.get_plat2_settings().tls_ask_token == "ask-token-for-tests"


def test_a_localhost_deployment_does_not_impose_the_ask_token(monkeypatch):
    monkeypatch.setenv("TLS_ASK_ENABLED", "true")
    plat_settings.reset_plat2_settings_cache()
    cfg = plat_settings.get_plat2_settings()
    assert cfg.on_demand_tls_in_use is False


def test_settings_object_is_cached_until_reset():
    first = plat_settings.get_plat2_settings()
    assert plat_settings.get_plat2_settings() is first
    plat_settings.reset_plat2_settings_cache()
    assert plat_settings.get_plat2_settings() is not first


def test_derived_urls_follow_the_sandbox_flag(monkeypatch):
    monkeypatch.setenv("APNS_USE_SANDBOX", "true")
    plat_settings.reset_plat2_settings_cache()
    assert plat_settings.get_plat2_settings().apns_base_url.endswith("api.sandbox.push.apple.com")
    monkeypatch.setenv("APNS_USE_SANDBOX", "false")
    plat_settings.reset_plat2_settings_cache()
    assert plat_settings.get_plat2_settings().apns_base_url.endswith("api.push.apple.com")


def test_resolver_defaults_to_a_public_https_resolver():
    cfg = plat_settings.get_plat2_settings()
    assert cfg.resolver_urls and all(url.startswith("https://") for url in cfg.resolver_urls)
