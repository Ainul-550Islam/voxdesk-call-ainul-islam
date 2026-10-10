"""Every adapter fails closed. A missing credential is not a successful call."""

from __future__ import annotations

import time

import pytest
from starlette.requests import Request

from app.core.config import settings
from app.telephony.provider_errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
    UnsupportedCapability,
)
from app.telephony.providers.factory import build, public_config, select
from app.telephony.providers.telnyx import TelnyxAdapter
from app.telephony.providers.twilio import TwilioAdapter
from app.telephony.providers.vonage import VonageAdapter
from app.telephony.providers.webhook_verifier import verify
from app.telephony.providers.base import HttpResult

pytestmark = pytest.mark.asyncio


def _request(path="/telephony/status", headers=None, body=b"", query=b""):
    raw = []
    for key, value in (headers or {}).items():
        raw.append((key.lower().encode(), value.encode()))
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query,
        "headers": raw,
        "client": ("127.0.0.1", 9),
        "server": ("example.com", 443),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _clear(monkeypatch):
    for name in (
        "twilio_account_sid",
        "twilio_auth_token",
        "twilio_phone_number",
        "twilio_whatsapp_number",
        "telnyx_api_key",
        "telnyx_public_key",
        "telnyx_connection_id",
        "vonage_api_key",
        "vonage_api_secret",
        "vonage_signature_secret",
        "vonage_application_id",
        "vonage_private_key",
    ):
        monkeypatch.setattr(settings, name, "", raising=False)


class _Transport:
    def __init__(self, status=200, body=None, explode=None):
        self.status = status
        self.body = body or {}
        self.explode = explode
        self.calls = []

    async def __call__(self, method, url, *, headers, json, timeout):
        self.calls.append({"method": method, "url": url, "json": json})
        if self.explode:
            raise self.explode
        return HttpResult(self.status, self.body)


async def test_each_adapter_rejects_missing_credentials(monkeypatch):
    _clear(monkeypatch)
    for adapter in (TwilioAdapter(), TelnyxAdapter(), VonageAdapter()):
        assert adapter.configured() is False
        assert adapter.health().ok is False
        with pytest.raises(ProviderConfigurationError):
            adapter.require_configured()
        with pytest.raises(ProviderConfigurationError):
            await adapter.search_numbers(country="US")


async def test_unknown_provider_and_secret_are_not_returned(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setattr(settings, "telnyx_api_key", "TELNYX-SECRET-VALUE", raising=False)
    with pytest.raises(ProviderValidationError):
        build("carrier-x")
    view = public_config()
    rendered = str(view)
    assert "TELNYX-SECRET-VALUE" not in rendered
    assert {row["provider"] for row in view} == {"twilio", "telnyx", "vonage"}
    assert any(row["provider"] == "telnyx" and row["configured"] is True for row in view)


async def test_disabled_provider_is_rejected(db, tenant_a):
    from app.telephony.providers.factory import enable

    await enable(db, tenant_a.id, "telnyx", enabled=False)
    with pytest.raises(ProviderUnavailableError):
        await select(db, tenant_a.id, requested="telnyx")


async def test_telnyx_maps_http_errors(monkeypatch):
    monkeypatch.setattr(settings, "telnyx_api_key", "key", raising=False)
    unauthorized = TelnyxAdapter(_Transport(401))
    with pytest.raises(ProviderAuthenticationError):
        await unauthorized.search_numbers(country="US")
    limited = TelnyxAdapter(_Transport(429))
    with pytest.raises(ProviderRateLimitedError):
        await limited.search_numbers(country="US")
    timed = TelnyxAdapter(_Transport(explode=TimeoutError("slow")))
    with pytest.raises(ProviderTimeoutError):
        await timed.search_numbers(country="US")
    down = TelnyxAdapter(_Transport(503))
    with pytest.raises(ProviderUnavailableError):
        await down.search_numbers(country="US")


async def test_telnyx_does_not_invent_a_call_or_whatsapp(monkeypatch):
    monkeypatch.setattr(settings, "telnyx_api_key", "key", raising=False)
    monkeypatch.setattr(settings, "telnyx_connection_id", "", raising=False)
    adapter = TelnyxAdapter(_Transport(200, {"data": {}}))
    with pytest.raises(ProviderConfigurationError):
        await adapter.create_outbound(to_number="+15551212", from_number="+15551213")
    with pytest.raises(UnsupportedCapability):
        await adapter.send_whatsapp("+15551212", "hello")
    monkeypatch.setattr(settings, "telnyx_connection_id", "conn", raising=False)
    empty = TelnyxAdapter(_Transport(200, {"data": {}}))
    with pytest.raises(ProviderUnavailableError):
        await empty.create_outbound(to_number="+15551212000", from_number="+15551213000")


async def test_telnyx_search_uses_only_returned_features(monkeypatch):
    monkeypatch.setattr(settings, "telnyx_api_key", "key", raising=False)
    body = {"data": [{"phone_number": "+15557654321", "features": ["voice", "sms"]}]}
    adapter = TelnyxAdapter(_Transport(200, body))
    found = await adapter.search_numbers(country="US")
    assert found[0].capabilities.voice is True
    assert found[0].capabilities.sms is True
    assert found[0].capabilities.whatsapp is False
    assert found[0].capabilities.mms is False
    assert found[0].capabilities.source == "provider_api"


async def test_vonage_voice_and_recording_fail_clearly(monkeypatch):
    monkeypatch.setattr(settings, "vonage_api_key", "key", raising=False)
    monkeypatch.setattr(settings, "vonage_api_secret", "secret", raising=False)
    adapter = VonageAdapter()
    with pytest.raises(ProviderConfigurationError):
        await adapter.create_outbound(to_number="+15551212000", from_number="+15551213000")
    with pytest.raises(UnsupportedCapability):
        await adapter.start_recording("call-1")
    with pytest.raises(UnsupportedCapability):
        await adapter.send_whatsapp()
    monkeypatch.setattr(settings, "vonage_application_id", "app", raising=False)
    monkeypatch.setattr(
        settings,
        "vonage_private_key",
        f"-----BEGIN {'PRIVATE'} KEY-----\nabc\n-----END {'PRIVATE'} KEY-----",
        raising=False,
    )
    with pytest.raises(UnsupportedCapability):
        await adapter.create_outbound(to_number="+15551212000", from_number="+15551213000")


async def test_vonage_number_search_parses_features_and_hides_secret(monkeypatch):
    monkeypatch.setattr(settings, "vonage_api_key", "VONAGE-KEY", raising=False)
    monkeypatch.setattr(settings, "vonage_api_secret", "VONAGE-SECRET", raising=False)
    transport = _Transport(200, {"numbers": [{"msisdn": "15557650000", "features": ["VOICE"]}]})
    adapter = VonageAdapter(transport)
    found = await adapter.search_numbers(country="US")
    assert found[0].e164 == "+15557650000"
    assert found[0].capabilities.voice is True
    assert found[0].capabilities.sms is False
    assert "VONAGE-SECRET" not in found[0].as_dict().__repr__()
    assert "api_secret" not in found[0].as_dict()


async def test_twilio_webhook_fail_closed_and_other_providers_reject(monkeypatch):
    monkeypatch.setattr(settings, "twilio_skip_webhook_verify", False, raising=False)
    monkeypatch.setattr(settings, "telnyx_public_key", "", raising=False)
    monkeypatch.setattr(settings, "vonage_signature_secret", "", raising=False)
    twilio = await verify("twilio", _request())
    assert twilio.valid is False
    assert (await verify("nope", _request())).valid is False
    telnyx = await verify("telnyx", _request("/telephony/telnyx"))
    assert telnyx.valid is False
    assert telnyx.reason == "configuration_missing"
    stale = await verify(
        "telnyx",
        _request(
            "/telephony/telnyx",
            {"telnyx-signature-ed25519": "abc", "telnyx-timestamp": "1"},
            b"{}",
        ),
    )
    monkeypatch.setattr(settings, "telnyx_public_key", "not-a-key", raising=False)
    stale = await verify(
        "telnyx",
        _request(
            "/telephony/telnyx",
            {"telnyx-signature-ed25519": "abc", "telnyx-timestamp": "1"},
            b"{}",
        ),
    )
    assert stale.valid is False
    assert stale.reason == "timestamp_rejected"
    wrong_path = await verify("vonage", _request("/evil"))
    assert wrong_path.reason == "endpoint_mismatch"
    missing = await verify("vonage", _request("/telephony/vonage"))
    assert missing.reason == "configuration_missing"


async def test_vonage_jwt_accepts_only_a_fresh_signature(monkeypatch):
    monkeypatch.setattr(settings, "vonage_signature_secret", "signing-secret", raising=False)
    import base64
    import hashlib
    import hmac
    import json

    def token(claims):
        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).rstrip(b"=").decode()
        )
        body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        sig = (
            base64.urlsafe_b64encode(
                hmac.new(b"signing-secret", f"{header}.{body}".encode(), hashlib.sha256).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        return f"{header}.{body}.{sig}"

    now = int(time.time())
    good = await verify(
        "vonage",
        _request(
            "/telephony/vonage", {"authorization": f"Bearer {token({'iat': now, 'exp': now + 60})}"}
        ),
    )
    assert good.valid is True
    bad = await verify(
        "vonage",
        _request("/telephony/vonage", {"authorization": "Bearer a.b.c"}),
    )
    assert bad.valid is False
    expired = await verify(
        "vonage",
        _request(
            "/telephony/vonage",
            {"authorization": f"Bearer {token({'iat': now - 1000, 'exp': now - 10})}"},
        ),
    )
    assert expired.reason == "timestamp_rejected"


async def test_twilio_capabilities_follow_configuration(monkeypatch):
    _clear(monkeypatch)
    bare = TwilioAdapter()
    assert bare.capabilities().voice is False
    monkeypatch.setattr(settings, "twilio_account_sid", "AC123", raising=False)
    monkeypatch.setattr(settings, "twilio_auth_token", "token", raising=False)
    monkeypatch.setattr(settings, "twilio_phone_number", "+15550001111", raising=False)
    monkeypatch.setattr(settings, "twilio_whatsapp_number", "", raising=False)
    caps = TwilioAdapter().capabilities()
    assert caps.voice is True
    assert caps.sms is True
    assert caps.recording is True
    assert caps.whatsapp is False
    assert caps.transcription is False
    with pytest.raises(ProviderUnavailableError):
        await TwilioAdapter().search_numbers(country="US")
