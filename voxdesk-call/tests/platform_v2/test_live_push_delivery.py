"""Opt-in verification that a push really reaches the provider.

Every test here needs live credentials and a live device token, and skips listing
exactly what is missing. When they do run they prove the thing a mock cannot:

* FCM accepts a message and returns a message id (and rejects a bogus token with a
  provider error code, which is what proves the request left the building),
* APNs does the same over its token-authenticated HTTP/2 path,
* ``push.send_to_user`` records the attempt against the device with the provider's
  answer, so the delivery ledger is written from reality and not from a fake.

Set ``VOXDESK_LIVE_FCM_TOKEN`` / ``VOXDESK_LIVE_APNS_TOKEN`` to a token registered to
a phone you control, plus the provider credentials the clients already read from
settings (``FCM_SERVICE_ACCOUNT_JSON``, ``APNS_KEY_ID``/``APNS_TEAM_ID``/
``APNS_PRIVATE_KEY``/``APNS_BUNDLE_ID``).
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.platform_v2.mobile import apns as apns_mod
from app.platform_v2.mobile import fcm as fcm_mod
from app.platform_v2.mobile import push as push_mod

pytestmark = [pytest.mark.live, pytest.mark.timeout(90)]

FCM_TOKEN_ENV = "VOXDESK_LIVE_FCM_TOKEN"
APNS_TOKEN_ENV = "VOXDESK_LIVE_APNS_TOKEN"


def _skip_unless(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"live push verification is opt-in; missing {name}")
    return value


@pytest.fixture(scope="module")
def fcm_client() -> fcm_mod.FcmClient:
    try:
        return fcm_mod.FcmClient.from_settings()
    except Exception as exc:  # noqa: BLE001 - not configured is the normal state
        pytest.skip(f"FCM is not configured in this environment: {exc}")


@pytest.fixture(scope="module")
def apns_client() -> apns_mod.ApnsClient:
    try:
        return apns_mod.ApnsClient.from_settings()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"APNs is not configured in this environment: {exc}")


async def test_fcm_accepts_a_real_message(fcm_client: fcm_mod.FcmClient):
    device_token = _skip_unless(FCM_TOKEN_ENV)
    result = await fcm_client.send(
        fcm_mod.PushMessage(
            token=device_token,
            title="VoxDesk live check",
            body="If you can read this, delivery is real.",
            data={"template": "live_probe"},
            collapse_key="live-probe",
            high_priority=False,
            tag="live-probe",
        )
    )
    assert result.ok is True, f"{result.error_code}: {result.error_reason}"
    assert result.message_id, "FCM accepted the message but returned no id"


async def test_fcm_rejects_a_token_it_does_not_know(fcm_client: fcm_mod.FcmClient):
    """A nonsense token must fail *at the provider*, which proves the call happened."""
    _skip_unless(FCM_TOKEN_ENV)
    result = await fcm_client.send(
        fcm_mod.PushMessage(
            token="definitely-not-a-real-fcm-token-" + uuid.uuid4().hex,
            title="VoxDesk live check",
            body="This must never be delivered.",
            data={},
            collapse_key="",
            high_priority=False,
            tag="",
        )
    )
    assert result.ok is False
    assert result.error_code, "a rejected send must carry the provider's error code"


async def test_apns_accepts_a_real_message(apns_client: apns_mod.ApnsClient):
    device_token = _skip_unless(APNS_TOKEN_ENV)
    result = await apns_client.send(
        apns_mod.ApnsMessage(
            device_token=device_token,
            title="VoxDesk live check",
            body="If you can read this, delivery is real.",
            data={"template": "live_probe"},
            collapse_id="live-probe",
            push_type=apns_mod.push_type_for("live_probe"),
            immediate=False,
            thread_id="live-probe",
        )
    )
    assert result.ok is True, f"{result.reason}: {result.detail}"
    assert result.apns_id, "APNs accepted the message but returned no apns-id"


async def test_apns_rejects_a_token_it_does_not_know(apns_client: apns_mod.ApnsClient):
    _skip_unless(APNS_TOKEN_ENV)
    result = await apns_client.send(
        apns_mod.ApnsMessage(
            device_token=uuid.uuid4().hex + uuid.uuid4().hex,
            title="VoxDesk live check",
            body="This must never be delivered.",
            data={},
            collapse_id="",
            push_type="alert",
            immediate=False,
            thread_id="",
        )
    )
    assert result.ok is False
    assert result.reason, "a rejected send must carry the provider's reason"


async def test_the_delivery_ledger_records_the_provider_answer(
    db, plat_scope, agent_headers, client
):
    """End to end: register a device with a live token, page it, read the ledger."""
    from sqlalchemy import select

    from app.db.platform_v2_models import PushDelivery

    device_token = os.environ.get(FCM_TOKEN_ENV, "").strip()
    if not device_token:
        pytest.skip(f"live push verification is opt-in; missing {FCM_TOKEN_ENV}")
    providers = push_mod.default_providers()
    if providers.fcm is None:
        pytest.skip("FCM is not configured in this environment")

    registered = await client.post(
        "/api/mobile/devices",
        headers=agent_headers,
        json={
            "platform": "android",
            "device_id": "live-probe-device",
            "app_version": "1.0.0",
            "os_version": "14",
        },
    )
    assert registered.status_code == 201, registered.text
    device_id = registered.json()["id"]
    attached = await client.post(
        f"/api/mobile/devices/{device_id}/token",
        headers=agent_headers,
        json={"token": device_token},
    )
    assert attached.status_code == 200, attached.text

    report = await push_mod.send_to_user(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=plat_scope.user.id,
        notification=push_mod.Notification(
            template="live_probe",
            title="VoxDesk live check",
            body="Delivery is real.",
            data={"template": "live_probe"},
            collapse_key="live-probe",
        ),
        providers=providers,
    )
    assert report, "nothing was attempted for a device with an active token"
    sent = [item for item in report if item.status == "sent"]
    assert sent, f"no delivery reached the provider: {[item.as_dict() for item in report]}"

    rows = (await db.execute(select(PushDelivery))).scalars().all()
    assert any(row.status == "sent" and row.provider_message_id for row in rows)
