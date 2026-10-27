"""FCM v1 client: JWT assertion, token caching, payload shape and error mapping.

Nothing here reaches Google. The transport is injected, the RSA key is generated in
the test, and the signatures are verified locally with the same public key — so a
test failure means the message would have been rejected, not that the network was
down.
"""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.platform_v2.errors import ProviderRejected, ValidationFailed
from app.platform_v2.mobile import fcm


def _b64d(value: str) -> bytes:
    padding_needed = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding_needed)


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def account(rsa_key) -> fcm.ServiceAccount:
    pem = rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return fcm.ServiceAccount(
        project_id="voxdesk-test",
        client_email="pusher@voxdesk-test.iam.gserviceaccount.com",
        private_key_pem=pem,
    )


class Recorder:
    """Transport double that records every call and replays scripted answers."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, str], dict | None]] = []

    async def __call__(self, method, url, headers, payload):
        self.calls.append((method, url, dict(headers), payload))
        if not self.responses:
            raise AssertionError(f"unexpected extra FCM call to {url}")
        return self.responses.pop(0)


def _token_response(token: str = "ya29.test", expires_in: int = 3600):
    return 200, {"access_token": token, "expires_in": expires_in, "token_type": "Bearer"}


def test_service_account_json_requires_the_three_essential_fields():
    with pytest.raises(ValidationFailed) as caught:
        fcm.ServiceAccount.from_json(json.dumps({"project_id": "p"}))
    assert sorted(caught.value.detail["missing"]) == ["client_email", "private_key"]


def test_service_account_json_rejects_garbage_and_empty_values():
    with pytest.raises(ValidationFailed):
        fcm.ServiceAccount.from_json("{not json")
    with pytest.raises(ProviderRejected):
        fcm.ServiceAccount.from_json("   ")


def test_service_account_json_unescapes_newlines_in_the_pem(account):
    raw = json.dumps(
        {
            "project_id": account.project_id,
            "client_email": account.client_email,
            # A JSON-escaped PEM is what a Kubernetes secret hands us.
            "private_key": account.private_key_pem.replace("\n", "\\n"),
        }
    )
    parsed = fcm.ServiceAccount.from_json(raw)
    assert "\n" in parsed.private_key_pem
    assert parsed.private_key_pem.startswith("-----BEGIN PRIVATE KEY-----")


def test_the_assertion_is_a_verifiable_rs256_jwt(account, rsa_key):
    assertion = fcm.build_assertion(account, now=1_700_000_000)
    header_b64, claims_b64, signature_b64 = assertion.split(".")
    header = json.loads(_b64d(header_b64))
    claims = json.loads(_b64d(claims_b64))
    assert header == {"alg": "RS256", "typ": "JWT"}
    assert claims["iss"] == account.client_email
    assert claims["scope"] == fcm.FCM_SCOPE
    assert claims["iat"] == 1_700_000_000
    assert claims["exp"] == 1_700_000_000 + 3600
    # A signature that does not verify means Google would answer 401 invalid_grant.
    rsa_key.public_key().verify(
        _b64d(signature_b64),
        f"{header_b64}.{claims_b64}".encode(),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )


def test_payload_uses_the_v1_envelope_and_stringifies_data():
    message = fcm.PushMessage(
        token="tok-1",
        title="Shift published",
        body="Your week of 3 Nov is ready",
        data={"shift_id": 42, "queue": "sales"},
        collapse_key="rota",
        high_priority=True,
    )
    envelope = message.as_payload()
    assert set(envelope) == {"message"}
    payload = envelope["message"]
    assert payload["token"] == "tok-1"
    assert payload["notification"] == {"title": "Shift published", "body": "Your week of 3 Nov is ready"}
    assert payload["android"]["priority"] == "HIGH"
    assert payload["android"]["collapse_key"] == "rota"
    # `tag` is what actually replaces the older notification in the shade.
    assert payload["android"]["notification"] == {"tag": "rota"}
    # FCM rejects non-string data values; the int must have been coerced.
    assert payload["data"] == {"shift_id": "42", "queue": "sales"}


def test_a_quiet_message_is_normal_priority_without_android_overrides():
    payload = fcm.PushMessage(token="t", title="a", body="b").as_payload()["message"]
    assert payload["android"] == {"priority": "NORMAL"}
    assert "data" not in payload


def test_unregistered_is_permanent_and_quota_is_transient():
    unregistered = fcm.classify(
        404,
        {
            "error": {
                "status": "NOT_FOUND",
                "message": "Requested entity was not found.",
                "details": [{"@type": "type.googleapis.com/google.firebase.fcm.v1.FcmError", "errorCode": "UNREGISTERED"}],
            }
        },
    )
    # details[].errorCode is more specific than the gRPC status, so it wins.
    assert unregistered.error_code == "UNREGISTERED"
    assert unregistered.permanent is True
    assert unregistered.transient is False

    quota = fcm.classify(429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded"}})
    assert quota.error_code == "RESOURCE_EXHAUSTED"
    assert quota.transient is True
    assert quota.permanent is False


def test_a_200_with_an_error_body_is_a_failure_not_a_success():
    result = fcm.classify(
        200,
        {
            "error": {
                "status": "INVALID_ARGUMENT",
                "message": "The registration token is not a valid FCM registration token",
                "details": [{"errorCode": "INVALID_ARGUMENT"}],
            }
        },
    )
    assert result.ok is False
    assert result.error_code == "INVALID_ARGUMENT"
    # Success with a lost token is worse than failure: it never invalidates the row.
    assert result.permanent is True


def test_our_own_credential_failures_are_transient_from_the_device_view():
    result = fcm.classify(403, {"error": {"status": "PERMISSION_DENIED", "message": "no"}})
    assert result.ok is False
    assert result.transient is True
    assert result.error_code == "PERMISSION_DENIED"


def test_unknown_error_shapes_are_permanent_with_a_readable_code():
    result = fcm.classify(418, {"raw": "teapot"})
    assert result.ok is False
    assert result.transient is False
    assert result.error_code == "teapot"


async def test_send_exchanges_one_token_then_reuses_it(account):
    recorder = Recorder([_token_response(), (200, {"name": "projects/voxdesk-test/messages/1"}), (200, {"name": "projects/voxdesk-test/messages/2"})])
    clock = iter([1_000.0, 1_010.0, 1_020.0])
    client = fcm.FcmClient(account, endpoint="https://fcm.test", transport=recorder, clock=lambda: next(clock))

    first = await client.send(fcm.PushMessage(token="t1", title="a", body="b"))
    second = await client.send(fcm.PushMessage(token="t2", title="a", body="b"))

    assert first.ok and first.message_id == "projects/voxdesk-test/messages/1"
    assert second.ok
    token_calls = [call for call in recorder.calls if call[1] == fcm.TOKEN_URI]
    assert len(token_calls) == 1, "the access token must be cached between sends"
    grant = token_calls[0][3]
    assert grant["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"

    method, url, headers, _payload = recorder.calls[1]
    assert method == "POST"
    assert url == "https://fcm.test/v1/projects/voxdesk-test/messages:send"
    assert headers["Authorization"] == "Bearer ya29.test"
    assert headers["Content-Type"] == "application/json"


async def test_the_access_token_is_refreshed_before_it_expires(account):
    recorder = Recorder([_token_response("first"), (200, {"name": "n1"}), _token_response("second"), (200, {"name": "n2"})])
    now = {"value": 1_000.0}
    client = fcm.FcmClient(account, endpoint="https://fcm.test", transport=recorder, clock=lambda: now["value"])

    await client.send(fcm.PushMessage(token="t", title="a", body="b"))
    # Past the 3600s lifetime minus the 120s skew: the cached token must not be reused.
    now["value"] = 1_000.0 + 3_600 - fcm.REFRESH_SKEW_SECONDS + 1
    await client.send(fcm.PushMessage(token="t", title="a", body="b"))

    token_calls = [call for call in recorder.calls if call[1] == fcm.TOKEN_URI]
    assert len(token_calls) == 2
    assert recorder.calls[3][2]["Authorization"] == "Bearer second"


async def test_a_failed_token_exchange_raises_provider_rejected(account):
    recorder = Recorder([(400, {"error": "invalid_grant"})])
    client = fcm.FcmClient(account, endpoint="https://fcm.test", transport=recorder, clock=lambda: 1_000.0)
    with pytest.raises(ProviderRejected) as caught:
        await client.send(fcm.PushMessage(token="t", title="a", body="b"))
    assert caught.value.detail["status_code"] == 400
    assert recorder.calls[0][1] == fcm.TOKEN_URI


async def test_a_failed_send_is_returned_and_logged_but_not_raised(account):
    recorder = Recorder([_token_response(), (503, {"error": {"status": "UNAVAILABLE", "message": "try later"}})])
    client = fcm.FcmClient(account, endpoint="https://fcm.test", transport=recorder, clock=lambda: 1_000.0)
    result = await client.send(fcm.PushMessage(token="t", title="a", body="b"))
    assert result.ok is False
    assert result.error_code == "UNAVAILABLE"
    assert result.transient is True
    assert result.as_dict()["transient"] is True


async def test_from_settings_refuses_when_the_provider_is_switched_off(monkeypatch):
    from app.platform_v2 import settings as settings_mod

    monkeypatch.setenv("FCM_ENABLED", "false")
    settings_mod.reset_plat2_settings_cache()
    try:
        with pytest.raises(ProviderRejected) as caught:
            fcm.FcmClient.from_settings()
        assert "FCM_ENABLED=false" in str(caught.value)
    finally:
        settings_mod.reset_plat2_settings_cache()


async def test_enabling_fcm_without_a_project_id_is_refused_at_settings_load(monkeypatch, account):
    """The deployment cannot even boot half-configured, so there is no runtime path."""
    from pydantic import ValidationError

    from app.platform_v2 import settings as settings_mod

    monkeypatch.setenv("FCM_ENABLED", "true")
    monkeypatch.setenv("FCM_PROJECT_ID", "")
    monkeypatch.setenv("FCM_SERVICE_ACCOUNT_JSON", "{}")
    settings_mod.reset_plat2_settings_cache()
    try:
        with pytest.raises(ValidationError) as caught:
            settings_mod.get_plat2_settings()
        assert "FCM_PROJECT_ID" in str(caught.value)
    finally:
        settings_mod.reset_plat2_settings_cache()
