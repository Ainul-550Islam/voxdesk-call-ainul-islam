"""The mobile API over HTTP: device registration, tokens, quiet hours, OAuth for the SDK.

The OAuth part is the PKCE flow the React Native SDK uses: an authorised code tied to a
device, exchanged with a verifier, rotated on refresh, and revocable per session family.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid

import pytest

from app.platform_v2.http import IDEMPOTENCY_HEADER
from tests.platform_v2.conftest import PlatScope

MOB = "/api/mobile"


def verifier_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)[:96]
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


async def register(client, headers, *, device_id: str = "device-abc-123", platform: str = "android"):
    return await client.post(
        f"{MOB}/devices",
        headers=headers,
        json={
            "platform": platform,
            "device_id": device_id,
            "app_version": "1.0.0",
            "os_version": "14",
        },
    )


# --- devices ------------------------------------------------------------------


async def test_registering_a_device_is_idempotent_per_device_id(
    client, plat_scope: PlatScope, agent_headers
):
    first = await register(client, agent_headers)
    assert first.status_code == 201, first.text
    assert first.json()["created"] is True

    second = await register(client, agent_headers)
    assert second.status_code == 201
    assert second.json()["created"] is False, "the same phone must not create a second row"
    assert second.json()["id"] == first.json()["id"]

    listed = await client.get(f"{MOB}/devices", headers=agent_headers)
    assert listed.status_code == 200
    assert listed.json()["count"] == 1


async def test_a_device_is_scoped_to_its_owner(client, plat_scope: PlatScope, agent_headers, agent_headers_b):
    registered = await register(client, agent_headers)
    device_id = registered.json()["id"]
    other = await client.get(f"{MOB}/devices", headers=agent_headers_b)
    assert other.json()["count"] == 0
    revoked = await client.delete(f"{MOB}/devices/{device_id}", headers=agent_headers_b)
    assert revoked.status_code == 404


async def test_an_unknown_platform_is_refused(client, plat_scope: PlatScope, agent_headers):
    response = await register(client, agent_headers, platform="windows")
    assert response.status_code == 422


async def test_unknown_body_fields_are_refused_rather_than_ignored(
    client, plat_scope: PlatScope, agent_headers
):
    response = await client.post(
        f"{MOB}/devices",
        headers=agent_headers,
        json={
            "platform": "android",
            "device_id": "device-xyz-999",
            "quiet_hours": {"start_minute": 1320, "end_minute": 420},
        },
    )
    assert response.status_code == 422
    assert "start_minute" in response.text


async def test_a_token_is_attached_and_reported_only_as_a_hint(
    client, plat_scope: PlatScope, agent_headers
):
    registered = await register(client, agent_headers)
    device_id = registered.json()["id"]
    secret_token = "fcm-token-abcdefghijklmnop"
    attached = await client.post(
        f"{MOB}/devices/{device_id}/token", headers=agent_headers, json={"token": secret_token}
    )
    assert attached.status_code == 200, attached.text
    body = attached.json()["token"]
    assert body["active"] is True
    # A push token is a credential: only its tail is ever shown.
    assert secret_token not in str(attached.json())
    assert body["hint"].endswith(secret_token[-4:])


async def test_a_short_token_is_refused(client, plat_scope: PlatScope, agent_headers):
    registered = await register(client, agent_headers)
    response = await client.post(
        f"{MOB}/devices/{registered.json()['id']}/token",
        headers=agent_headers,
        json={"token": "short"},
    )
    assert response.status_code == 422


async def test_quiet_hours_are_stored_in_the_documented_shape(
    client, plat_scope: PlatScope, agent_headers
):
    registered = await register(client, agent_headers)
    device_id = registered.json()["id"]
    updated = await client.post(
        f"{MOB}/devices/{device_id}/quiet-hours",
        headers=agent_headers,
        json={"quiet_hours": {"start": "22:00", "end": "07:00", "timezone": "Asia/Dhaka"}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["quiet_hours"] == {
        "start": "22:00",
        "end": "07:00",
        "timezone": "Asia/Dhaka",
        "enabled": True,
    }

    cleared = await client.post(
        f"{MOB}/devices/{device_id}/quiet-hours",
        headers=agent_headers,
        json={"quiet_hours": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["quiet_hours"] == {}


async def test_a_quiet_window_longer_than_the_limit_is_refused(
    client, plat_scope: PlatScope, agent_headers
):
    registered = await register(client, agent_headers)
    response = await client.post(
        f"{MOB}/devices/{registered.json()['id']}/quiet-hours",
        headers=agent_headers,
        json={"quiet_hours": {"start": "00:00", "end": "23:00", "timezone": "UTC"}},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["detail"]["span_hours"] == pytest.approx(23.0)


async def test_revoking_a_device_marks_it_and_stops_it_receiving(
    client, plat_scope: PlatScope, agent_headers
):
    registered = await register(client, agent_headers)
    device_id = registered.json()["id"]
    revoked = await client.delete(f"{MOB}/devices/{device_id}", headers=agent_headers)
    assert revoked.status_code == 204, revoked.text

    listed = await client.get(f"{MOB}/devices", headers=agent_headers)
    assert listed.json()["devices"][0]["revoked_at"]

    # A revoked device cannot take a new token.
    refused = await client.post(
        f"{MOB}/devices/{device_id}/token", headers=agent_headers, json={"token": "fcm-token-zzzzzzzz"}
    )
    assert refused.status_code == 409


# --- deliveries and providers --------------------------------------------------


async def test_deliveries_list_and_summarise(client, plat_scope: PlatScope, agent_headers):
    response = await client.get(f"{MOB}/deliveries", headers=agent_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["deliveries"] == []
    assert set(body["summary"]) == {"queued", "sent", "failed", "dropped"}


async def test_the_provider_status_says_what_is_configured(client, plat_scope: PlatScope, owner_headers):
    response = await client.get(f"{MOB}/providers", headers=owner_headers)
    assert response.status_code == 200
    body = response.json()
    assert set(body["fcm"]) >= {"enabled", "configured"}
    assert set(body["apns"]) >= {"enabled", "configured", "sandbox"}
    # Nothing is configured in tests, and the endpoint says so instead of pretending.
    assert body["fcm"]["enabled"] is False


async def test_a_test_push_reports_why_nothing_was_sent(client, plat_scope: PlatScope, agent_headers):
    await register(client, agent_headers)
    response = await client.post(f"{MOB}/push/test", headers=agent_headers, json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["note"]
    # Each attempt names why it was dropped rather than claiming a delivery.
    assert all(
        attempt.get("error_code") or attempt.get("error_reason") for attempt in body["attempts"]
    )


async def test_a_test_push_needs_a_registered_device(client, plat_scope: PlatScope, agent_headers_b):
    response = await client.post(f"{MOB}/push/test", headers=agent_headers_b, json={})
    assert response.status_code == 200
    assert response.json()["attempts"] == []


async def test_a_test_push_is_idempotent_under_one_key(client, plat_scope: PlatScope, agent_headers):
    await register(client, agent_headers)
    headers = {**agent_headers, IDEMPOTENCY_HEADER: "push-test-1"}
    first = await client.post(f"{MOB}/push/test", headers=headers, json={})
    assert first.status_code == 200
    second = await client.post(f"{MOB}/push/test", headers=headers, json={})
    assert second.status_code == 200
    assert second.json()["replayed"] is True


# --- OAuth (PKCE) --------------------------------------------------------------


async def test_a_mobile_authorization_code_can_be_exchanged_for_tokens(
    client, plat_scope: PlatScope, agent_headers
):
    registered = await register(client, agent_headers, device_id="oauth-device-1")
    device_id = registered.json()["id"]
    verifier, challenge = verifier_pair()

    authorized = await client.post(
        f"{MOB}/oauth/authorize",
        headers=agent_headers,
        json={
            "device_id": "oauth-device-1",
            "platform": "android",
            "code_challenge": challenge,
            "challenge_method": "S256",
        },
    )
    assert authorized.status_code == 201, authorized.text
    code = authorized.json()["code"]
    assert authorized.json()["expires_in"] > 0

    exchanged = await client.post(
        f"{MOB}/oauth/token",
        json={"code": code, "code_verifier": verifier, "device_id": "oauth-device-1"},
    )
    assert exchanged.status_code == 200, exchanged.text
    tokens = exchanged.json()
    assert tokens["access_token"] and tokens["refresh_token"]
    assert tokens["expires_in"] > 0

    # A code is single-use.
    replay = await client.post(
        f"{MOB}/oauth/token",
        json={"code": code, "code_verifier": verifier, "device_id": "oauth-device-1"},
    )
    assert replay.status_code == 409


async def test_the_wrong_verifier_is_refused(client, plat_scope: PlatScope, agent_headers):
    registered = await register(client, agent_headers, device_id="oauth-device-2")
    _verifier, challenge = verifier_pair()
    authorized = await client.post(
        f"{MOB}/oauth/authorize",
        headers=agent_headers,
        json={
            "device_id": "oauth-device-2",
            "platform": "ios",
            "code_challenge": challenge,
            "challenge_method": "S256",
        },
    )
    wrong = await client.post(
        f"{MOB}/oauth/token",
        json={
            "code": authorized.json()["code"],
            "code_verifier": secrets.token_urlsafe(48)[:96],
            "device_id": "oauth-device-2",
        },
    )
    # A bad verifier invalidates the grant, so the SDK restarts the flow; it is a
    # conflict on the code, not a malformed request.
    assert wrong.status_code == 409
    assert wrong.json()["detail"]["code"] == "conflict"


async def test_refresh_rotates_the_refresh_token(client, plat_scope: PlatScope, agent_headers):
    registered = await register(client, agent_headers, device_id="oauth-device-3")
    verifier, challenge = verifier_pair()
    authorized = await client.post(
        f"{MOB}/oauth/authorize",
        headers=agent_headers,
        json={
            "device_id": "oauth-device-3",
            "platform": "ios",
            "code_challenge": challenge,
            "challenge_method": "S256",
        },
    )
    tokens = (
        await client.post(
            f"{MOB}/oauth/token",
            json={
                "code": authorized.json()["code"],
                "code_verifier": verifier,
                "device_id": "oauth-device-3",
            },
        )
    ).json()

    refreshed = await client.post(
        f"{MOB}/oauth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]

    # The old refresh token is spent.
    stale = await client.post(
        f"{MOB}/oauth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert stale.status_code == 409


async def test_sessions_are_listed_and_revocable_per_family(
    client, plat_scope: PlatScope, agent_headers
):
    registered = await register(client, agent_headers, device_id="oauth-device-4")
    verifier, challenge = verifier_pair()
    authorized = await client.post(
        f"{MOB}/oauth/authorize",
        headers=agent_headers,
        json={
            "device_id": "oauth-device-4",
            "platform": "ios",
            "code_challenge": challenge,
            "challenge_method": "S256",
        },
    )
    tokens = (
        await client.post(
            f"{MOB}/oauth/token",
            json={
                "code": authorized.json()["code"],
                "code_verifier": verifier,
                "device_id": "oauth-device-4",
            },
        )
    ).json()

    sessions = await client.get(f"{MOB}/sessions", headers=agent_headers)
    assert sessions.status_code == 200
    assert sessions.json()["count"] >= 1
    family_id = sessions.json()["sessions"][0]["family_id"]

    revoked = await client.delete(f"{MOB}/sessions/{family_id}", headers=agent_headers)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked"] >= 1

    # Revoking the family invalidates the refresh token that belonged to it.
    stale = await client.post(
        f"{MOB}/oauth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert stale.status_code == 409


async def test_a_code_issued_to_one_device_cannot_be_used_by_another(
    client, plat_scope: PlatScope, agent_headers
):
    registered = await register(client, agent_headers, device_id="oauth-device-5")
    verifier, challenge = verifier_pair()
    authorized = await client.post(
        f"{MOB}/oauth/authorize",
        headers=agent_headers,
        json={
            "device_id": "oauth-device-5",
            "platform": "ios",
            "code_challenge": challenge,
            "challenge_method": "S256",
        },
    )
    response = await client.post(
        f"{MOB}/oauth/token",
        json={
            "code": authorized.json()["code"],
            "code_verifier": verifier,
            "device_id": "somebody-elses-device",
        },
    )
    assert response.status_code == 409
    assert "another device" in response.json()["detail"]["message"]


async def test_an_unknown_code_is_refused(client, plat_scope: PlatScope):
    response = await client.post(
        f"{MOB}/oauth/token",
        json={"code": "not-a-code", "code_verifier": "x" * 50, "device_id": "device-abc-123"},
    )
    assert response.status_code in (404, 409, 422)
