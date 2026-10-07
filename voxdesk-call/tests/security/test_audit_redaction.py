"""Regression coverage for secret-key redaction versus policy metadata."""
from __future__ import annotations

from app.audit.redaction import REDACTED, is_sensitive_key, redact_tree


def test_token_lifetime_metadata_remains_auditable_but_token_values_are_redacted():
    changes = {"refresh_token_days": 7, "session_idle_minutes": 60}

    assert not is_sensitive_key("refresh_token_days")
    assert redact_tree(changes) == changes
    assert is_sensitive_key("refresh_token")
    assert redact_tree({"refresh_token": "actual-secret"})["refresh_token"] == REDACTED


def test_token_digest_and_credential_fields_stay_sensitive():
    for name in ("access_token", "token_hash", "api_key", "client_secret"):
        assert is_sensitive_key(name), name
