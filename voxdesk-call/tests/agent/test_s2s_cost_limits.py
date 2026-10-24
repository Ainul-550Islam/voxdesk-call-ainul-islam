"""The speech-to-speech ceilings: the session length, the per-session and
per-tenant budgets, and the idempotent usage ledger.

The token usage is written into the **existing** usage ledger with a
deterministic idempotency key, so a session that is finalised twice bills
once. The tests use the real ledger, not a double.
"""

from __future__ import annotations

import uuid

import pytest

from app.agent.s2s.s2s_cost import (
    S2SCostMeter,
    build_cost_meter,
    persist_token_usage,
    tenant_spend_micro_usd,
)
from app.agent.s2s.s2s_session import S2SBudgetExceeded
from app.db.carrier_s2s_models import S2SSessionRecord
from tests.conftest import make_tenant


# ---------------------------------------------------------------------------
# The meter
# ---------------------------------------------------------------------------
def test_the_meter_counts_audio_and_tokens() -> None:
    meter = S2SCostMeter(provider="openai_realtime", model="gpt-4o-realtime-preview")
    meter.record_audio_bytes(raw=b"\x00" * 640, output=False, sample_rate=16000)
    meter.record_tokens(input_tokens=100, output_tokens=50)
    usage = meter.snapshot()
    assert usage.input_audio_seconds == pytest.approx(0.02)
    assert usage.input_tokens == 100
    assert usage.output_tokens == 50
    # No price is configured, so the cost stays unknown rather than invented.
    assert usage.cost_known is False
    assert usage.cost_micro_usd == 0


def test_the_meter_computes_a_cost_when_a_price_is_configured() -> None:
    meter = S2SCostMeter(
        input_micro_usd_per_million=1_000_000,
        output_micro_usd_per_million=2_000_000,
    )
    meter.record_tokens(input_tokens=1_000_000, output_tokens=1_000_000)
    usage = meter.snapshot()
    assert usage.cost_known is True
    assert usage.cost_micro_usd == 3_000_000


def test_the_meter_counts_the_output_audio_separately() -> None:
    meter = S2SCostMeter()
    meter.record_audio_bytes(raw=b"\x00" * 640, output=True, sample_rate=16000)
    usage = meter.snapshot()
    assert usage.output_audio_seconds == pytest.approx(0.02)
    assert usage.input_audio_seconds == pytest.approx(0.0)


def test_the_session_ceiling_is_enforced() -> None:
    meter = S2SCostMeter(max_session_seconds=1)
    meter.started_at -= 10
    assert meter.session_limit_exceeded() is True
    with pytest.raises(S2SBudgetExceeded):
        meter.check_limits()


def test_the_budget_ceiling_is_enforced() -> None:
    meter = S2SCostMeter(session_budget_micro_usd=1, input_micro_usd_per_million=1_000_000)
    meter.record_tokens(input_tokens=1_000_000)
    assert meter.budget_exceeded() is True
    with pytest.raises(S2SBudgetExceeded):
        meter.check_limits()


def test_a_meter_without_a_ceiling_never_fails() -> None:
    meter = S2SCostMeter(max_session_seconds=0, session_budget_micro_usd=0)
    meter.started_at -= 10_000
    meter.check_limits()
    assert meter.session_limit_exceeded() is False
    assert meter.budget_exceeded() is False


def test_the_meter_is_built_from_the_settings() -> None:
    meter = build_cost_meter(
        provider="gemini_live",
        model="gemini-2.0-flash-exp",
        max_session_seconds=42,
        session_budget_micro_usd=1000,
        tenant_budget_micro_usd=2000,
    )
    assert meter.provider == "gemini_live"
    assert meter.max_session_seconds == 42
    assert meter.session_budget_micro_usd == 1000
    assert meter.tenant_budget_micro_usd == 2000


# ---------------------------------------------------------------------------
# The usage ledger
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_token_usage_is_written_once_per_call(db) -> None:
    tenant = await make_tenant(db, name="S2S Ledger Tenant")
    call_id = uuid.uuid4()

    created = await persist_token_usage(
        db,
        tenant,
        call_id=call_id,
        input_tokens=120,
        output_tokens=45,
        provider="openai_realtime",
    )
    await db.commit()
    assert created is True

    # The same call finalised a second time must not bill twice.
    repeated = await persist_token_usage(
        db,
        tenant,
        call_id=call_id,
        input_tokens=120,
        output_tokens=45,
        provider="openai_realtime",
    )
    await db.commit()
    assert repeated is False


@pytest.mark.asyncio
async def test_a_session_without_tokens_writes_nothing(db) -> None:
    tenant = await make_tenant(db, name="S2S Empty Tenant")
    assert (
        await persist_token_usage(
            db, tenant, call_id=uuid.uuid4(), input_tokens=0, output_tokens=0
        )
        is False
    )


@pytest.mark.asyncio
async def test_the_tenant_spend_is_the_sum_of_its_sessions(db) -> None:
    tenant = await make_tenant(db, name="S2S Spend Tenant")
    other = await make_tenant(db, name="S2S Other Tenant")
    db.add_all(
        [
            S2SSessionRecord(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                provider="openai_realtime",
                model="gpt-4o-realtime-preview",
                status="completed",
                cost_micro_usd=1200,
                cost_known=True,
            ),
            S2SSessionRecord(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                provider="gemini_live",
                model="gemini-2.0-flash-exp",
                status="failed_over",
                failover_reason="provider_error",
                error="the provider dropped the socket",
                cost_micro_usd=300,
                cost_known=True,
            ),
            S2SSessionRecord(
                id=uuid.uuid4(),
                tenant_id=other.id,
                provider="openai_realtime",
                model="gpt-4o-realtime-preview",
                status="completed",
                cost_micro_usd=9999,
                cost_known=True,
            ),
        ]
    )
    await db.commit()

    assert await tenant_spend_micro_usd(db, tenant.id) == 1500
    assert await tenant_spend_micro_usd(db, other.id) == 9999
