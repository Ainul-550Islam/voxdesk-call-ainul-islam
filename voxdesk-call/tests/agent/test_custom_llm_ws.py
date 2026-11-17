"""PART 12 / P12-11 — custom-LLM WebSocket contract, signature, reconnect, failover."""

from __future__ import annotations

import json

import pytest

from app.agent.providers.custom_llm_ws import (
    FIRST_TOKEN_TIMEOUT_S,
    INBOUND_EVENTS,
    CustomLlmConfig,
    CustomLlmProtocolError,
    FirstTokenWatchdog,
    ReconnectPolicy,
    WebhookSignatureError,
    end_call_frame,
    parse_inbound,
    response_frame,
    send_frame,
    sign_frame,
    tool_call_frame,
    verify_frame,
)


def test_config_frame_carries_tools_and_the_response_budget() -> None:
    frame = CustomLlmConfig(
        url="wss://customer.example/llm",
        model="their-model",
        voice_id="v1",
        transcript_id="t1",
        tools=({"name": "book", "description": "book it"},),
        variables={"caller": "+1555"},
    ).to_frame()
    assert frame["event"] == "config"
    assert frame["config"]["model"] == "their-model"
    assert frame["config"]["tools"][0]["name"] == "book"
    assert frame["config"]["response_timeout_ms"] == 2000


@pytest.mark.parametrize("event", INBOUND_EVENTS)
def test_every_contract_event_parses(event) -> None:
    parsed = parse_inbound(json.dumps({"event": event, "x": 1}))
    assert parsed.name == event
    assert parsed.payload == {"x": 1}


def test_update_only_is_a_control_frame_not_a_turn() -> None:
    assert parse_inbound({"event": "update_only"}).is_control_only is True
    assert parse_inbound({"event": "response_required"}).wants_response is True
    assert parse_inbound({"event": "reminder_required"}).wants_response is True


def test_an_unknown_event_is_rejected() -> None:
    with pytest.raises(CustomLlmProtocolError, match="unknown inbound event"):
        parse_inbound({"event": "do_the_thing"})


def test_signature_round_trip_and_tamper_detection() -> None:
    body = b'{"event":"response_required"}'
    signature = sign_frame(body, "s3cret")
    assert verify_frame(body, signature, "s3cret") is True
    assert verify_frame(body + b" ", signature, "s3cret") is False
    assert verify_frame(body, signature, "wrong") is False
    assert verify_frame(body, "", "s3cret") is False


def test_a_bad_signature_stops_the_frame_before_it_reaches_the_parser() -> None:
    body = b'{"event":"response_required"}'
    with pytest.raises(WebhookSignatureError):
        parse_inbound(body, secret="s3cret", signature="deadbeef")


def test_signed_frames_parse_when_the_signature_matches() -> None:
    body = json.dumps({"event": "response_required", "response_id": "r1"}).encode()
    parsed = parse_inbound(body, secret="k", signature=sign_frame(body, "k"))
    assert parsed.payload["response_id"] == "r1"


def test_backoff_is_exponential_and_capped() -> None:
    policy = ReconnectPolicy(initial_s=0.5, factor=2.0, max_s=8.0, max_attempts=6)
    assert policy.sequence() == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0]
    assert policy.exhausted(6) is False
    assert policy.exhausted(7) is True


def test_the_first_token_budget_is_two_seconds() -> None:
    watchdog = FirstTokenWatchdog()
    watchdog.start(at=100.0)
    assert watchdog.on_token(at=101.9) == "ok"
    assert watchdog.on_token(at=102.1) == "failover"
    assert FIRST_TOKEN_TIMEOUT_S == 2.0


def test_a_watchdog_that_never_saw_a_token_is_not_expired() -> None:
    watchdog = FirstTokenWatchdog(timeout_s=2.0)
    assert watchdog.expired(at=999.0) is False


def test_outbound_frames_have_the_documented_shape() -> None:
    assert response_frame(response_id="r1", content="hello") == {
        "event": "response",
        "response_id": "r1",
        "content": "hello",
        "content_complete": True,
        "end_call": False,
    }
    call = tool_call_frame(call_id="c1", name="book", arguments={"slot": "3pm"})
    assert call["event"] == "tool_call" and call["call"]["timeout_seconds"] == 10.0
    assert end_call_frame() == {"event": "end_call", "reason": "agent_ended"}


def test_a_tool_call_without_a_name_is_refused() -> None:
    with pytest.raises(CustomLlmProtocolError, match="needs a name"):
        tool_call_frame(call_id="c1", name="", arguments={})


async def test_send_frame_returns_the_exact_wire_text() -> None:
    sent: list[str] = []

    async def send(text: str) -> None:
        sent.append(text)

    text = await send_frame(send, response_frame(response_id="r9", content="ok"))
    assert sent == [text]
    assert json.loads(text)["response_id"] == "r9"
    assert " " not in text, "frames are compact-encoded"
