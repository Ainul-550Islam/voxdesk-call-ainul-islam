"""A production deployment must refuse to boot on a weak or default secret.

The repository ships a development default for JWT_SECRET on purpose — the app
has to start on a laptop — which makes the guard, not the default, the control.
``app/main.py`` raises ``RuntimeError`` during startup when
``settings.validate_security()`` reports anything and ``APP_ENV`` is production;
in every other environment the same problems are logged as warnings so
development stays usable.

These tests drive the real guard rather than a copy of its rules, and add a
wiring assertion so deleting the raise in ``main.py`` fails the suite.
"""
from __future__ import annotations

import inspect

import pytest

from app.core.config import Settings, settings

DEFAULT_JWT_SECRET = str(Settings.model_fields["jwt_secret"].default)
STRONG_SECRET = "a" * 48 + "-B9!qz"


@pytest.fixture
def production(monkeypatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "public_base_url", "https://api.example.com")
    monkeypatch.setattr(settings, "jwt_secret", STRONG_SECRET)
    monkeypatch.setattr(settings, "secret_key", STRONG_SECRET)


def test_the_shipped_default_is_itself_refused(monkeypatch) -> None:
    monkeypatch.setattr(settings, "jwt_secret", DEFAULT_JWT_SECRET)
    problems = settings.validate_security()
    assert any("built-in default" in problem for problem in problems), (
        f"the shipped JWT_SECRET default ({DEFAULT_JWT_SECRET!r}) is accepted; "
        "every deployment that forgets to set it would sign tokens with a "
        "publicly known key"
    )


def test_a_secret_below_the_length_floor_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(settings, "jwt_secret", "short-but-not-32-chars")
    problems = settings.validate_security()
    assert any("at least 32 characters" in problem for problem in problems), (
        "a 24-character JWT secret passes validation"
    )


def test_an_empty_secret_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(settings, "jwt_secret", "")
    problems = settings.validate_security()
    assert any("JWT_SECRET" in problem for problem in problems)


def test_a_strong_secret_produces_no_jwt_finding(monkeypatch) -> None:
    monkeypatch.setattr(settings, "jwt_secret", STRONG_SECRET)
    monkeypatch.setattr(settings, "secret_key", STRONG_SECRET)
    problems = settings.validate_security()
    jwt_problems = [p for p in problems if "JWT_SECRET" in p or "SECRET_KEY" in p]
    assert not jwt_problems, (
        f"a 48-character non-default secret is still reported: {jwt_problems}. "
        "A guard that always complains is a guard operators learn to ignore."
    )


def test_the_default_credential_marker_for_secret_key_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(settings, "secret_key", "change-me")
    monkeypatch.setattr(settings, "jwt_secret", STRONG_SECRET)
    problems = settings.validate_security()
    assert any("SECRET_KEY" in problem for problem in problems), (
        "the literal 'change-me' SECRET_KEY is accepted"
    )


def test_production_requires_signed_telephony_webhooks(production, monkeypatch) -> None:
    monkeypatch.setattr(settings, "telephony_webhook_secret", "")
    problems = settings.validate_security()
    assert any("TELEPHONY_WEBHOOK_SECRET" in problem for problem in problems), (
        "production would boot with unsigned telephony webhooks"
    )


def test_production_refuses_to_disable_twilio_signature_verification(
    production, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "twilio_skip_webhook_verify", True)
    problems = settings.validate_security()
    assert any("TWILIO_SKIP_WEBHOOK_VERIFY" in problem for problem in problems), (
        "production would boot with Twilio signature verification disabled, which "
        "lets anyone who learns the webhook URL forge call events"
    )


def test_production_requires_https_public_base_url(production, monkeypatch) -> None:
    monkeypatch.setattr(settings, "public_base_url", "http://api.example.com")
    problems = settings.validate_security()
    assert any("PUBLIC_BASE_URL" in problem for problem in problems)


def test_production_refuses_e2e_mode(production, monkeypatch) -> None:
    monkeypatch.setattr(settings, "e2e_enabled", True)
    monkeypatch.setattr(settings, "e2e_test_number", "+15550001111")
    monkeypatch.setattr(settings, "e2e_allowed_callers", "+15550001111")
    problems = settings.validate_security()
    assert any("E2E_ENABLED" in problem for problem in problems), (
        "real-call E2E mode is allowed in production"
    )


def test_the_startup_guard_raises_in_production_and_only_in_production() -> None:
    """Wiring assertion: the guard must be wired to a raise, not to a log line.

    This reads ``app.main``'s lifespan source rather than executing it, because
    booting the real lifespan needs a database and a provider configuration. It
    asserts the shape of the guard that already exists: problems are collected,
    and production escalates them into a refusal.
    """
    from app.main import lifespan

    source = inspect.getsource(lifespan)
    assert "validate_security()" in source, (
        "the lifespan no longer calls settings.validate_security()"
    )
    assert "is_production" in source, (
        "the lifespan no longer distinguishes production from other environments"
    )
    assert "raise RuntimeError" in source, (
        "insecure configuration is no longer fatal in production; a warning that "
        "nobody reads is not a control"
    )


def test_development_logs_instead_of_refusing() -> None:
    """The same problems must stay non-fatal outside production."""
    from app.main import lifespan

    source = inspect.getsource(lifespan)
    raise_index = source.index("raise RuntimeError")
    guard = source[:raise_index]
    assert "if settings.is_production" in guard, (
        "the production-only escalation is gone; either every environment now "
        "refuses to start, or none does"
    )
