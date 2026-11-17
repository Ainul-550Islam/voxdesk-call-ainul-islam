"""Request-a-call demo gates (PART 18, P18-03).

Each test here is one of the five promises the demo page makes. They are
negative tests by majority, on purpose: the feature works when it *refuses*
correctly, and a regression that opens a gate is silent otherwise.

No test touches the network. The captcha verifier is scripted and the dialler is
a recorder, which is also why the thresholds are assertable at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.demo_models import DemoCallRequest
from app.services import public_demo_service as demo

pytestmark = pytest.mark.usefixtures()

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
NUMBER = "+15550100111"
OTHER_NUMBER = "+15550100222"
IP = "203.0.113.7"
SALT = "test-salt-for-demo-hashing"


def _config(**overrides) -> demo.DemoConfig:
    base = dict(
        enabled=True,
        daily_calls=25,
        daily_seconds=600,
        max_call_seconds=60,
        default_call_seconds=25,
        per_ip_hourly=3,
        per_number_daily=2,
        captcha=demo.public_abuse_service.CaptchaConfig(
            provider="recaptcha_v3", enabled=True, threshold=0.5, site_key="site-key"
        ),
    )
    base.update(overrides)
    return demo.DemoConfig(**base)


async def _accepting_verifier(token, meta):  # noqa: ARG001
    return {"success": True, "score": 0.9}


async def _rejecting_verifier(token, meta):  # noqa: ARG001
    return {"success": True, "score": 0.1}


class Recorder:
    """A dialler that records instead of dialling."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, int]] = []
        self.fail = fail

    async def __call__(self, number: str, max_seconds: int) -> str:
        self.calls.append((number, max_seconds))
        if self.fail:
            raise RuntimeError("carrier said no")
        return "provider-call-1"


async def _verify_number(session: AsyncSession, number: str = NUMBER) -> None:
    sent: list[tuple[str, str]] = []

    async def send(to: str, code: str) -> None:
        sent.append((to, code))

    await demo.request_verification(session, number=number, send_code=send, moment=NOW)
    await demo.confirm_verification(
        session, number=number, code=sent[-1][1], moment=NOW
    )
    await session.commit()


async def _request(session: AsyncSession, **overrides):
    kwargs = dict(
        number=NUMBER,
        ip=IP,
        user_agent="pytest",
        consent=True,
        consent_version=demo.CONSENT_TEXT_VERSION,
        captcha_token="token",
        config=_config(),
        salt=SALT,
        verifier=_accepting_verifier,
        dialler=Recorder(),
        moment=NOW,
    )
    kwargs.update(overrides)
    return await demo.create_demo_call(session, **kwargs)


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


async def test_consent_is_required(db: AsyncSession):
    await _verify_number(db)
    with pytest.raises(demo.DemoError) as exc:
        await _request(db, consent=False)
    assert exc.value.code == "consent_required"
    assert exc.value.status == 422


async def test_stale_consent_wording_is_refused(db: AsyncSession):
    """A page left open across a wording change must not record the old text."""
    await _verify_number(db)
    with pytest.raises(demo.DemoError) as exc:
        await _request(db, consent_version="2020-01-01")
    assert exc.value.code == "consent_text_changed"
    assert exc.value.detail["current_version"] == demo.CONSENT_TEXT_VERSION


async def test_the_consent_text_is_stored_with_the_request(db: AsyncSession):
    await _verify_number(db)
    record = await _request(db)
    await db.commit()
    row = await db.get(DemoCallRequest, __import__("uuid").UUID(record["id"]))
    assert row is not None
    assert "I consent to VoxDesk placing one automated demonstration call" in row.consent_text
    assert row.consent_version == demo.CONSENT_TEXT_VERSION


# ---------------------------------------------------------------------------
# Captcha
# ---------------------------------------------------------------------------


async def test_missing_captcha_is_refused(db: AsyncSession):
    await _verify_number(db)
    with pytest.raises(demo.DemoError) as exc:
        await _request(db, captcha_token=None)
    assert exc.value.code == "captcha_missing"


async def test_low_captcha_score_is_refused(db: AsyncSession):
    await _verify_number(db)
    with pytest.raises(demo.DemoError) as exc:
        await _request(db, verifier=_rejecting_verifier)
    assert exc.value.code == "captcha_low_score"


async def test_a_captcha_provider_outage_does_not_open_the_gate(db: AsyncSession):
    await _verify_number(db)

    async def broken(token, meta):  # noqa: ARG001
        raise TimeoutError("provider unreachable")

    with pytest.raises(demo.DemoError) as exc:
        await _request(db, verifier=broken)
    assert exc.value.code == "captcha_error"


# ---------------------------------------------------------------------------
# Verified destination only
# ---------------------------------------------------------------------------


async def test_unverified_number_is_never_dialled(db: AsyncSession):
    recorder = Recorder()
    with pytest.raises(demo.DemoError) as exc:
        await _request(db, dialler=recorder)
    assert exc.value.code == "destination_not_verified"
    assert recorder.calls == []


async def test_verification_code_is_single_use(db: AsyncSession):
    sent: list[str] = []

    async def send(to: str, code: str) -> None:
        sent.append(code)

    await demo.request_verification(db, number=NUMBER, send_code=send, moment=NOW)
    code = sent[-1]
    await demo.confirm_verification(db, number=NUMBER, code=code, moment=NOW)

    # The same code again is refused — a leaked code cannot be replayed.
    with pytest.raises(demo.DemoError) as exc:
        await demo.confirm_verification(db, number=NUMBER, code=code, moment=NOW)
    assert exc.value.code == "verification_not_requested"

    # And the row records that the number is verified, which is what the gate reads.
    assert await demo.is_verified(db, NUMBER)


async def test_a_wrong_code_is_counted_and_limited(db: AsyncSession):
    async def send(to: str, code: str) -> None:
        return None

    await demo.request_verification(db, number=NUMBER, send_code=send, moment=NOW)
    for _ in range(5):
        with pytest.raises(demo.DemoError):
            await demo.confirm_verification(db, number=NUMBER, code="000000", moment=NOW)
    with pytest.raises(demo.DemoError) as exc:
        await demo.confirm_verification(db, number=NUMBER, code="000000", moment=NOW)
    assert exc.value.code == "verification_attempts_exhausted"


async def test_an_expired_code_is_refused(db: AsyncSession):
    sent: list[str] = []

    async def send(to: str, code: str) -> None:
        sent.append(code)

    await demo.request_verification(db, number=NUMBER, send_code=send, moment=NOW)
    later = NOW + demo.VERIFICATION_TTL + timedelta(seconds=1)
    with pytest.raises(demo.DemoError) as exc:
        await demo.confirm_verification(db, number=NUMBER, code=sent[-1], moment=later)
    assert exc.value.code == "verification_expired"


@pytest.mark.parametrize(
    "raw",
    ["5550100111", "+01", "++15550100111", "1-555-010-0111", "", "+abcdefgh"],
)
def test_non_e164_destinations_are_refused(raw: str):
    with pytest.raises(demo.DemoError) as exc:
        demo.normalise_destination(raw)
    assert exc.value.code == "destination_not_e164"


def test_e164_is_normalised_before_anything_else_sees_it():
    assert demo.normalise_destination(" +1 (555) 010-0111 ") == "+15550100111"


# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------


async def test_per_ip_rate_limit_stops_the_fourth_request(db: AsyncSession):
    await _verify_number(db)
    # The per-number limit is raised so this test measures the per-IP window and
    # not the (tighter, separately tested) number window.
    config = _config(per_number_daily=99)
    for _ in range(3):
        await _request(db, config=config, dialler=Recorder())
        await db.commit()
    with pytest.raises(demo.DemoError) as exc:
        await _request(db, config=config, dialler=Recorder())
    assert exc.value.code == "rate_limited_ip"
    assert exc.value.status == 429


async def test_per_number_rate_limit_binds_across_different_ips(db: AsyncSession):
    """The same number is capped even when the requests come from elsewhere."""
    await _verify_number(db)
    for ip in ("203.0.113.1", "203.0.113.2"):
        await _request(db, ip=ip, dialler=Recorder())
        await db.commit()
    with pytest.raises(demo.DemoError) as exc:
        await _request(db, ip="203.0.113.3", dialler=Recorder())
    assert exc.value.code == "rate_limited_number"


async def test_rows_carry_the_service_clock_not_the_wall_clock(db: AsyncSession):
    """A row must be visible to the window that counts it.

    The service takes `moment` so its windows are testable; if it stamped rows
    with a different clock the row could land outside its own window.
    """
    await _verify_number(db)
    await _request(db, dialler=Recorder())
    await db.commit()
    row = (await db.execute(select(DemoCallRequest))).scalars().one()
    assert row.created_at.replace(tzinfo=timezone.utc) == NOW


async def test_rate_limit_window_slides(db: AsyncSession):
    await _verify_number(db)
    config = _config(per_number_daily=99)
    for _ in range(3):
        await _request(db, config=config, dialler=Recorder())
        await db.commit()
    # Every one of those rows must age out of the hourly window — moving the
    # clock past the first three but not the last would still be rate limited,
    # correctly. Rows are written at `NOW`, so one hour plus a second clears all.
    later = NOW + demo.IP_WINDOW + timedelta(seconds=1)
    record = await _request(db, moment=later, config=config, dialler=Recorder())
    assert record["status"] == "dialled"


# ---------------------------------------------------------------------------
# Hard cost cap
# ---------------------------------------------------------------------------


async def test_daily_call_cap_is_a_ceiling_not_an_average(db: AsyncSession):
    await _verify_number(db)
    config = _config(daily_calls=2, per_ip_hourly=99, per_number_daily=99)
    for ip in ("203.0.113.1", "203.0.113.2"):
        await _request(db, ip=ip, config=config, dialler=Recorder())
        await db.commit()
    with pytest.raises(demo.DemoError) as exc:
        await _request(db, ip="203.0.113.3", config=config, dialler=Recorder())
    assert exc.value.code == "demo_daily_cap_reached"
    assert exc.value.detail["limit"] == 2


async def test_daily_seconds_cap_refuses_before_the_dial(db: AsyncSession):
    await _verify_number(db)
    recorder = Recorder()
    config = _config(daily_seconds=30, per_ip_hourly=99, per_number_daily=99)
    await _request(db, config=config, dialler=recorder)
    await db.commit()
    with pytest.raises(demo.DemoError) as exc:
        await _request(db, ip="203.0.113.9", config=config, dialler=recorder)
    assert exc.value.code == "demo_seconds_cap_reached"
    assert len(recorder.calls) == 1, "the second attempt must not reach the carrier"


async def test_a_disabled_demo_places_no_calls(db: AsyncSession):
    with pytest.raises(demo.DemoError) as exc:
        await _request(db, config=_config(enabled=False))
    assert exc.value.code == "demo_disabled"
    assert exc.value.status == 503


# ---------------------------------------------------------------------------
# Placing the call
# ---------------------------------------------------------------------------


async def test_a_verified_number_is_dialled_exactly_once(db: AsyncSession):
    await _verify_number(db)
    recorder = Recorder()
    record = await _request(db, dialler=recorder)
    assert recorder.calls == [(NUMBER, 25)]
    assert record["status"] == "dialled"
    assert record["destination"] == "+155******11"


async def test_a_failed_dial_is_recorded_and_reported(db: AsyncSession):
    await _verify_number(db)
    with pytest.raises(demo.DemoError) as exc:
        await _request(db, dialler=Recorder(fail=True))
    assert exc.value.code == "dial_failed"
    assert exc.value.status == 502
    rows = (await db.execute(
        __import__("sqlalchemy").select(DemoCallRequest)
    )).scalars().all()
    assert [r.status for r in rows] == ["dial_failed"]
    assert "carrier said no" in (rows[0].failure_reason or "")


async def test_demo_usage_reports_the_remaining_budget(db: AsyncSession):
    await _verify_number(db)
    await _request(db, dialler=Recorder())
    await db.commit()
    usage = await demo.demo_usage(db, config=_config(), moment=NOW)
    assert usage["calls_today"] == 1
    assert usage["calls_remaining"] == 24
    assert usage["max_call_seconds"] == 60
    assert usage["consent_version"] == demo.CONSENT_TEXT_VERSION
    assert "+15550100111" not in str(usage), "the public payload must never carry a number"


# ---------------------------------------------------------------------------
# The constants themselves
# ---------------------------------------------------------------------------


def test_masking_never_leaves_a_dialable_string():
    assert demo.mask_destination("+15550100111") == "+155******11"
    assert demo.mask_destination("+1") == "***"
    assert demo.mask_destination("+442071838750") == "+442*******50"
    masked = demo.mask_destination("+15550100111")
    assert "550100" not in masked, "the exchange must not survive masking"


def test_hashes_are_salted_so_a_dump_is_not_a_phone_book():
    first = demo.hash_destination(NUMBER, "salt-a")
    second = demo.hash_destination(NUMBER, "salt-b")
    assert first != second
    assert NUMBER not in first
