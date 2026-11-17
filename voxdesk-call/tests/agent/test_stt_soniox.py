"""PART 12 / P12-08 — Soniox request shape, endpoint tokens, medical vocabulary, presets."""

from __future__ import annotations

import base64
import json

import pytest

from app.agent.providers.stt_providers import (
    ENDPOINTING_PRESETS,
    MEDICAL_VOCABULARY_EN,
    SonioxSessionConfig,
    SonioxSTTProvider,
    endpointing_ms,
    medical_vocabulary,
    soniox_endpointing_preset,
)


def test_soniox_open_message_carries_what_the_service_needs() -> None:
    provider = SonioxSTTProvider(api_key="sx-key", config=SonioxSessionConfig(language_hints=("en",)))
    payload = json.loads(provider.open_message())
    assert payload["api_key"] == "sx-key"
    assert payload["model"] == "stt-rt-v3"
    assert payload["audio_format"] == "mulaw"
    assert payload["sample_rate"] == 8000
    assert payload["num_channels"] == 1
    assert payload["language_hints"] == ["en"]
    assert payload["enable_endpoint_detection"] is True


def test_a_missing_key_fails_before_the_socket_opens() -> None:
    provider = SonioxSTTProvider(api_key="")
    with pytest.raises(ValueError, match="API key"):
        provider.open_message()


def test_terms_are_sent_as_context_only_when_present() -> None:
    bare = SonioxSessionConfig().to_wire(api_key="k")
    assert "context" not in bare and "client_reference_id" not in bare

    pinned = SonioxSessionConfig(terms=("metformin",), client_reference_id="call-1").to_wire(api_key="k")
    assert pinned["context"] == {"terms": ["metformin"]}
    assert pinned["client_reference_id"] == "call-1"


@pytest.mark.parametrize("bad", [4000, 22050, 44100])
def test_unsupported_sample_rates_are_refused(bad) -> None:
    with pytest.raises(ValueError, match="8000 or 16000"):
        SonioxSessionConfig(sample_rate=bad)


def test_endpoint_sensitivity_is_bounded() -> None:
    with pytest.raises(ValueError, match="endpoint_sensitivity"):
        SonioxSessionConfig(endpoint_sensitivity=1.5)


async def test_audio_is_base64_encoded_and_the_session_opens_first() -> None:
    sent: list[str] = []

    async def send(message: str) -> None:
        sent.append(message)

    provider = SonioxSTTProvider(api_key="k", send=send)
    await provider.push_audio(b"\x00\x01\x02")

    assert provider.opened is True
    assert json.loads(sent[0])["api_key"] == "k", "the session opens with a config message"
    assert base64.b64decode(sent[1]) == b"\x00\x01\x02"


async def test_audio_without_a_transport_is_an_error_not_a_silent_drop() -> None:
    provider = SonioxSTTProvider(api_key="k")
    with pytest.raises(RuntimeError, match="transport"):
        await provider.push_audio(b"\x00")


def test_final_tokens_accumulate_until_the_end_marker() -> None:
    provider = SonioxSTTProvider(api_key="k")
    first = provider.handle_message(
        {"tokens": [{"text": "my ", "is_final": True}, {"text": "refill", "is_final": False}]}
    )
    assert first == "my"
    assert provider.utterances == 0

    second = provider.handle_message(
        {"tokens": [{"text": "number ", "is_final": True}, {"text": "<end>", "is_final": True}]}
    )
    assert second == "number"
    assert provider.utterances == 1
    assert provider.final_transcripts == ["my", "number"]


def test_an_event_with_no_finals_produces_nothing() -> None:
    provider = SonioxSTTProvider(api_key="k")
    assert provider.handle_message({"tokens": [{"text": "he", "is_final": False}]}) == ""
    assert provider.final_transcripts == []


def test_transcript_callback_sees_the_end_flag() -> None:
    seen: list[tuple[str, bool]] = []
    provider = SonioxSTTProvider(api_key="k", on_transcript=lambda text, ended: seen.append((text, ended)))
    provider.handle_message({"tokens": [{"text": "hello", "is_final": True}, {"text": "<end>"}]})
    assert seen == [("hello", True)]


def test_medical_vocabulary_is_english_only_and_opt_in() -> None:
    assert medical_vocabulary(False) == []
    assert medical_vocabulary(True, language="es-US") == []
    terms = medical_vocabulary(True, language="en-GB")
    assert terms == list(MEDICAL_VOCABULARY_EN)


def test_medical_vocabulary_accepts_extra_terms_without_duplicates() -> None:
    terms = medical_vocabulary(True, extra_terms=["metformin", "  ", "ozempic"])
    assert terms.count("metformin") == 1
    assert "ozempic" in terms
    assert all(term == term.strip() for term in terms)


def test_endpointing_presets_are_ordered_and_explicit() -> None:
    assert ENDPOINTING_PRESETS["latency"] < ENDPOINTING_PRESETS["default"] < ENDPOINTING_PRESETS["accuracy"]
    assert endpointing_ms(None) == ENDPOINTING_PRESETS["default"]
    assert endpointing_ms("ACCURACY") == ENDPOINTING_PRESETS["accuracy"]


def test_an_unknown_endpointing_mode_fails_instead_of_guessing() -> None:
    with pytest.raises(ValueError, match="unknown stt_endpointing_mode"):
        endpointing_ms("turbo")


def test_soniox_endpointing_preset_uses_the_shared_mode_names() -> None:
    default = soniox_endpointing_preset(None)
    accuracy = soniox_endpointing_preset("ACCURACY")
    latency = soniox_endpointing_preset("latency")
    assert default == {"endpoint_sensitivity": 0.7, "max_endpoint_delay_ms": 1000}
    assert accuracy["max_endpoint_delay_ms"] > default["max_endpoint_delay_ms"]
    assert latency["max_endpoint_delay_ms"] < default["max_endpoint_delay_ms"]
    with pytest.raises(ValueError, match="unknown stt_endpointing_mode"):
        soniox_endpointing_preset("turbo")
