"""The Vonage capability flag: the live media stream is only offered when
the whole credential set is present, and the HTTP surface answers 501
instead of pretending that the feature works."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.telephony.provider_errors import UnsupportedCapability
from app.telephony.providers.vonage import VonageAdapter, vonage_media_capabilities


@pytest.fixture
def vonage_credentials(monkeypatch):
    """A complete Vonage credential set, with a real RS256 key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    monkeypatch.setattr(settings, "vonage_api_key", "key-1", raising=False)
    monkeypatch.setattr(settings, "vonage_api_secret", "secret-1", raising=False)
    monkeypatch.setattr(settings, "vonage_application_id", "app-1", raising=False)
    monkeypatch.setattr(settings, "vonage_private_key", pem, raising=False)
    monkeypatch.setattr(settings, "vonage_signature_secret", "sig-1", raising=False)
    return pem


def test_media_streaming_is_refused_without_credentials() -> None:
    adapter = VonageAdapter()
    assert adapter.supports_ai_media_stream is False
    with pytest.raises(UnsupportedCapability):
        adapter.require_media_streaming()


def test_media_streaming_is_available_with_the_full_credential_set(
    vonage_credentials,
) -> None:
    adapter = VonageAdapter()
    assert adapter.supports_ai_media_stream is True


def test_capability_row_is_not_configured_without_credentials() -> None:
    row = vonage_media_capabilities()["vonage"]
    assert row["supported"] is False
    assert row["serializer"] is None


