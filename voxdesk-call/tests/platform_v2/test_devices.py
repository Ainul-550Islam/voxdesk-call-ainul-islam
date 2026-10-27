"""Device registry, push-token rotation and quiet hours.

These run against the real tables: the tenancy assertions are only meaningful if a
row in tenant A is invisible to tenant B through the same query the router uses.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import UserRole
from app.db.platform_v2_models import MobileDevice, PushToken
from app.platform_v2.errors import NotFound, ValidationFailed
from app.platform_v2.mobile import devices as dev
from tests.conftest import make_user
from tests.platform_v2.conftest import PlatScope


async def _device(db, scope: PlatScope, user, platform: str = "android", device_id: str = "device-1"):
    row, created = await dev.register(
        db,
        tenant_id=scope.tenant.id,
        user_id=user.id,
        platform=platform,
        device_id=device_id,
        app_version="1.4.0",
        os_version="14",
        commit=True,
    )
    assert created is True
    return row


async def _token(db, scope: PlatScope, device, token: str = "fcm-token-abcdefghijklmnop"):
    return await dev.attach_token(db, tenant_id=scope.tenant.id, device=device, token=token, commit=True)


# --- validation ---------------------------------------------------------------


def test_platform_must_be_ios_or_android():
    assert dev.validate_platform(" IOS ") == "ios"
    with pytest.raises(ValidationFailed) as caught:
        dev.validate_platform("windows")
    assert caught.value.detail["platform"] == "windows"


def test_a_token_must_be_present_bounded_and_printable():
    with pytest.raises(ValidationFailed):
        dev.validate_token("   ", platform="android")
    with pytest.raises(ValidationFailed) as caught:
        dev.validate_token("a" * (dev.MAX_TOKEN_LENGTH + 1), platform="ios")
    assert str(dev.MAX_TOKEN_LENGTH) in str(caught.value)
    with pytest.raises(ValidationFailed):
        dev.validate_token("has space", platform="android")
    with pytest.raises(ValidationFailed):
        dev.validate_token("emoji-\N{SNOWMAN}", platform="android")
    with pytest.raises(ValidationFailed):
        dev.validate_token("tok", platform="nokia")


def test_the_hint_never_leaks_the_whole_token():
    token = "fcm-" + "x" * 140 + "ABCDEF"
    hint = dev.token_hint(token)
    assert hint == "…ABCDEF"
    assert dev.token_hint("short") == "…"


def test_quiet_hours_normalises_and_rejects_impossible_windows():
    window = dev.validate_quiet_hours({"start": "22:00", "end": "07:00", "timezone": "Asia/Dhaka"})
    assert window == {"start": "22:00", "end": "07:00", "timezone": "Asia/Dhaka", "enabled": True}
    assert dev.validate_quiet_hours(None) == {}
    assert dev.validate_quiet_hours({"enabled": False, "start": "22:00", "end": "07:00"}) == {}

    with pytest.raises(ValidationFailed):
        dev.validate_quiet_hours({"start": "22:00", "end": "22:00"})
    with pytest.raises(ValidationFailed) as caught:
        dev.validate_quiet_hours({"start": "25:00", "end": "07:00"})
    assert "start" in str(caught.value)
    with pytest.raises(ValidationFailed):
        dev.validate_quiet_hours({"start": "22:00", "end": "07:00", "timezone": "Mars/Olympus"})
    with pytest.raises(ValidationFailed) as caught:
        dev.validate_quiet_hours({"start": "08:00", "end": "21:00", "timezone": "UTC"})
    # 13 hours is wider than the 12h ceiling: a tenant cannot switch notifications off.
    assert caught.value.detail["max_span_hours"] == 12
    with pytest.raises(ValidationFailed):
        dev.validate_quiet_hours({"start": "09:00", "end": "17:00", "extra": 1})


def test_a_window_that_crosses_midnight_is_quiet_on_both_sides():
    # Dhaka is UTC+6, so these UTC instants are the local times named beside them.
    window = {"start": "22:00", "end": "07:00", "timezone": "Asia/Dhaka", "enabled": True}
    assert dev.in_quiet_hours(window, datetime(2026, 11, 3, 18, 30, tzinfo=UTC)) is True  # 00:30 local
    assert dev.in_quiet_hours(window, datetime(2026, 11, 3, 0, 30, tzinfo=UTC)) is True  # 06:30 local
    assert dev.in_quiet_hours(window, datetime(2026, 11, 3, 16, 30, tzinfo=UTC)) is True  # 22:30 local
    assert dev.in_quiet_hours(window, datetime(2026, 11, 3, 5, 0, tzinfo=UTC)) is False  # 11:00 local
    assert dev.in_quiet_hours(window, datetime(2026, 11, 3, 1, 0, tzinfo=UTC)) is False  # 07:00 local


def test_a_daytime_window_is_half_open():
    window = {"start": "09:00", "end": "12:00", "timezone": "UTC"}
    assert dev.in_quiet_hours(window, datetime(2026, 11, 3, 9, 0, tzinfo=UTC)) is True
    assert dev.in_quiet_hours(window, datetime(2026, 11, 3, 11, 59, tzinfo=UTC)) is True
    assert dev.in_quiet_hours(window, datetime(2026, 11, 3, 12, 0, tzinfo=UTC)) is False


def test_no_window_and_malformed_windows_never_block_notifications():
    assert dev.in_quiet_hours(None) is False
    assert dev.in_quiet_hours({}) is False
    # A row written by an older version must not silence a device forever.
    assert dev.in_quiet_hours({"start": "nonsense", "end": "07:00"}) is False


# --- registration -------------------------------------------------------------


async def test_registering_twice_updates_the_same_row(plat_scope: PlatScope, db, wfm_agent):
    first, created = await dev.register(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=wfm_agent.id,
        platform="ios",
        device_id="dev-9",
        app_version="1.0.0",
        commit=True,
    )
    assert created is True
    second, created_again = await dev.register(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=wfm_agent.id,
        platform="ios",
        device_id="dev-9",
        app_version="2.0.0",
        commit=True,
    )
    assert created_again is False
    assert second.id == first.id
    assert second.app_version == "2.0.0"

    rows = (await db.execute(select(MobileDevice))).scalars().all()
    assert len(rows) == 1


async def test_registration_refuses_a_blank_or_oversized_device_id(plat_scope: PlatScope, db, wfm_agent):
    with pytest.raises(ValidationFailed):
        await dev.register(db, tenant_id=plat_scope.tenant.id, user_id=wfm_agent.id, platform="ios", device_id="  ")
    with pytest.raises(ValidationFailed):
        await dev.register(
            db, tenant_id=plat_scope.tenant.id, user_id=wfm_agent.id, platform="ios", device_id="d" * 121
        )
    with pytest.raises(ValidationFailed):
        await dev.register(db, tenant_id=plat_scope.tenant.id, user_id=wfm_agent.id, platform="web", device_id="d")


async def test_re_registering_clears_a_previous_revocation(plat_scope: PlatScope, db, wfm_agent):
    row = await _device(db, plat_scope, wfm_agent)
    await dev.revoke(db, tenant_id=plat_scope.tenant.id, user_id=wfm_agent.id, device_pk=row.id, commit=True)
    assert row.revoked_at is not None

    _, created = await dev.register(
        db, tenant_id=plat_scope.tenant.id, user_id=wfm_agent.id, platform="android", device_id="device-1", commit=True
    )
    assert created is False
    assert row.revoked_at is None, "a reinstall must bring a revoked device back"


async def test_one_device_cannot_be_read_through_another_tenant(
    plat_scope: PlatScope, plat_scope_b: PlatScope, db, wfm_agent
):
    row = await _device(db, plat_scope, wfm_agent)
    with pytest.raises(NotFound):
        await dev.get(db, tenant_id=plat_scope_b.tenant.id, user_id=wfm_agent.id, device_pk=row.id)
    other = await make_user(db, plat_scope_b.tenant, UserRole.AGENT)
    with pytest.raises(NotFound):
        await dev.get(db, tenant_id=plat_scope_b.tenant.id, user_id=other.id, device_pk=row.id)
    assert await dev.list_for_user(db, tenant_id=plat_scope_b.tenant.id, user_id=wfm_agent.id) == []


# --- tokens -------------------------------------------------------------------


async def test_attaching_a_new_token_supersedes_the_old_one(plat_scope: PlatScope, db, wfm_agent):
    device = await _device(db, plat_scope, wfm_agent)
    old = await _token(db, plat_scope, device, "old-token-aaaaaaaaaaaa")
    new = await _token(db, plat_scope, device, "new-token-bbbbbbbbbbbb")

    active = await dev.active_tokens(db, tenant_id=plat_scope.tenant.id, device=device)
    assert [row.id for row in active] == [new.id]
    refreshed = await db.get(PushToken, old.id)
    assert refreshed.active is False
    assert refreshed.last_error == "superseded by a newer token"
    assert refreshed.invalidated_at is not None


async def test_the_same_token_on_a_new_phone_moves_to_the_new_device(plat_scope: PlatScope, db, wfm_agent):
    first = await _device(db, plat_scope, wfm_agent, device_id="phone-a")
    row = await _token(db, plat_scope, first, "shared-token-cccccccc")
    second = await _device(db, plat_scope, wfm_agent, device_id="phone-b")
    moved = await dev.attach_token(
        db, tenant_id=plat_scope.tenant.id, device=second, token="shared-token-cccccccc", commit=True
    )
    assert moved.device_id == second.id
    assert moved.id == row.id
    assert moved.active is True
    assert await dev.active_tokens(db, tenant_id=plat_scope.tenant.id, device=first) == []


async def test_detaching_returns_how_many_tokens_were_deactivated(plat_scope: PlatScope, db, wfm_agent):
    device = await _device(db, plat_scope, wfm_agent)
    await _token(db, plat_scope, device)
    assert await dev.detach_token(db, tenant_id=plat_scope.tenant.id, device=device, commit=True) == 1
    assert await dev.detach_token(db, tenant_id=plat_scope.tenant.id, device=device, commit=True) == 0


async def test_revoking_a_device_deactivates_its_tokens_immediately(plat_scope: PlatScope, db, wfm_agent):
    device = await _device(db, plat_scope, wfm_agent)
    token = await _token(db, plat_scope, device)
    await dev.revoke(db, tenant_id=plat_scope.tenant.id, user_id=wfm_agent.id, device_pk=device.id, commit=True)
    refreshed = await db.get(PushToken, token.id)
    assert refreshed.active is False
    assert refreshed.last_error == "device revoked"


async def test_a_permanent_failure_deactivates_only_after_the_threshold(plat_scope: PlatScope, db, wfm_agent):
    device = await _device(db, plat_scope, wfm_agent)
    token = await _token(db, plat_scope, device)

    deactivated = [
        await dev.record_failure(db, token=token, error="UNREGISTERED", permanent=True)
        for _ in range(dev.MAX_PERMANENT_FAILURES)
    ]
    assert deactivated == [False, False, True]
    assert token.active is False
    assert token.failure_count == dev.MAX_PERMANENT_FAILURES


async def test_a_transient_failure_never_deactivates_a_token(plat_scope: PlatScope, db, wfm_agent):
    device = await _device(db, plat_scope, wfm_agent)
    token = await _token(db, plat_scope, device)
    for _ in range(10):
        assert await dev.record_failure(db, token=token, error="UNAVAILABLE", permanent=False) is False
    assert token.active is True


async def test_a_success_clears_the_failure_count(plat_scope: PlatScope, db, wfm_agent):
    device = await _device(db, plat_scope, wfm_agent)
    token = await _token(db, plat_scope, device)
    await dev.record_failure(db, token=token, error="UNAVAILABLE", permanent=False)
    assert token.failure_count == 1
    await dev.record_success(db, token=token)
    assert token.failure_count == 0
    assert token.last_error == ""


async def test_purging_stale_tokens_spares_a_recently_seen_device(plat_scope: PlatScope, db, wfm_agent):
    device = await _device(db, plat_scope, wfm_agent)
    await _token(db, plat_scope, device)
    assert await dev.purge_stale(db, tenant_id=plat_scope.tenant.id, commit=True) == 0

    device.last_seen_at = datetime.now(tz=UTC) - timedelta(days=90)
    await db.commit()
    assert await dev.purge_stale(db, tenant_id=plat_scope.tenant.id, older_than_days=30, commit=True) == 1
    token = (await db.execute(select(PushToken))).scalars().one()
    assert token.active is False
    assert token.last_error == "device unseen for 30 days"
    # The device row survives: a support investigation still needs the history.
    assert (await db.execute(select(MobileDevice))).scalars().one().id == device.id


# --- serialisation ------------------------------------------------------------


async def test_the_serialised_device_never_contains_a_token_value(plat_scope: PlatScope, db, wfm_agent):
    device = await _device(db, plat_scope, wfm_agent)
    token = await _token(db, plat_scope, device, "super-secret-token-KLMNOP")
    payload = dev.describe(device, [token])
    assert payload["platform"] == "android"
    assert payload["tokens"][0]["hint"] == "…KLMNOP"
    assert "super-secret-token-KLMNOP" not in str(payload)
    assert uuid.UUID(payload["id"]) == device.id
