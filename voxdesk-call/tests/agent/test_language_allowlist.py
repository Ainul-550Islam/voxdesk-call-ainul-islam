"""P12-07 — a per-agent language allowlist that detection is restricted to."""

from __future__ import annotations

import pytest

from app.agent.language import (
    LanguagePolicy,
    build_language_policy,
    decide_language,
    detection_constraints,
    execute_switch_language,
)


def test_single_language_default() -> None:
    policy = build_language_policy(language="en-US")
    assert policy.primary == "en-US"
    assert policy.allowlist == ("en-US",)
    assert policy.validate() == []
    assert policy.allows("en-US") and not policy.allows("es-US")


def test_allowlist_from_a_comma_separated_string_and_a_list() -> None:
    from_string = build_language_policy(languages="en-US, es-US", language="en-US")
    from_list = build_language_policy(languages=["en-US", "es-US"], language="en-US")
    assert from_string.allowlist == from_list.allowlist == ("en-US", "es-US")

    # A list that omits the primary gets the primary added, not silently dropped.
    policy = build_language_policy(languages=["es-US"], language="en-US")
    assert policy.allowlist == ("en-US", "es-US")


def test_multi_turns_on_detection_and_needs_two_languages() -> None:
    policy = build_language_policy(languages=["en-US", "es-US", "multi"], language="en-US")
    assert policy.detect_multilingual is True
    assert policy.allowlist == ("en-US", "es-US")
    constraints = detection_constraints(policy)
    assert constraints["stt_language"] == "multi"
    assert constraints["stt_model"] == "nova-3"
    assert "en-US, es-US" in constraints["llm_instruction"]

    with pytest.raises(ValueError, match="detect_multilingual"):
        build_language_policy(languages=["en-US", "multi"], language="en-US")


def test_an_allowed_language_is_replied_in() -> None:
    policy = build_language_policy(languages=["en-US", "es-US"], language="en-US")
    decision = decide_language("es-US", policy)
    assert decision.allowed is True
    assert decision.reply_language == "es-US"
    assert decision.instruction == ""


def test_a_disallowed_language_gets_the_fallback_phrase_not_a_guess() -> None:
    policy = build_language_policy(languages=["en-US", "es-US"], language="en-US")
    decision = decide_language("fr-FR", policy)
    assert decision.allowed is False
    assert decision.reply_language == "en-US"
    assert decision.fallback_phrase
    assert "does not support" in decision.instruction

    spoken_fallback = decide_language("de-DE", policy).fallback_phrase
    assert spoken_fallback == policy.fallback_phrase


def test_primary_only_policy_ignores_a_caller_switch() -> None:
    policy = build_language_policy(
        languages=["en-US", "es-US"], language="en-US", reply_policy="primary_only"
    )
    decision = decide_language("es-US", policy)
    assert decision.allowed is True
    assert decision.reply_language == "en-US"
    assert "always reply in en-US" in decision.instruction


def test_unknown_codes_and_bad_policies_are_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported codes"):
        build_language_policy(languages=["en-US", "klingon"], language="en-US")
    with pytest.raises(ValueError, match="at least one language"):
        build_language_policy(languages=[])

    broken = LanguagePolicy(primary="en-US", allowlist=("en-US", "es-US"), reply_policy="vibes")
    assert any("reply_policy" in problem for problem in broken.validate())

    not_listed = LanguagePolicy(primary="en-US", allowlist=("es-US",))
    assert any("must also appear in the allowlist" in problem for problem in not_listed.validate())


def test_no_detection_means_the_primary_language() -> None:
    policy = build_language_policy(languages=["en-US", "es-US"], language="en-US")
    for detected in (None, "", "multi"):
        decision = decide_language(detected, policy)
        assert decision.reply_language == "en-US"
        assert decision.allowed is True


def test_switch_tool_still_refuses_languages_outside_the_allowlist() -> None:
    with pytest.raises(ValueError, match="allowed_languages"):
        execute_switch_language("fr-FR", allowed_languages=["en-US", "es-US"])
    result = execute_switch_language("es-MX", allowed_languages=["en-US", "es-US"])
    assert result["ok"] is True
    assert result["language"].startswith("es")


def test_base_code_matching_is_tolerated() -> None:
    policy = build_language_policy(languages=["en-US", "es-US"], language="en-US")
    assert policy.allows("es") is True
    assert policy.allows("es-AR") is True
