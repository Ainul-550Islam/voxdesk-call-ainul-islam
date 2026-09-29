"""Lineage privacy and fingerprint coverage."""

from __future__ import annotations

from app.auth.identity.events import scrub
from app.governance.hashing import fingerprint


def test_lineage_uses_fingerprints_and_scrubs_credentials_from_metadata():
    raw = {"request": "safe-id", "access_token": "not-stored"}
    safe = scrub(raw)
    assert safe["access_token"] == "***"
    assert fingerprint({"input": "same"}) == fingerprint({"input": "same"})
    assert fingerprint({"input": "same"}) != fingerprint({"input": "different"})
