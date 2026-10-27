"""APNs client: ES256 provider token, JOSE signatures, headers and error mapping.

The single most common hand-rolled-APNs bug is signing with the DER form
``cryptography`` returns instead of the JOSE ``r||s`` form; the first test here
verifies the raw 64-byte signature against the public key, which is exactly what
Apple does.
"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from app.platform_v2.errors import ProviderRejected, ValidationFailed
from app.platform_v2.mobile import apns


def _b64d(value: str) -> bytes:
    padding_needed = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding_needed)


@pytest.fixture(scope="module")
def ec_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(scope="module")
def config(ec_key) -> apns.ApnsConfig:
    pem = ec_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return apns.ApnsConfig(
        key_id="ABC123DEFG",
        team_id="TEAM123456",
        private_key_p8=pem,
        bundle_id="ai.voxdesk.app",
        use_sandbox=True,
    )


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str], dict]] = []

    async def __call__(self, url, headers, payload):
        self.calls.append((url, dict(headers), payload))
        if not self.responses:
            raise AssertionError(f"unexpected extra APNs call to {url}")
        return self.responses.pop(0)


def test_the_provider_token_signature_is_jose_raw_not_der(config, ec_key):
    token = apns.build_provider_token(config, now=1_700_000_000)
    header_b64, claims_b64, signature_b64 = token.split(".")
    header = json.loads(_b64d(header_b64))
    claims = json.loads(_b64d(claims_b64))
    assert header == {"alg": "ES256", "kid": "ABC123DEFG"}
    assert claims["iss"] == "TEAM123456"
    assert claims["iat"] == 1_700_000_000

    raw = _b64d(signature_b64)
    assert len(raw) == 64, "JOSE requires a raw 64-byte r||s signature"
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    ec_key.public_key().verify(
        encode_dss_signature(r, s),
        f"{header_b64}.{claims_b64}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )


def test_a_non_ec_key_is_refused_rather_than_producing_a_bad_token():
    from cryptography.hazmat.primitives.asymmetric import rsa

    pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode()
    )
    wrong = apns.ApnsConfig(key_id="K", team_id="T", private_key_p8=pem, bundle_id="ai.voxdesk.app")
    with pytest.raises(ValidationFailed) as caught:
        apns.build_provider_token(wrong)
    assert "P-256" in str(caught.value)


def test_a_garbage_private_key_is_a_validation_failure():
    broken = apns.ApnsConfig(key_id="K", team_id="T", private_key_p8="not a key", bundle_id="b")
    with pytest.raises(ValidationFailed) as caught:
        apns.build_provider_token(broken)
    assert "could not be loaded" in str(caught.value)


def test_the_host_follows_the_sandbox_flag_and_an_explicit_endpoint():
    base = dict(key_id="K", team_id="T", private_key_p8="k", bundle_id="b")
    assert apns.ApnsConfig(**base, use_sandbox=True).host == apns.SANDBOX_HOST
    assert apns.ApnsConfig(**base, use_sandbox=False).host == apns.PRODUCTION_HOST
    assert apns.ApnsConfig(**base, endpoint="https://apns.local/").host == "https://apns.local"


def test_the_alert_payload_flattens_custom_keys_beside_aps():
    message = apns.ApnsMessage(
        device_token="tok",
        title="Shift published",
        body="Week of 3 Nov",
        data={"shift_id": "42", "queue": "sales"},
        badge=3,
        thread_id="rota",
    )
    payload = message.payload()
    assert payload["aps"]["alert"] == {"title": "Shift published", "body": "Week of 3 Nov"}
    assert payload["aps"]["sound"] == "default"
    assert payload["aps"]["badge"] == 3
    assert payload["aps"]["thread-id"] == "rota"
    assert payload["shift_id"] == "42"
    assert payload["queue"] == "sales"


def test_a_background_push_is_silent_and_cannot_carry_an_alert():
    payload = apns.ApnsMessage(
        device_token="tok", title="ignored", body="ignored", push_type="background"
    ).payload()
    # APNs rejects an alert inside a background push, so the aps dict is replaced.
    assert payload["aps"] == {"content-available": 1}
    assert "alert" not in payload["aps"]


def test_a_data_key_called_aps_cannot_overwrite_the_envelope():
    payload = apns.ApnsMessage(
        device_token="tok", title="t", body="b", data={"aps": {"alert": "spoof"}}
    ).payload()
    assert payload["aps"]["alert"] == {"title": "t", "body": "b"}


def test_headers_carry_topic_type_priority_and_a_collapse_id():
    message = apns.ApnsMessage(
        device_token="tok", title="t", body="b", collapse_id="rota-2026-11-03", immediate=False, expiry_seconds=60
    )
    headers = message.headers(bundle_id="ai.voxdesk.app", provider_token="jwt")
    assert headers["authorization"] == "bearer jwt"
    assert headers["apns-topic"] == "ai.voxdesk.app"
    assert headers["apns-push-type"] == "alert"
    assert headers["apns-priority"] == str(apns.PRIORITY_DEFERRED)
    assert headers["apns-collapse-id"] == "rota-2026-11-03"
    # apns-id must be a UUID, because a retry of the same id replaces the notification.
    assert len(headers["apns-id"]) == 36


def test_a_long_collapse_id_is_truncated_to_the_64_byte_limit():
    headers = apns.ApnsMessage(
        device_token="tok", title="t", body="b", collapse_id="x" * 100
    ).headers(bundle_id="b", provider_token="jwt")
    assert len(headers["apns-collapse-id"]) == 64


def test_bad_device_token_is_permanent_and_too_many_requests_is_transient():
    permanent = apns.classify(410, {"reason": "Unregistered"})
    assert permanent.permanent is True
    assert permanent.reason == "Unregistered"

    transient = apns.classify(429, {"reason": "TooManyRequests"})
    assert transient.transient is True
    assert transient.permanent is False


def test_a_500_without_a_reason_is_still_transient():
    result = apns.classify(503, {})
    assert result.transient is True
    assert result.reason == "HTTP_503"


def test_a_400_with_an_unrecognised_reason_is_permanent_not_silently_retried():
    result = apns.classify(400, {"reason": "PayloadTooLarge"})
    assert result.ok is False
    assert result.permanent is True
    assert result.reason == "PayloadTooLarge"


def test_a_200_keeps_the_apns_id():
    result = apns.classify(200, {"apns-id": "3f0d"})
    assert result.ok is True
    assert result.apns_id == "3f0d"


async def test_send_posts_to_the_device_path_with_a_cached_token(config):
    recorder = Recorder(
        [
            (200, {"apns-id": "a1"}),
            (200, {"apns-id": "a2"}),
        ]
    )
    issued = {"value": 1_000.0}
    client = apns.ApnsClient(config, transport=recorder, clock=lambda: issued["value"])

    first = await client.send(apns.ApnsMessage(device_token="dev-1", title="t", body="b"))
    issued["value"] = 1_000.0 + 60
    second = await client.send(apns.ApnsMessage(device_token="dev-2", title="t", body="b"))

    assert first.ok and first.apns_id == "a1"
    assert second.ok
    url, headers, _payload = recorder.calls[0]
    assert url == f"{apns.SANDBOX_HOST}/3/device/dev-1"
    assert headers["apns-topic"] == "ai.voxdesk.app"
    # One provider token covers both sends: Apple rate-limits token rotation.
    tokens = {headers["authorization"] for _url, headers, _payload in recorder.calls}
    assert len(tokens) == 1


async def test_the_provider_token_is_rotated_after_its_ttl(config):
    recorder = Recorder([(200, {}), (200, {})])
    issued = {"value": 1_000.0}
    client = apns.ApnsClient(config, transport=recorder, clock=lambda: issued["value"])

    await client.send(apns.ApnsMessage(device_token="dev-1", title="t", body="b"))
    issued["value"] = 1_000.0 + apns.TOKEN_TTL_SECONDS + 1
    await client.send(apns.ApnsMessage(device_token="dev-1", title="t", body="b"))

    tokens = [headers["authorization"] for _url, headers, _payload in recorder.calls]
    assert tokens[0] != tokens[1]


async def test_an_unknown_push_type_never_reaches_the_network(config):
    recorder = Recorder([])
    client = apns.ApnsClient(config, transport=recorder, clock=lambda: 1_000.0)
    with pytest.raises(ValidationFailed) as caught:
        await client.send(apns.ApnsMessage(device_token="d", title="t", body="b", push_type="shout"))
    assert caught.value.detail["push_type"] == "shout"
    assert recorder.calls == []


async def test_a_rejected_send_is_returned_not_raised(config):
    recorder = Recorder([(410, {"reason": "Unregistered"})])
    client = apns.ApnsClient(config, transport=recorder, clock=lambda: 1_000.0)
    result = await client.send(apns.ApnsMessage(device_token="d", title="t", body="b"))
    assert result.ok is False
    assert result.permanent is True
    assert result.as_dict()["reason"] == "Unregistered"


async def test_from_settings_requires_all_four_credentials(monkeypatch):
    from app.platform_v2 import settings as settings_mod

    monkeypatch.setenv("APNS_ENABLED", "true")
    monkeypatch.setenv("APNS_KEY_ID", "K")
    monkeypatch.setenv("APNS_TEAM_ID", "")
    monkeypatch.setenv("APNS_PRIVATE_KEY_P8", "")
    monkeypatch.setenv("APNS_BUNDLE_ID", "")
    settings_mod.reset_plat2_settings_cache()
    try:
        with pytest.raises(Exception) as caught:
            apns.ApnsClient.from_settings()
        text = str(caught.value)
        assert "APNS_TEAM_ID" in text or "incomplete" in text
    finally:
        settings_mod.reset_plat2_settings_cache()


def test_every_product_template_uses_a_visible_alert_push():
    for template in ("rota_published", "shift_swap_approved", "time_off_decided", "queue_breach"):
        # A background push may be coalesced by iOS, so none of these may be silent.
        assert apns.push_type_for(template) == "alert"
