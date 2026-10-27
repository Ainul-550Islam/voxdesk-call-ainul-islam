"""Push fan-out: dedupe, quiet hours, provider absence, retries and honest reporting.

The providers are doubles that return the same ``SendResult``/``ApnsResult`` objects
the real clients return, so a change to the provider contract in ``fcm.py`` or
``apns.py`` breaks these tests instead of passing silently.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.platform_v2_models import PushDelivery
from app.platform_v2.mobile import devices as dev
from app.platform_v2.mobile import push
from app.platform_v2.mobile.apns import ApnsResult
from app.platform_v2.mobile.fcm import SendResult
from tests.platform_v2.conftest import PlatScope


class FakeFcm:
    platform = "android"

    def __init__(self, results=None):
        self.results = list(results or [SendResult(ok=True, message_id="fcm-1", status_code=200)])
        self.sent: list = []
        self.error: Exception | None = None

    async def send(self, message):
        self.sent.append(message)
        if self.error is not None:
            raise self.error
        return self.results.pop(0)


class FakeApns:
    platform = "ios"

    def __init__(self, results=None):
        self.results = list(results or [ApnsResult(ok=True, apns_id="apns-1", status_code=200)])
        self.sent: list = []
        self.error: Exception | None = None

    async def send(self, message):
        self.sent.append(message)
        if self.error is not None:
            raise self.error
        return self.results.pop(0)


def _note(**overrides) -> push.Notification:
    base = dict(template="rota_published", title="Rota published", body="Week of 3 Nov", collapse_key="rota-1")
    base.update(overrides)
    return push.Notification(**base)


async def _user_device(
    db,
    scope: PlatScope,
    *,
    platform: str = "android",
    device_id: str | None = None,
    token: str = "fcm-token-1234567890ab",
    **register_kwargs,
):
    from app.db.models import UserRole
    from tests.conftest import make_user

    user = await make_user(db, scope.tenant, UserRole.AGENT)
    device, _ = await dev.register(
        db,
        tenant_id=scope.tenant.id,
        user_id=user.id,
        platform=platform,
        device_id=device_id or f"dev-{uuid.uuid4().hex[:6]}",
        commit=True,
        **register_kwargs,
    )
    if token:
        await dev.attach_token(db, tenant_id=scope.tenant.id, device=device, token=token, commit=True)
    return user, device


# --- pure helpers -------------------------------------------------------------


def test_urgent_templates_ignore_quiet_hours():
    assert push.Notification(template="queue_breach", title="t", body="b").is_urgent is True
    assert push.Notification(template="open_shift_available", title="t", body="b").is_urgent is True
    assert push.Notification(template="rota_published", title="t", body="b").is_urgent is False
    # An explicit urgent flag works for templates that are not on the list.
    assert push.Notification(template="rota_published", title="t", body="b", urgent=True).is_urgent is True


def test_the_dedupe_key_collapses_within_a_bucket_and_changes_across_buckets():
    device_id = uuid.uuid4()
    base = dict(template="rota_published", device_id=device_id, collapse_key="rota-1")
    first = push.dedupe_key(**base, moment=datetime(2026, 11, 3, 9, 1, tzinfo=UTC))
    same_bucket = push.dedupe_key(**base, moment=datetime(2026, 11, 3, 9, 4, 59, tzinfo=UTC))
    next_bucket = push.dedupe_key(**base, moment=datetime(2026, 11, 3, 9, 5, tzinfo=UTC))
    assert first == same_bucket
    assert first != next_bucket
    assert len(first) == 60
    # Different devices never collide, or one person's page-out would silence another's.
    assert first != push.dedupe_key(**{**base, "device_id": uuid.uuid4()}, moment=datetime(2026, 11, 3, 9, 1, tzinfo=UTC))


def test_an_android_message_carries_string_data_and_the_collapse_key():
    message = push._message_for("android", "tok", _note(data={"shift_id": 7, "urgent": True}, urgent=True))
    payload = message.as_payload()["message"]
    assert payload["android"]["priority"] == "HIGH"
    assert payload["android"]["collapse_key"] == "rota-1"
    assert payload["data"] == {"shift_id": "7", "urgent": "True"}


def test_an_ios_message_uses_an_alert_and_a_collapse_id():
    message = push._message_for("ios", "tok", _note(urgent=True))
    assert message.push_type == "alert"
    assert message.immediate is True
    assert message.collapse_id == "rota-1"
    assert message.thread_id == "rota-1"
    assert message.payload()["aps"]["alert"]["title"] == "Rota published"


# --- delivery -----------------------------------------------------------------


async def test_a_sent_notification_is_recorded_with_the_provider_id(plat_scope: PlatScope, db):
    user, device = await _user_device(db, plat_scope)
    fake = FakeFcm()
    reports = await push.send_to_user(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=user.id,
        notification=_note(),
        providers=push.Providers(fcm=fake),
        commit=True,
    )
    assert len(reports) == 1
    assert reports[0].status == push.STATUS_SENT
    assert reports[0].provider_message_id == "fcm-1"
    assert len(fake.sent) == 1

    row = (await db.execute(select(PushDelivery))).scalars().one()
    assert row.status == push.STATUS_SENT
    assert row.provider == "fcm"
    assert row.sent_at is not None
    assert row.payload_summary["title"] == "Rota published"
    assert row.payload_summary["data_keys"] == []


async def test_the_same_notification_in_the_same_bucket_is_sent_once(plat_scope: PlatScope, db):
    user, _device = await _user_device(db, plat_scope)
    fake = FakeFcm()
    moment = datetime.now(tz=UTC)
    first = await push.send_to_user(
        db, tenant_id=plat_scope.tenant.id, user_id=user.id, notification=_note(),
        providers=push.Providers(fcm=fake), now=moment, commit=True,
    )
    second = await push.send_to_user(
        db, tenant_id=plat_scope.tenant.id, user_id=user.id, notification=_note(),
        providers=push.Providers(fcm=fake), now=moment, commit=True,
    )
    assert first[0].status == push.STATUS_SENT
    # The second call reports the existing row instead of paging the person twice.
    assert second[0].status == push.STATUS_SENT
    assert second[0].dedupe_key == first[0].dedupe_key
    assert len(fake.sent) == 1
    assert len((await db.execute(select(PushDelivery))).scalars().all()) == 1


async def test_quiet_hours_drop_a_routine_notification_and_keep_an_urgent_one(plat_scope: PlatScope, db):
    user, _device = await _user_device(
        db,
        plat_scope,
        quiet_hours={"start": "22:00", "end": "07:00", "timezone": "Asia/Dhaka"},
    )
    night = datetime(2026, 11, 3, 18, 0, tzinfo=UTC)  # 00:00 in Dhaka
    fake = FakeFcm()

    routine = await push.send_to_user(
        db, tenant_id=plat_scope.tenant.id, user_id=user.id, notification=_note(template="time_off_decided"),
        providers=push.Providers(fcm=fake), now=night, commit=True,
    )
    assert routine[0].status == push.STATUS_DROPPED
    assert routine[0].error_code == "quiet_hours"
    assert fake.sent == []

    urgent = await push.send_to_user(
        db, tenant_id=plat_scope.tenant.id, user_id=user.id, notification=_note(template="queue_breach"),
        providers=push.Providers(fcm=fake), now=night + timedelta(minutes=10), commit=True,
    )
    assert urgent[0].status == push.STATUS_SENT
    assert len(fake.sent) == 1


async def test_a_revoked_device_and_a_disabled_device_and_a_missing_token_are_all_dropped(
    plat_scope: PlatScope, db
):
    revoked_user, revoked_device = await _user_device(db, plat_scope)
    await dev.revoke(
        db, tenant_id=plat_scope.tenant.id, user_id=revoked_user.id, device_pk=revoked_device.id, commit=True
    )
    reports = await push.send_to_user(
        db, tenant_id=plat_scope.tenant.id, user_id=revoked_user.id, notification=_note(),
        providers=push.Providers(fcm=FakeFcm()), commit=True,
    )
    assert reports[0].error_code == "device_revoked"

    off_user, _ = await _user_device(db, plat_scope, push_enabled=False)
    reports = await push.send_to_user(
        db, tenant_id=plat_scope.tenant.id, user_id=off_user.id, notification=_note(),
        providers=push.Providers(fcm=FakeFcm()), commit=True,
    )
    assert reports[0].error_code == "push_disabled"

    bare_user, _ = await _user_device(db, plat_scope, token="")
    reports = await push.send_to_user(
        db, tenant_id=plat_scope.tenant.id, user_id=bare_user.id, notification=_note(),
        providers=push.Providers(fcm=FakeFcm()), commit=True,
    )
    assert reports[0].error_code == "no_active_token"

    # A drop that never reached a provider is not a delivery attempt.
    assert (await db.execute(select(PushDelivery))).scalars().all() == []


async def test_a_deployment_without_credentials_records_dropped_not_sent(plat_scope: PlatScope, db):
    user, _device = await _user_device(db, plat_scope)
    reports = await push.send_to_user(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=user.id,
        notification=_note(),
        providers=push.Providers(),
        commit=True,
    )
    assert reports[0].status == push.STATUS_DROPPED
    assert reports[0].error_code == "provider_not_configured"
    row = (await db.execute(select(PushDelivery))).scalars().one()
    assert row.status == push.STATUS_DROPPED
    assert row.sent_at is None


async def test_a_transient_failure_keeps_the_token_alive_and_a_permanent_one_counts(plat_scope: PlatScope, db):
    user, device = await _user_device(db, plat_scope)
    fake = FakeFcm([SendResult(ok=False, error_code="UNAVAILABLE", error_reason="try later", transient=True, status_code=503)])
    reports = await push.send_to_user(
        db, tenant_id=plat_scope.tenant.id, user_id=user.id, notification=_note(),
        providers=push.Providers(fcm=fake), commit=True,
    )
    assert reports[0].status == push.STATUS_FAILED
    assert reports[0].error_code == "UNAVAILABLE"
    token = (await db.execute(select(dev.PushToken))).scalars().one()
    assert token.failure_count == 1
    assert token.active is True

    user2, _device2 = await _user_device(db, plat_scope, token="second-token-abcdef")
    permanent = FakeFcm(
        [SendResult(ok=False, error_code="UNREGISTERED", error_reason="not registered", transient=False, status_code=404)]
    )
    await push.send_to_user(
        db, tenant_id=plat_scope.tenant.id, user_id=user2.id, notification=_note(),
        providers=push.Providers(fcm=permanent), commit=True,
    )
    fresh = (
        await db.execute(select(dev.PushToken).where(dev.PushToken.token == "second-token-abcdef"))
    ).scalars().one()
    assert fresh.failure_count == 1


async def test_a_provider_exception_is_recorded_as_a_failed_attempt(plat_scope: PlatScope, db):
    user, _device = await _user_device(db, plat_scope)
    fake = FakeFcm()
    fake.error = TimeoutError("connect timed out")
    reports = await push.send_to_user(
        db, tenant_id=plat_scope.tenant.id, user_id=user.id, notification=_note(),
        providers=push.Providers(fcm=fake), commit=True,
    )
    assert reports[0].status == push.STATUS_FAILED
    assert reports[0].error_code == "provider_exception"
    assert "TimeoutError" in reports[0].error_reason


async def test_an_ios_device_is_sent_through_apns(plat_scope: PlatScope, db):
    user, _device = await _user_device(db, plat_scope, platform="ios", token="0123456789abcdef0123")
    fake = FakeApns()
    reports = await push.send_to_user(
        db, tenant_id=plat_scope.tenant.id, user_id=user.id, notification=_note(),
        providers=push.Providers(apns=fake), commit=True,
    )
    assert reports[0].status == push.STATUS_SENT
    assert reports[0].provider_message_id == "apns-1"
    row = (await db.execute(select(PushDelivery))).scalars().one()
    assert row.provider == "apns"


async def test_one_users_devices_are_never_notified_for_another_tenant(plat_scope: PlatScope, plat_scope_b: PlatScope, db):
    user, _device = await _user_device(db, plat_scope)
    fake = FakeFcm()
    reports = await push.send_to_user(
        db, tenant_id=plat_scope_b.tenant.id, user_id=user.id, notification=_note(),
        providers=push.Providers(fcm=fake), commit=True,
    )
    assert reports == []
    assert fake.sent == []


# --- retries ------------------------------------------------------------------


async def test_retrying_resends_a_failure_and_records_success(plat_scope: PlatScope, db):
    user, _device = await _user_device(db, plat_scope)
    failing = FakeFcm(
        [SendResult(ok=False, error_code="UNAVAILABLE", error_reason="later", transient=True, status_code=503)]
    )
    await push.send_to_user(
        db, tenant_id=plat_scope.tenant.id, user_id=user.id, notification=_note(data={"shift_id": "9"}),
        providers=push.Providers(fcm=failing), commit=True,
    )
    working = FakeFcm([SendResult(ok=True, message_id="retry-1", status_code=200)])
    summary = await push.retry_failed(db, tenant_id=plat_scope.tenant.id, providers=push.Providers(fcm=working), commit=True)
    assert summary == {"attempted": 1, "sent": 1, "still_failing": 0}
    row = (await db.execute(select(PushDelivery))).scalars().one()
    assert row.status == push.STATUS_SENT
    assert row.error_code == ""
    # The retry reconstructed the message from what was stored, not from memory.
    retried = working.sent[0]
    assert retried.data == {"shift_id": "9"}
    assert retried.collapse_key == "rota-1"


async def test_a_failure_outside_the_retry_window_is_left_alone(plat_scope: PlatScope, db):
    user, device = await _user_device(db, plat_scope)
    old = datetime.now(tz=UTC) - push.RETRY_WINDOW - timedelta(hours=1)
    db.add(
        PushDelivery(
            id=uuid.uuid4(),
            tenant_id=plat_scope.tenant.id,
            device_id=device.id,
            template="rota_published",
            provider="fcm",
            status=push.STATUS_FAILED,
            dedupe_key="stale-key",
            payload_summary={"title": "t", "body": "b"},
            created_at=old,
        )
    )
    await db.commit()
    fake = FakeFcm()
    summary = await push.retry_failed(db, tenant_id=plat_scope.tenant.id, providers=push.Providers(fcm=fake), commit=True)
    assert summary["attempted"] == 0
    assert fake.sent == []


async def test_a_retry_for_a_revoked_device_becomes_dropped(plat_scope: PlatScope, db):
    user, device = await _user_device(db, plat_scope)
    await push.send_to_user(
        db, tenant_id=plat_scope.tenant.id, user_id=user.id, notification=_note(),
        providers=push.Providers(fcm=FakeFcm(
            [SendResult(ok=False, error_code="UNAVAILABLE", transient=True, status_code=503)]
        )),
        commit=True,
    )
    await dev.revoke(db, tenant_id=plat_scope.tenant.id, user_id=user.id, device_pk=device.id, commit=True)
    summary = await push.retry_failed(db, tenant_id=plat_scope.tenant.id, providers=push.Providers(fcm=FakeFcm()), commit=True)
    assert summary["attempted"] == 0
    row = (await db.execute(select(PushDelivery))).scalars().one()
    assert row.status == push.STATUS_DROPPED
    assert row.error_code == "device_revoked"


async def test_sent_rows_are_never_retried(plat_scope: PlatScope, db):
    user, _device = await _user_device(db, plat_scope)
    await push.send_to_user(
        db, tenant_id=plat_scope.tenant.id, user_id=user.id, notification=_note(),
        providers=push.Providers(fcm=FakeFcm()), commit=True,
    )
    summary = await push.retry_failed(db, tenant_id=plat_scope.tenant.id, providers=push.Providers(fcm=FakeFcm()), commit=True)
    assert summary == {"attempted": 0, "sent": 0, "still_failing": 0}


# --- reporting ----------------------------------------------------------------


async def test_deliveries_for_a_user_are_scoped_and_summarised(plat_scope: PlatScope, db):
    user, _device = await _user_device(db, plat_scope)
    await push.send_to_user(
        db, tenant_id=plat_scope.tenant.id, user_id=user.id, notification=_note(),
        providers=push.Providers(fcm=FakeFcm()), commit=True,
    )
    rows = await push.deliveries_for(db, tenant_id=plat_scope.tenant.id, user_id=user.id)
    assert len(rows) == 1
    described = push.describe_delivery(rows[0])
    assert described["status"] == push.STATUS_SENT
    assert described["provider"] == "fcm"
    assert set(described) == {
        "id", "device_id", "template", "provider", "status", "provider_message_id",
        "error_code", "error_reason", "collapse_id", "created_at", "sent_at",
    }
    assert push.summarise_deliveries(rows) == {
        push.STATUS_QUEUED: 0, push.STATUS_SENT: 1, push.STATUS_FAILED: 0, push.STATUS_DROPPED: 0,
    }
    assert await push.deliveries_for(db, tenant_id=plat_scope.tenant.id, user_id=uuid.uuid4()) == []


def test_an_unconfigured_deployment_yields_no_providers_rather_than_an_exception(monkeypatch):
    from app.platform_v2 import settings as settings_mod

    monkeypatch.setenv("FCM_ENABLED", "false")
    monkeypatch.setenv("APNS_ENABLED", "false")
    settings_mod.reset_plat2_settings_cache()
    try:
        providers = push.default_providers()
        assert providers.fcm is None
        assert providers.apns is None
        assert providers.for_platform("android") is None
        assert providers.for_platform("ios") is None
    finally:
        settings_mod.reset_plat2_settings_cache()


async def test_a_duplicate_claim_does_not_discard_the_other_devices_delivery(plat_scope: PlatScope, db):
    """The savepoint matters: a plain rollback would lose the staged rows."""
    from app.db.models import UserRole
    from tests.conftest import make_user

    user = await make_user(db, plat_scope.tenant, UserRole.AGENT)
    first, _ = await dev.register(
        db, tenant_id=plat_scope.tenant.id, user_id=user.id, platform="android", device_id="a", commit=True
    )
    second, _ = await dev.register(
        db, tenant_id=plat_scope.tenant.id, user_id=user.id, platform="android", device_id="b", commit=True
    )
    await dev.attach_token(db, tenant_id=plat_scope.tenant.id, device=first, token="token-aaaaaaaaaaaa", commit=True)
    await dev.attach_token(db, tenant_id=plat_scope.tenant.id, device=second, token="token-bbbbbbbbbbbb", commit=True)

    moment = datetime(2026, 11, 3, 9, 1, tzinfo=UTC)
    # Somebody else already claimed the second device's slot for this bucket.
    pre = PushDelivery(
        id=uuid.uuid4(),
        tenant_id=plat_scope.tenant.id,
        device_id=second.id,
        template="rota_published",
        provider="fcm",
        status=push.STATUS_SENT,
        dedupe_key=push.dedupe_key(
            template="rota_published", device_id=second.id, collapse_key="rota-1", moment=moment
        ),
        collapse_id="rota-1",
        payload_summary={"title": "t", "body": "b", "attempts": 0},
    )
    db.add(pre)
    await db.commit()

    fake = FakeFcm()
    reports = await push.send_to_user(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=user.id,
        notification=_note(),
        providers=push.Providers(fcm=fake),
        now=moment,
        commit=True,
    )
    by_device = {report.device_id: report for report in reports}
    assert by_device[second.id].status == push.STATUS_SENT, "the existing row is reported, not resent"
    assert by_device[first.id].status == push.STATUS_SENT, "the duplicate must not roll back the first delivery"
    assert len(fake.sent) == 1
    rows = (await db.execute(select(PushDelivery))).scalars().all()
    assert len(rows) == 2


async def test_a_delivery_is_not_retried_more_than_the_attempt_cap(plat_scope: PlatScope, db):
    user, device = await _user_device(db, plat_scope)
    db.add(
        PushDelivery(
            id=uuid.uuid4(),
            tenant_id=plat_scope.tenant.id,
            device_id=device.id,
            template="rota_published",
            provider="fcm",
            status=push.STATUS_FAILED,
            dedupe_key="exhausted-key",
            collapse_id="rota-1",
            payload_summary={"title": "t", "body": "b", "attempts": push.MAX_ATTEMPTS},
        )
    )
    await db.commit()
    fake = FakeFcm()
    summary = await push.retry_failed(
        db, tenant_id=plat_scope.tenant.id, providers=push.Providers(fcm=fake), commit=True
    )
    assert summary["attempted"] == 0
    assert fake.sent == []
