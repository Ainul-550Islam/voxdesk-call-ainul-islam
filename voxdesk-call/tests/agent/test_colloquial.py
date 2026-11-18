"""P12-01 — the colloquial rewriter must change the register and never the facts."""

from __future__ import annotations

import random

import pytest
from pipecat.frames.frames import TextFrame
from pipecat.processors.frame_processor import FrameDirection

from app.agent.colloquial import (
    DEFAULT_BUDGET_MS,
    ColloquialRewriter,
    protected_facts,
    rewrite,
)


def test_corporate_sentences_become_speech() -> None:
    result = rewrite(
        "I would be happy to assist you with that request at this time. "
        "Please be advised that we are currently experiencing a delay due to the fact that "
        "the system is down."
    )
    assert not result.bypassed
    assert result.changed
    lowered = result.text.lower()
    for stiff in ("would be happy to assist", "at this time", "please be advised", "due to the fact that"):
        assert stiff not in lowered, lowered
    assert "i'd be happy to help" in lowered
    assert "right now" in lowered
    assert "because" in lowered
    assert result.latency_ms < DEFAULT_BUDGET_MS


def test_facts_are_byte_identical_after_rewriting() -> None:
    text = (
        "Please be advised that your appointment at 3:30 PM on 2026-11-12 for $1,250.00 "
        "was confirmed. Reference ORD-1042 and email ops@example.com or visit "
        "https://voxdesk.example.com/portal for approximately 15% more detail."
    )
    result = rewrite(text)
    assert not result.bypassed, result.reason
    before = sorted(protected_facts(text))
    after = sorted(protected_facts(result.text))
    assert before == after, (before, after)
    for fact in ("3:30 pm", "2026-11-12", "$1,250.00", "ord-1042", "ops@example.com", "15%"):
        assert fact in after, fact
    assert "https://voxdesk.example.com/portal" in result.text


def test_a_rule_that_would_eat_a_fact_is_discarded() -> None:
    """The guard is exercised with a deliberately destructive rule, not trusted by inspection."""
    import app.agent.colloquial as mod

    text = "You may wish to call 555-0134 for additional detail."
    original_rules = mod.WORD_REWRITES
    mod.WORD_REWRITES = original_rules + ((r"\badditional\b", ""),)
    try:
        result = mod.rewrite(text)
    finally:
        mod.WORD_REWRITES = original_rules
    # Removing the word is harmless, so the facts stay equal and the rewrite stands.
    assert result.text != text
    assert "555-0134" in result.text

    mod.WORD_REWRITES = original_rules + ((r"555-0134", "555-9999"),)
    try:
        guarded = mod.rewrite(text)
    finally:
        mod.WORD_REWRITES = original_rules
    assert guarded.bypassed is True
    assert guarded.reason == "entity_guard"
    assert guarded.text == text, "a rewrite that changes a number must be discarded"


def test_budget_exhaustion_returns_the_original_text() -> None:
    # First call is `started`; the rewrite then checks the deadline once per stage. Let the
    # stages start promptly and make the final check land past the 50 ms budget.
    ticks = iter([0.0, 0.001, 0.001, 0.001, 1.0, 1.0, 1.0, 1.0])

    def clock() -> float:
        return next(ticks)

    result = rewrite(
        "I would be happy to assist you at this time with your request.",
        budget_ms=50.0,
        clock=clock,
    )
    assert result.bypassed is True
    assert result.reason == "budget"
    assert result.text == "I would be happy to assist you at this time with your request."


def test_zero_budget_always_bypasses() -> None:
    text = "I am unable to assist with that."
    result = rewrite(text, budget_ms=0.0)
    assert result.bypassed is True and result.text == text


def test_empty_and_whitespace_are_returned_untouched() -> None:
    assert rewrite("").text == ""
    assert rewrite("   ").text == "   "


def test_long_sentences_are_split_without_losing_words() -> None:
    text = (
        "Your order was placed on Tuesday morning and the courier has already collected it "
        "from our warehouse so it should arrive at the address we have on file tomorrow "
        "before noon if the traffic is normal."
    )
    result = rewrite(text)
    assert not result.bypassed
    assert result.text.count(".") >= 2, result.text
    for word in ("courier", "warehouse", "traffic"):
        assert word in result.text
    before_words = len(text.split())
    after_words = len(result.text.split())
    assert abs(after_words - before_words) <= 2, (before_words, after_words)


def test_opener_is_seeded_and_bounded() -> None:
    text = "Your table is booked for four people."
    always = rewrite(text, add_opener=True, opener_probability=1.0, rng=random.Random(7))
    assert always.text.lower().startswith(("sure,", "okay,", "alright,", "so,")), always.text
    never = rewrite(text, add_opener=True, opener_probability=0.0, rng=random.Random(7))
    assert not never.text.lower().startswith(("sure,", "okay,", "alright,", "so,"))


def test_contractions_and_register_swaps() -> None:
    result = rewrite(
        "It is not possible to utilize that form; we are not able to commence the process "
        "and you are required to obtain a reference number."
    )
    lowered = result.text.lower()
    assert "it's not possible" in lowered or "it isn't possible" in lowered
    assert "use" in lowered
    assert "utilize" not in lowered
    assert "start" in lowered
    assert "need" in lowered or "needed" in lowered


@pytest.mark.asyncio
async def test_processor_rewrites_text_frames_and_emits_latency_metric() -> None:
    rewriter = ColloquialRewriter(budget_ms=DEFAULT_BUDGET_MS, rng=random.Random(1))
    frame = TextFrame(text="Please be advised that I am unable to assist at this time.")

    pushed: list[str] = []
    original_push = rewriter.push_frame

    async def capture(pushed_frame, direction=FrameDirection.DOWNSTREAM):
        if isinstance(pushed_frame, TextFrame):
            pushed.append(pushed_frame.text)
        return None

    rewriter.push_frame = capture  # type: ignore[assignment]
    try:
        await rewriter.process_frame(frame, FrameDirection.DOWNSTREAM)
    finally:
        rewriter.push_frame = original_push  # type: ignore[assignment]

    assert pushed and "please be advised" not in pushed[0].lower()
    names = [m["name"] for m in rewriter.metric_samples]
    assert "colloquial_latency_ms" in names
    summary = rewriter.summary()
    assert summary["rewrites"] == 1
    assert summary["max_latency_ms"] < DEFAULT_BUDGET_MS


@pytest.mark.asyncio
async def test_processor_bypass_keeps_text_and_counts() -> None:
    rewriter = ColloquialRewriter(budget_ms=0.0)
    frame = TextFrame(text="I am unable to assist with that.")
    seen: list[str] = []

    async def capture(pushed_frame, direction=FrameDirection.DOWNSTREAM):
        if isinstance(pushed_frame, TextFrame):
            seen.append(pushed_frame.text)

    rewriter.push_frame = capture  # type: ignore[assignment]
    await rewriter.process_frame(frame, FrameDirection.DOWNSTREAM)
    assert seen == ["I am unable to assist with that."]
    assert rewriter.bypass_count == 1
    assert any(m["name"] == "colloquial_bypassed" for m in rewriter.metric_samples)


@pytest.mark.asyncio
async def test_disabled_processor_is_a_no_op() -> None:
    rewriter = ColloquialRewriter(enabled=False)
    frame = TextFrame(text="Please be advised that I am unable to assist.")
    seen: list[str] = []

    async def capture(pushed_frame, direction=FrameDirection.DOWNSTREAM):
        if isinstance(pushed_frame, TextFrame):
            seen.append(pushed_frame.text)

    rewriter.push_frame = capture  # type: ignore[assignment]
    await rewriter.process_frame(frame, FrameDirection.DOWNSTREAM)
    assert seen == ["Please be advised that I am unable to assist."]
    assert rewriter.metric_samples == []
