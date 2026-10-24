"""The failover from speech-to-speech to the cascaded STT -> LLM -> TTS
pipeline.

A realtime provider can fail in the middle of a live call. Ending the call
is the wrong answer -- the caller is already on the line -- so the session
falls back to the cascaded pipeline with the conversation carried over, and
every failover is counted in ``voxdesk_s2s_failover_total{reason}``.
"""

from __future__ import annotations

import pytest

from app.agent.s2s import s2s_failover
from app.agent.s2s.s2s_failover import (
    REASON_BUDGET_EXCEEDED,
    REASON_CONNECTION_LOST,
    REASON_DISABLED,
    REASON_PROVIDER_ERROR,
    REASON_TIMEOUT,
    CascadedFallback,
    S2S_FAILOVER_TOTAL,
    failover_reason,
    failover_to_cascaded,
)
from app.agent.s2s.s2s_session import (
    ReconnectPolicy,
    S2SBudgetExceeded,
    S2SProviderError,
    S2SSessionTimeout,
)


# ---------------------------------------------------------------------------
# The reason vocabulary
# ---------------------------------------------------------------------------
def test_failover_reasons_are_a_closed_vocabulary() -> None:
    assert failover_reason(S2SSessionTimeout("t")) == REASON_TIMEOUT
    assert failover_reason(S2SBudgetExceeded("b")) == REASON_BUDGET_EXCEEDED
    assert failover_reason(S2SProviderError("p")) == REASON_PROVIDER_ERROR
    assert failover_reason("the socket closed") == REASON_CONNECTION_LOST
    assert failover_reason("something else") == REASON_PROVIDER_ERROR


def test_a_text_failure_is_mapped_onto_a_reason() -> None:
    assert failover_reason("connect timed out") == REASON_CONNECTION_LOST
    assert failover_reason("the budget is gone") == REASON_BUDGET_EXCEEDED
    assert failover_reason("timeout while waiting") == REASON_TIMEOUT


def test_the_failover_counter_is_exported() -> None:
    before = S2S_FAILOVER_TOTAL.labels(reason=REASON_TIMEOUT)._value.get()
    S2S_FAILOVER_TOTAL.labels(reason=REASON_TIMEOUT).inc()
    assert S2S_FAILOVER_TOTAL.labels(reason=REASON_TIMEOUT)._value.get() == before + 1


def test_the_reconnect_policy_is_bounded() -> None:
    policy = ReconnectPolicy(max_attempts=3, base_delay_seconds=0.25, max_delay_seconds=1.0)
    assert policy.delay_for(1) == 0.0
    assert policy.delay_for(2) == pytest.approx(0.25)
    assert policy.delay_for(3) == pytest.approx(0.5)
    assert policy.delay_for(9) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# The failover itself
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_disabled_fallback_returns_nothing_and_is_counted(monkeypatch) -> None:
    before = S2S_FAILOVER_TOTAL.labels(reason=REASON_DISABLED)._value.get()
    result = await failover_to_cascaded(
        error=S2SProviderError("the provider is down"),
        session=None,
        tenant=None,
        call=None,
        runtime_config=None,
        fallback_enabled=False,
        provider="openai_realtime",
    )
    assert result is None
    assert S2S_FAILOVER_TOTAL.labels(reason=REASON_DISABLED)._value.get() == before + 1


@pytest.mark.asyncio
async def test_the_fallback_carries_the_conversation_over(monkeypatch) -> None:
    async def _fake_build(**kwargs):
        return CascadedFallback(
            stt=object(),
            llm=object(),
            tts=object(),
            system_prompt=str(kwargs.get("runtime_config") or ""),
            history=list(kwargs.get("history") or []),
            provider=str(kwargs.get("provider") or ""),
        )

    monkeypatch.setattr(s2s_failover, "build_cascaded_fallback", _fake_build)
    before = S2S_FAILOVER_TOTAL.labels(reason=REASON_PROVIDER_ERROR)._value.get()
    fallback = await failover_to_cascaded(
        error=S2SProviderError("the provider is down"),
        session=None,
        tenant=None,
        call=None,
        runtime_config="You are a receptionist.",
        history=[{"role": "user", "content": "I need an appointment"}],
        provider="openai_realtime",
    )
    assert fallback is not None
    assert fallback.provider == "openai_realtime"
    assert fallback.history == [{"role": "user", "content": "I need an appointment"}]
    assert S2S_FAILOVER_TOTAL.labels(reason=REASON_PROVIDER_ERROR)._value.get() == before + 1


def test_the_cascaded_context_is_seeded_with_the_history() -> None:
    fallback = CascadedFallback(
        system_prompt="You are a receptionist.",
        history=[
            {"role": "user", "content": "I need an appointment"},
            {"role": "assistant", "content": "Of course."},
        ],
    )
    context = fallback.context()
    messages = context.get_messages()
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are a receptionist."
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"


def test_the_cascaded_fallback_reports_what_it_carries() -> None:
    fallback = CascadedFallback(
        stt=object(),
        history=[{"role": "user", "content": "hello"}],
        provider="gemini_live",
        llm_provider="openai",
        llm_model="gpt-4o-mini",
    )
    described = fallback.as_dict()
    assert described["provider"] == "gemini_live"
    assert described["llm_model"] == "gpt-4o-mini"
    assert described["history_turns"] == 1
    assert described["has_stt"] is True
    assert described["has_llm"] is False
    assert described["has_tts"] is False
