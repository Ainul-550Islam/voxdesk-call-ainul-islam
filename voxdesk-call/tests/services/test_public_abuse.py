"""PART 14 / P14-02 — captcha gates, budget caps and toll-fraud heuristics."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.retell_models import PublicWidgetKey
from app.services import public_abuse_service as abuse


def test_the_captcha_config_is_read_from_the_key_and_validated() -> None:
    config = abuse.CaptchaConfig.from_widget_config(
        {"captcha": {"provider": "turnstile", "threshold": 0.7, "site_key": "0x4AAA"}}
    )
    assert config.provider == "turnstile"
    assert config.threshold == 0.7
    with pytest.raises(abuse.AbuseError) as bad_provider:
        abuse.CaptchaConfig.from_widget_config({"captcha": {"provider": "wordle"}})
    assert bad_provider.value.code == "invalid_captcha_provider"
    with pytest.raises(abuse.AbuseError):
        abuse.CaptchaConfig.from_widget_config({"captcha": {"threshold": 2}})


@pytest.mark.asyncio
async def test_a_missing_token_is_a_refusal_not_a_pass() -> None:
    decision = await abuse.verify_captcha(None, abuse.CaptchaConfig(), verifier=None)
    assert decision.allowed is False
    assert decision.code == "captcha_missing"


@pytest.mark.asyncio
async def test_a_score_below_the_threshold_is_refused() -> None:
    async def verifier(_token, _meta):
        return {"success": True, "score": 0.2}

    decision = await abuse.verify_captcha(
        "tok", abuse.CaptchaConfig(threshold=0.5), verifier
    )
    assert decision.allowed is False
    assert decision.code == "captcha_low_score"
    assert decision.detail["score"] == 0.2


@pytest.mark.asyncio
async def test_a_provider_outage_refuses_rather_than_opens_the_gate() -> None:
    async def verifier(_token, _meta):
        raise RuntimeError("provider 502")

    decision = await abuse.verify_captcha("tok", abuse.CaptchaConfig(), verifier)
    assert decision.allowed is False
    assert decision.code == "captcha_error"


@pytest.mark.asyncio
async def test_an_unconfigured_verifier_is_a_deployment_fault() -> None:
    decision = await abuse.verify_captcha("tok", abuse.CaptchaConfig(), None)
    assert decision.code == "captcha_unconfigured"


@pytest.mark.asyncio
async def test_disabling_captcha_is_possible_and_explicit() -> None:
    decision = await abuse.verify_captcha(None, abuse.CaptchaConfig(enabled=False), None)
    assert decision.allowed is True
    assert decision.code == "captcha_disabled"


@pytest.fixture
async def public_key(db, tenant_a) -> PublicWidgetKey:
    row = PublicWidgetKey(
        tenant_id=tenant_a.id,
        agent_id="agent_1",
        name="Site widget",
        key_prefix="vdpk_abuse",
        key_hash="hash-" + str(tenant_a.id)[:8],
        allowed_origins=["https://example.test"],
        allowed_capabilities=["widget_session_create"],
        widget_config={"budget": {"daily_requests": 2, "daily_seconds": 60}},
    )
    db.add(row)
    await db.flush()
    return row


@pytest.mark.asyncio
async def test_the_request_budget_is_consumed_and_then_refuses(db, tenant_a, public_key) -> None:
    budget = abuse.BudgetConfig.from_widget_config(public_key.widget_config)
    assert budget.daily_requests == 2
    first = await abuse.check_budget(
        db, tenant_id=tenant_a.id, public_key_id=public_key.id, budget=budget
    )
    second = await abuse.check_budget(
        db, tenant_id=tenant_a.id, public_key_id=public_key.id, budget=budget
    )
    third = await abuse.check_budget(
        db, tenant_id=tenant_a.id, public_key_id=public_key.id, budget=budget
    )
    assert (first.allowed, second.allowed, third.allowed) == (True, True, False)
    assert third.code == "budget_requests_exhausted"
    assert third.detail["requests"] == 2


@pytest.mark.asyncio
async def test_the_seconds_budget_refuses_a_long_session(db, tenant_a, public_key) -> None:
    budget = abuse.BudgetConfig.from_widget_config(public_key.widget_config)
    await abuse.check_budget(
        db, tenant_id=tenant_a.id, public_key_id=public_key.id, budget=budget, seconds=50
    )
    decision = await abuse.check_budget(
        db, tenant_id=tenant_a.id, public_key_id=public_key.id, budget=budget, seconds=30
    )
    assert decision.allowed is False
    assert decision.code == "budget_seconds_exhausted"


@pytest.mark.asyncio
async def test_the_budget_resets_on_a_new_utc_day(db, tenant_a, public_key) -> None:
    budget = abuse.BudgetConfig(daily_requests=1, daily_seconds=60)
    today = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
    await abuse.check_budget(
        db, tenant_id=tenant_a.id, public_key_id=public_key.id, budget=budget, moment=today
    )
    same_day = await abuse.check_budget(
        db, tenant_id=tenant_a.id, public_key_id=public_key.id, budget=budget, moment=today
    )
    next_day = await abuse.check_budget(
        db,
        tenant_id=tenant_a.id,
        public_key_id=public_key.id,
        budget=budget,
        moment=today + timedelta(days=1),
    )
    assert same_day.allowed is False
    assert next_day.allowed is True


def test_rapid_calls_from_one_fingerprint_are_flagged() -> None:
    decision = abuse.toll_fraud_check(recent_calls=9, fingerprint_reuse=9)
    assert decision.allowed is False
    assert decision.code == "toll_fraud_rapid"


def test_a_majority_of_high_cost_destinations_is_flagged() -> None:
    decision = abuse.toll_fraud_check(
        recent_calls=1, destinations=["+234801", "+234802", "+234803", "+8801712"]
    )
    assert decision.allowed is False
    assert decision.code == "toll_fraud_destinations"
    quiet = abuse.toll_fraud_check(
        recent_calls=1, destinations=["+8801712", "+8801713", "+8801714", "+8801715"]
    )
    assert quiet.allowed is True


def test_fingerprints_are_stable_and_do_not_contain_the_ip() -> None:
    first = abuse.fingerprint("203.0.113.9", "Mozilla/5.0", "salt")
    second = abuse.fingerprint("203.0.113.9", "Mozilla/5.0", "salt")
    third = abuse.fingerprint("203.0.113.9", "Mozilla/5.0", "other-salt")
    assert first == second
    assert first != third
    assert "203.0.113.9" not in first


@pytest.mark.asyncio
async def test_evaluate_runs_fraud_then_captcha_then_budget(db, tenant_a, public_key) -> None:
    refused = await abuse.evaluate(
        db,
        tenant_id=tenant_a.id,
        public_key_id=public_key.id,
        widget_config={"captcha": {"enabled": False}, "budget": {"daily_requests": 5}},
        captcha_token=None,
        recent_calls=10,
        destinations=["+234801"] * 5,
    )
    # Only ten calls from a single fingerprint without reuse is *not* enough for
    # the rapid rule; the high-cost destination rule fires instead.
    assert refused.code == "toll_fraud_destinations"

    allowed = await abuse.evaluate(
        db,
        tenant_id=tenant_a.id,
        public_key_id=public_key.id,
        widget_config={"captcha": {"enabled": False}, "budget": {"daily_requests": 5}},
        captcha_token=None,
    )
    assert allowed.allowed is True
    usage = await abuse.usage(db, tenant_id=tenant_a.id, public_key_id=public_key.id)
    assert usage["requests"] == 1
