"""PART 12 / P12-11 — structured output: schema pass-through and the failure path."""

from __future__ import annotations

import pytest

from app.agent.providers.llm_providers import (
    StructuredOutputError,
    fast_tier,
    parse_structured_response,
    structured_output_params,
)

SCHEMA = {
    "type": "object",
    "required": ["intent", "confidence"],
    "properties": {
        "intent": {"type": "string"},
        "confidence": {"type": "number"},
        "needs_human": {"type": "boolean"},
    },
}


def test_no_schema_means_no_parameters() -> None:
    assert structured_output_params(None) == {}
    assert structured_output_params({}) == {}


def test_the_schema_is_passed_through_untouched() -> None:
    params = structured_output_params(SCHEMA, name="triage", strict=True)
    assert params["type"] == "json_schema"
    assert params["json_schema"]["schema"] is SCHEMA
    assert params["json_schema"]["name"] == "triage"
    assert params["json_schema"]["strict"] is True


def test_a_schema_without_a_type_is_refused_before_the_call() -> None:
    with pytest.raises(StructuredOutputError, match="top-level 'type'"):
        structured_output_params({"required": ["a"]})


def test_a_valid_response_parses() -> None:
    data = parse_structured_response('{"intent": "refill", "confidence": 0.91}', SCHEMA)
    assert data["intent"] == "refill"


def test_missing_required_keys_are_named() -> None:
    with pytest.raises(StructuredOutputError, match="missing required keys: confidence"):
        parse_structured_response('{"intent": "refill"}', SCHEMA)


def test_a_wrong_type_is_named() -> None:
    with pytest.raises(StructuredOutputError, match="confidence should be number"):
        parse_structured_response('{"intent": "refill", "confidence": "high"}', SCHEMA)


def test_non_json_output_is_a_structured_error_not_a_crash() -> None:
    with pytest.raises(StructuredOutputError, match="was not JSON"):
        parse_structured_response("Sure! I can help with that.", SCHEMA)


def test_a_json_array_is_not_an_object() -> None:
    with pytest.raises(StructuredOutputError, match="must be a JSON object"):
        parse_structured_response("[1, 2, 3]", SCHEMA)


def test_parsing_without_a_schema_is_refused() -> None:
    with pytest.raises(StructuredOutputError, match="no schema configured"):
        parse_structured_response('{"intent": "x"}', None)


def test_optional_keys_are_checked_when_present() -> None:
    with pytest.raises(StructuredOutputError, match="needs_human should be boolean"):
        parse_structured_response(
            '{"intent": "x", "confidence": 0.4, "needs_human": "yes"}', SCHEMA
        )


def test_fast_tier_lookup_is_case_insensitive_and_conservative() -> None:
    assert fast_tier("GPT-4o-mini") is True
    assert fast_tier("claude-3-5-haiku-latest") is True
    assert fast_tier("gpt-4o") is False
    assert fast_tier("") is False


def test_openai_factory_passes_the_json_schema_to_the_live_service(monkeypatch) -> None:
    from app.agent import llm_factory

    monkeypatch.setattr(llm_factory, "_has_key", lambda _provider: True)
    monkeypatch.setattr(llm_factory, "_api_key", lambda _provider: "sk-unit-test")
    service = llm_factory.build_llm(
        "openai", "gpt-4o-mini", structured_output_schema=SCHEMA, structured_output_strict=False
    )
    response_format = service._settings["extra"]["response_format"]
    assert response_format["json_schema"]["schema"] is SCHEMA
    assert response_format["json_schema"]["strict"] is False


def test_google_factory_uses_json_mime_type_and_response_schema(monkeypatch) -> None:
    from app.agent import llm_factory

    monkeypatch.setattr(llm_factory, "_has_key", lambda _provider: True)
    monkeypatch.setattr(llm_factory, "_api_key", lambda _provider: "google-unit-test")
    service = llm_factory.build_llm("google", "gemini-2.0-flash", structured_output_schema=SCHEMA)
    assert service._settings["extra"] == {
        "response_mime_type": "application/json",
        "response_schema": SCHEMA,
    }


def test_anthropic_does_not_silently_drop_configured_json_schema(monkeypatch) -> None:
    from app.agent import llm_factory
    from app.agent.errors import UnsupportedProviderFeatureError

    monkeypatch.setattr(llm_factory, "_has_key", lambda _provider: True)
    monkeypatch.setattr(llm_factory, "_api_key", lambda _provider: "anthropic-unit-test")
    with pytest.raises(UnsupportedProviderFeatureError, match="structured output"):
        llm_factory.build_llm("anthropic", "claude-haiku-4-5", structured_output_schema=SCHEMA)
