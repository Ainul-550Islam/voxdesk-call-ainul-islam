from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.contact_center_v2.dialer.pacing_engine import binomial_abandonment_rate
from app.contact_center_v2.errors import ContactCenterV2Error
from app.contact_center_v2.pacing import (
    AbandonmentMeasurement,
    calling_window_open,
    effective_cap,
    next_calling_window,
    pacing_decision,
    timezone_for_contact,
)


def measurement(answered: int = 100, abandoned: int = 0) -> AbandonmentMeasurement:
    return AbandonmentMeasurement(
        window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        human_answered=answered,
        abandoned=abandoned,
        cap=Decimal("0.03"),
        minimum_sample=50,
    )


def test_binomial_tail_includes_low_volume_answer_variance():
    # Ten attempts at 80% answer rate have mean eight answers, but the tail
    # above eight available agents is non-zero and must not be projected away.
    assert binomial_abandonment_rate(
        attempts=10, available_agents=8, answer_rate=Decimal("0.8")
    ) == Decimal("0.060398")
    assert binomial_abandonment_rate(
        attempts=8, available_agents=8, answer_rate=Decimal("0.8")
    ) == Decimal("0")


def test_predictive_base_capacity_does_not_need_abandonment_history():
    decision = pacing_decision(
        mode="predictive", available_agents=3, inflight=0, max_inflight=10,
        ratio=Decimal("1"), measurement=measurement(answered=0),
    )
    assert decision.allowed == 3
    assert decision.reason == "predictive_paced"


def test_predictive_excess_requires_measured_headroom():
    args = dict(
        mode="predictive", available_agents=2, inflight=0, max_inflight=10,
        ratio=Decimal("2"),
    )
    assert pacing_decision(**args, measurement=measurement(answered=0)).allowed == 2
    assert pacing_decision(**args, measurement=measurement(answered=100, abandoned=0)).allowed == 4
    capped = pacing_decision(**args, measurement=measurement(answered=100, abandoned=3))
    assert capped.allowed == 0
    assert capped.reason == "abandonment_cap"


def test_progressive_reserves_agents_and_preview_never_auto_dials():
    progressive = pacing_decision(
        mode="progressive", available_agents=2, inflight=1, max_inflight=10,
        ratio=Decimal("1"), measurement=measurement(),
    )
    assert (progressive.allowed, progressive.reserved_agents) == (2, 2)
    preview = pacing_decision(
        mode="preview", available_agents=10, inflight=0, max_inflight=10,
        ratio=Decimal("1"), measurement=measurement(),
    )
    assert preview.allowed == 0
    assert preview.reason == "agent_click_required"


def test_ai_blast_uses_ai_capacity_without_human_agent_availability():
    decision = pacing_decision(
        mode="ai_agent_blast", available_agents=0, inflight=1, max_inflight=5,
        ratio=Decimal("2"), measurement=measurement(answered=0),
    )
    assert decision.allowed == 2
    assert decision.reserved_agents == 0
    assert decision.reason == "ai_agent_capacity"
    capped = pacing_decision(
        mode="ai_agent_blast", available_agents=0, inflight=5, max_inflight=5,
        ratio=Decimal("2"), measurement=measurement(answered=0),
    )
    assert capped.allowed == 0 and capped.reason == "max_inflight"


def test_cap_above_three_percent_requires_matching_override():
    assert effective_cap(Decimal("0.02")) == Decimal("0.02")
    with pytest.raises(ContactCenterV2Error, match="override"):
        effective_cap(Decimal("0.031"))
    assert effective_cap(Decimal("0.04"), Decimal("0.04")) == Decimal("0.04")


def test_recipient_timezone_and_overnight_holiday_windows():
    now = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)  # Monday 09:00 in New York
    windows = [{"days": [0], "opens_at": "09:00", "closes_at": "17:00"}]
    assert calling_window_open(
        recipient_timezone="America/New_York", windows=windows, holiday_dates=[], now=now,
    )
    assert not calling_window_open(
        recipient_timezone="America/New_York", windows=windows,
        holiday_dates=["2026-01-05"], now=now,
    )
    retry = next_calling_window(
        recipient_timezone="America/New_York", windows=windows,
        holiday_dates=["2026-01-05"], now=now,
    )
    assert retry > now
    overnight = [{"days": [0], "opens_at": "22:00", "closes_at": "02:00"}]
    after_midnight = datetime(2026, 1, 6, 1, 30, tzinfo=timezone.utc)
    assert calling_window_open(
        recipient_timezone="UTC", windows=overnight, holiday_dates=[], now=after_midnight,
    )


def test_invalid_or_missing_timezone_fails_closed():
    for value in ("", "Mars/Olympus"):
        with pytest.raises(ContactCenterV2Error) as raised:
            timezone_for_contact(value)
        assert raised.value.code == "recipient_timezone_required"
