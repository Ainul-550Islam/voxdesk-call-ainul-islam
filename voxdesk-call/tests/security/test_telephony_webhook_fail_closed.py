"""Generic telephony webhooks must fail closed when no signing secret is configured.

Regression for the public default secret that previously let anyone forge a webhook
on a deployment that had not set TELEPHONY_WEBHOOK_SECRET.
"""

import time

import pytest

from app.telephony import webhooks
from app.telephony.exceptions import ProviderWebhookVerificationError
from app.telephony.webhooks import (
    compute_webhook_hmac_signature,
    get_webhook_signing_secret,
    verify_incoming_webhook_signature,
)

OLD_PUBLIC_DEFAULT = "voxdesk-telephony-webhook-secret"
BODY = b'{"event":"call_started"}'


@pytest.fixture
def unset_secret(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "telephony_webhook_secret", "", raising=False)
    monkeypatch.setattr(webhooks.settings, "twilio_auth_token", "", raising=False)
    monkeypatch.setattr(webhooks.settings, "vonage_api_secret", "", raising=False)
    monkeypatch.setattr(webhooks.settings, "telnyx_public_key", "", raising=False)


def _headers(secret: str) -> dict[str, str]:
    ts = str(int(time.time()))
    return {
        "x-voxdesk-timestamp": ts,
        "x-voxdesk-signature": compute_webhook_hmac_signature(
            secret=secret, raw_body=BODY, timestamp=ts
        ),
    }


def test_no_secret_configured_raises(unset_secret):
    with pytest.raises(ProviderWebhookVerificationError):
        get_webhook_signing_secret("generic")


def test_public_default_signature_is_rejected_when_unset(unset_secret):
    with pytest.raises(ProviderWebhookVerificationError):
        verify_incoming_webhook_signature(
            provider="generic", headers=_headers(OLD_PUBLIC_DEFAULT), raw_body=BODY
        )


def test_public_default_is_not_a_fallback_when_secret_is_set(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "telephony_webhook_secret", "operator-secret-123", raising=False)
    with pytest.raises(ProviderWebhookVerificationError):
        verify_incoming_webhook_signature(
            provider="generic", headers=_headers(OLD_PUBLIC_DEFAULT), raw_body=BODY
        )


def test_configured_secret_verifies(monkeypatch):
    monkeypatch.setattr(webhooks.settings, "telephony_webhook_secret", "operator-secret-123", raising=False)
    verify_incoming_webhook_signature(
        provider="generic", headers=_headers("operator-secret-123"), raw_body=BODY
    )


def test_no_public_default_constant_remains():
    import inspect

    assert not hasattr(webhooks, "DEFAULT_WEBHOOK_SECRET")
    assert OLD_PUBLIC_DEFAULT not in inspect.getsource(webhooks)
