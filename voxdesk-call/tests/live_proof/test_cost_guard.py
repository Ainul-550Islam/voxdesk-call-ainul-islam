"""The spend guard: caps are hard, and a crossed cap is an abort, not a warning."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.telephony.live_proof.cost_guard import CostGuard, CostGuardExceeded


class FakeClock:
    """A monotonic clock the test advances. Not a stand-in for the guard."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _guard(clock: FakeClock, *, minutes: float = 3.0, usd: float = 0.50, rate: float = 0.05) -> CostGuard:
    return CostGuard(max_minutes=minutes, max_usd=usd, usd_per_minute=rate, clock=clock)


def test_elapsed_minutes_and_spend_follow_the_clock():
    clock = FakeClock()
    guard = _guard(clock)
    guard.start()

    clock.advance(90)
    report = guard.report()

    assert report.minutes == pytest.approx(1.5)
    assert report.usd == pytest.approx(0.075)
    assert report.within_caps


def test_stop_freezes_elapsed_time():
    clock = FakeClock()
    guard = _guard(clock)
    guard.start()
    clock.advance(60)
    stopped = guard.stop()
    clock.advance(600)

    assert guard.report().minutes == pytest.approx(stopped.minutes)
    assert guard.elapsed_minutes() == pytest.approx(1.0)


def test_crossing_the_minutes_cap_raises_with_the_cap_name():
    clock = FakeClock()
    guard = _guard(clock, minutes=1.0)
    guard.start()
    clock.advance(61)

    with pytest.raises(CostGuardExceeded) as error:
        guard.tick()

    assert error.value.cap == "minutes"
    assert error.value.code == "live_proof_cost_cap"
    assert error.value.as_dict()["minutes"] == pytest.approx(61 / 60.0, abs=1e-3)


def test_crossing_the_spend_cap_raises_even_when_time_is_left():
    clock = FakeClock()
    guard = _guard(clock, minutes=60.0, usd=0.10, rate=0.05)
    guard.start()
    clock.advance(180)  # 3 minutes * $0.05 = $0.15 > $0.10

    with pytest.raises(CostGuardExceeded) as error:
        guard.tick()

    assert error.value.cap == "usd"
    assert error.value.usd == pytest.approx(0.15)


def test_exactly_at_the_cap_is_allowed_and_one_tick_over_is_not():
    clock = FakeClock()
    guard = _guard(clock, minutes=2.0, usd=10.0, rate=0.0)
    guard.start()
    clock.advance(120)

    assert guard.ensure_within_cap().minutes == pytest.approx(2.0)
    clock.advance(0.001)
    with pytest.raises(CostGuardExceeded):
        guard.tick()


def test_remaining_budget_is_reported_for_the_operator():
    clock = FakeClock()
    guard = _guard(clock, minutes=4.0, usd=0.40, rate=0.10)
    guard.start()
    clock.advance(60)
    report = guard.report()

    assert report.remaining_minutes == pytest.approx(3.0)
    assert report.remaining_usd == pytest.approx(0.30)
    assert report.as_dict()["within_caps"] is True


@pytest.mark.parametrize(
    ("minutes", "usd", "rate"),
    [(0.0, 1.0, 0.1), (-1.0, 1.0, 0.1), (1.0, 0.0, 0.1), (1.0, -5.0, 0.1), (1.0, 1.0, -0.1)],
)
def test_impossible_caps_are_a_configuration_error_not_a_zero(minutes: float, usd: float, rate: float):
    with pytest.raises(ValueError):
        CostGuard(max_minutes=minutes, max_usd=usd, usd_per_minute=rate)


def test_from_settings_uses_the_configured_ceiling(monkeypatch):
    monkeypatch.setattr(settings, "live_proof_max_minutes", 2.5)
    monkeypatch.setattr(settings, "live_proof_max_usd", 0.25)
    monkeypatch.setattr(settings, "live_proof_usd_per_minute", 0.07)

    guard = CostGuard.from_settings(clock=FakeClock())

    assert guard.max_minutes == pytest.approx(2.5)
    assert guard.max_usd == pytest.approx(0.25)
    assert guard.usd_per_minute == pytest.approx(0.07)


def test_an_unstarted_guard_reports_zero_and_does_not_raise():
    guard = CostGuard(max_minutes=1.0, max_usd=1.0, usd_per_minute=1.0)

    assert guard.started is False
    assert guard.elapsed_minutes() == 0.0
    assert guard.ensure_within_cap().within_caps
