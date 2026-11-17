"""PART 13 / P13-09 — the interactive handoff assistant.

The assistant talks to the *human*, not the caller. It has three jobs and this
file checks each one separately:

* answer the human's question from facts it was given, and say "I don't have
  that detail" when it was not;
* accept or reject according to the tenant's criteria;
* time out into a reject with a reason, because silence is not consent.
"""

from __future__ import annotations

import pytest

from app.agent.handoff_assistant import (
    ACCEPT,
    REJECT,
    REJECT_REASONS,
    UNKNOWN_ANSWER,
    Criteria,
    HandoffAssistant,
    HandoffContext,
    answer,
    brief,
    script,
    topic_of,
)


@pytest.fixture()
def context() -> HandoffContext:
    return HandoffContext(
        call_id="CA-1",
        tenant_id="tenant-1",
        caller_number="+8801712345678",
        caller_name="Ainul",
        intent="billing_dispute",
        summary="Double charged for October.",
        facts={"account_tier": "gold", "reason": "double charge"},
        sentiment="frustrated",
        previous_calls=2,
    )


@pytest.fixture()
def assistant() -> HandoffAssistant:
    return HandoffAssistant(Criteria(required_skills=("billing",), required_languages=("en",)))


def test_the_brief_names_who_is_calling_and_why(context) -> None:
    text = brief(context)
    assert "Ainul" in text
    assert "billing dispute" in text
    assert "frustrated" in text
    assert text.startswith("Ainul is on the line")


def test_the_brief_is_bounded(context) -> None:
    verbose = HandoffContext(call_id="x", summary="word " * 500)
    assert len(brief(verbose)) <= 320 + 200  # summary is truncated, the header is not


def test_a_known_question_is_answered_from_the_facts(context) -> None:
    assert "double charge" in (answer(context, "What are they calling about?") or "")


def test_caller_history_is_answered_without_a_lookup(context) -> None:
    assert answer(context, "Have they called before?") == "They have called 2 time(s) before."


def test_a_first_time_caller_is_described_as_such(context) -> None:
    fresh = HandoffContext(call_id="CA-2", previous_calls=0)
    assert answer(fresh, "any history?") == "This is their first call with us."


def test_a_question_we_cannot_answer_returns_none(context) -> None:
    assert answer(context, "What is the outstanding invoice number?") is None
    assert answer(context, "what is their account tier") == "Account tier: gold."


def test_the_assistant_says_it_does_not_know(context, assistant) -> None:
    decision = assistant.evaluate(context, question="What is the invoice number?")
    assert decision.answer == UNKNOWN_ANSWER


def test_an_unknown_topic_is_not_guessed() -> None:
    assert topic_of("what is the airspeed of an unladen swallow") is None


def test_a_qualified_human_accepts(context, assistant) -> None:
    decision = assistant.evaluate(
        context,
        human={"skills": ["billing"], "languages": ["en"]},
        human_decision=ACCEPT,
    )
    assert decision.accepted is True
    assert decision.reason is None
    assert decision.matched_skill == "billing"


def test_a_human_without_the_skill_is_rejected_with_a_reason(context, assistant) -> None:
    decision = assistant.evaluate(
        context, human={"skills": ["sales"], "languages": ["en"]}, human_decision=ACCEPT
    )
    assert decision.accepted is False
    assert decision.reason == "wrong_skill"


def test_a_human_without_the_language_is_rejected(context, assistant) -> None:
    decision = assistant.evaluate(
        context, human={"skills": ["billing"], "languages": ["bn"]}, human_decision=ACCEPT
    )
    assert decision.reason == "wrong_language"


def test_the_human_can_reject_and_the_reason_is_kept(context, assistant) -> None:
    decision = assistant.evaluate(
        context,
        human={"skills": ["billing"], "languages": ["en"]},
        human_decision=REJECT,
        reject_reason="unavailable",
    )
    assert decision.accepted is False
    assert decision.reason == "unavailable"


def test_an_unknown_reject_reason_does_not_become_free_text(context, assistant) -> None:
    decision = assistant.evaluate(
        context,
        human={"skills": ["billing"], "languages": ["en"]},
        human_decision=REJECT,
        reject_reason="I just don't feel like it",
    )
    assert decision.reason == "caller_requested_human_refused"


def test_silence_times_out_into_a_reject(context, assistant) -> None:
    decision = assistant.evaluate(
        context,
        human={"skills": ["billing"], "languages": ["en"]},
        elapsed_seconds=40,
    )
    assert decision.accepted is False
    assert decision.reason == "timeout"


def test_silence_before_the_timeout_is_not_consent(context, assistant) -> None:
    decision = assistant.evaluate(
        context,
        human={"skills": ["billing"], "languages": ["en"]},
        elapsed_seconds=3,
    )
    assert decision.accepted is False
    assert decision.reason is None, "no reason yet: the human is still listening"


def test_closed_hours_block_the_transfer(context) -> None:
    closed = HandoffAssistant(Criteria(within_hours=False))
    decision = closed.evaluate(context, human={"skills": ["billing"]}, human_decision=ACCEPT)
    assert decision.accepted is False
    assert decision.reason == "policy_blocked"


def test_no_capacity_blocks_the_transfer(context) -> None:
    full = HandoffAssistant(Criteria(has_capacity=False))
    decision = full.evaluate(context, human={}, human_decision=ACCEPT)
    assert decision.reason == "unavailable"


def test_every_reject_reason_is_a_stable_name() -> None:
    assert "timeout" in REJECT_REASONS
    assert all(" " not in reason for reason in REJECT_REASONS)


def test_the_preview_script_answers_a_list_of_questions(context) -> None:
    rows = script(["What do they want?", "invoice number?"], context)
    assert rows[0]["answer"] is not None
    assert rows[1]["answer"] is None
