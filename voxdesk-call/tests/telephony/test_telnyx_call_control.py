"""Telnyx Call Control v2: retry policy and idempotency.

The transport here is a scripted HTTP stub, which is the right level: what is
under test is *this* adapter's decision -- retry or do not retry, reuse the key or
mint a new one, refuse the call or issue the command. No assertion claims a real
Telnyx API round trip; the live round trip is covered by the ``live``-marked tests
that skip without ``TELNYX_API_KEY``.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.telephony.provider_errors import (
    ProviderAuthenticationError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
)
from app.telephony.providers.base import HttpResult
from app.telephony.providers.telnyx import TelnyxAdapter

pytestmark = pytest.mark.asyncio


class ScriptedTransport:
    """Records every attempt and replays a scripted list of outcomes."""

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    async def __call__(self, method, url, *, headers, json, timeout):
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "json": json, "timeout": timeout}
        )
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @property
    def attempts(self) -> int:
        return len(self.calls)

    @property
    def idempotency_keys(self) -> list[str]:
        return [call["headers"].get("Idempotency-Key", "") for call in self.calls]


@pytest.fixture(autouse=True)
def _telnyx_credentials(monkeypatch):
    """Credentials present, retries fast. Nothing here touches the network."""
    monkeypatch.setattr(settings, "telnyx_api_key", "KEY-test-0123456789abcdef")
    monkeypatch.setattr(settings, "telnyx_call_control_max_retries", 2)
    monkeypatch.setattr(settings, "telnyx_call_control_retry_backoff_seconds", 0.0)
    monkeypatch.setattr(settings, "telnyx_call_control_timeout_seconds", 2.0)
    yield


def _ok(body: dict | None = None) -> HttpResult:
    return HttpResult(200, body if body is not None else {"data": {"result": "ok"}})


# --------------------------------------------------------------------------- #
# Retry policy
# --------------------------------------------------------------------------- #


async def test_transient_5xx_is_retried_and_succeeds():
    transport = ScriptedTransport([HttpResult(503, {}), _ok()])
    adapter = TelnyxAdapter(transport=transport)

    result = await adapter.answer("v3:call-1")

    assert transport.attempts == 2
    assert result.external_id == "v3:call-1"
    assert result.status == "in_progress"


async def test_the_idempotency_key_is_identical_across_retries():
    transport = ScriptedTransport([HttpResult(502, {}), HttpResult(504, {}), _ok()])
    adapter = TelnyxAdapter(transport=transport)

    await adapter.hangup_call("v3:call-1")

    assert transport.attempts == 3
    keys = transport.idempotency_keys
    assert keys[0] and len(set(keys)) == 1
    assert keys[0].startswith("voxdesk-telnyx-hangup-")


async def test_429_is_retried():
    transport = ScriptedTransport([HttpResult(429, {}), _ok()])
    adapter = TelnyxAdapter(transport=transport)

    await adapter.answer("v3:call-1")

    assert transport.attempts == 2


async def test_transport_timeout_is_retried():
    transport = ScriptedTransport([ProviderTimeoutError("timeout", provider="telnyx"), _ok()])
    adapter = TelnyxAdapter(transport=transport)

    await adapter.answer("v3:call-1")

    assert transport.attempts == 2


async def test_retries_stop_at_the_configured_budget():
    transport = ScriptedTransport([HttpResult(500, {})])
    adapter = TelnyxAdapter(transport=transport)

    with pytest.raises(ProviderUnavailableError):
        await adapter.answer("v3:call-1")

    # 1 initial attempt + telnyx_call_control_max_retries retries
    assert transport.attempts == int(settings.telnyx_call_control_max_retries) + 1


@pytest.mark.parametrize("status", [400, 404, 409, 422])
async def test_client_errors_are_not_retried(status: int):
    transport = ScriptedTransport([HttpResult(status, {"errors": [{"detail": "nope"}]})])
    adapter = TelnyxAdapter(transport=transport)

    with pytest.raises(Exception) as error:
        await adapter.answer("v3:call-1")

    assert transport.attempts == 1
    assert getattr(error.value, "status_code", None) in {404, 409, 422}


@pytest.mark.parametrize("status", [401, 403])
async def test_credential_errors_are_never_retried(status: int):
    transport = ScriptedTransport([HttpResult(status, {})])
    adapter = TelnyxAdapter(transport=transport)

    with pytest.raises((ProviderAuthenticationError, ProviderValidationError, Exception)) as error:
        await adapter.answer("v3:call-1")

    assert transport.attempts == 1
    assert error.value.code in {"authentication", "authorization"}


async def test_a_transport_failure_after_the_budget_surfaces_the_last_error():
    transport = ScriptedTransport([ProviderUnavailableError("down", provider="telnyx")])
    adapter = TelnyxAdapter(transport=transport)

    with pytest.raises(ProviderUnavailableError):
        await adapter.send_dtmf("v3:call-1", "1234")

    assert transport.attempts == int(settings.telnyx_call_control_max_retries) + 1


# --------------------------------------------------------------------------- #
# Idempotency key derivation
# --------------------------------------------------------------------------- #


async def test_idempotency_key_is_stable_and_bounded():
    adapter = TelnyxAdapter()
    first = adapter._call_control_idempotency_key("transfer", "v3:call-1", {"to": "+15551230000"})
    second = adapter._call_control_idempotency_key("transfer", "v3:call-1", {"to": "+15551230000"})
    assert first == second
    assert len(first) <= 100


async def test_idempotency_key_changes_with_command_call_and_payload():
    adapter = TelnyxAdapter()
    base = adapter._call_control_idempotency_key("transfer", "v3:call-1", {"to": "+15551230000"})
    assert base != adapter._call_control_idempotency_key("hangup", "v3:call-1", {"to": "+15551230000"})
    assert base != adapter._call_control_idempotency_key("transfer", "v3:call-2", {"to": "+15551230000"})
    assert base != adapter._call_control_idempotency_key("transfer", "v3:call-1", {"to": "+15551239999"})


async def test_idempotency_key_is_order_insensitive_in_the_payload():
    adapter = TelnyxAdapter()
    left = adapter._call_control_idempotency_key("transfer", "c", {"a": 1, "b": 2})
    right = adapter._call_control_idempotency_key("transfer", "c", {"b": 2, "a": 1})
    assert left == right


# --------------------------------------------------------------------------- #
# Command surface
# --------------------------------------------------------------------------- #


async def test_answer_posts_to_the_v2_action_endpoint():
    transport = ScriptedTransport([_ok()])
    adapter = TelnyxAdapter(transport=transport)

    await adapter.answer("v3:call-1")

    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["url"].endswith("/calls/v3:call-1/actions/answer")
    assert call["timeout"] == pytest.approx(2.0)


async def test_transfer_normalises_the_destination():
    transport = ScriptedTransport([_ok()])
    adapter = TelnyxAdapter(transport=transport)

    result = await adapter.transfer_call("v3:call-1", "+1 (555) 123-0000")

    assert transport.calls[0]["json"] == {"to": "+15551230000"}
    assert result.to_number == "+15551230000"
    assert result.status == "transfer_requested"
    assert transport.calls[0]["url"].endswith("/actions/transfer")


async def test_transfer_can_name_the_caller_id():
    transport = ScriptedTransport([_ok()])
    adapter = TelnyxAdapter(transport=transport)

    await adapter.transfer_call("v3:call-1", "+15551230000", from_number="+15559876543")

    assert transport.calls[0]["json"] == {"to": "+15551230000", "from": "+15559876543"}


async def test_send_dtmf_accepts_dtmf_characters_only():
    transport = ScriptedTransport([_ok()])
    adapter = TelnyxAdapter(transport=transport)

    await adapter.send_dtmf("v3:call-1", "12#*9")

    assert transport.calls[0]["json"] == {"digits": "12#*9"}
    assert transport.calls[0]["url"].endswith("/actions/send_dtmf")


@pytest.mark.parametrize("digits", ["", "   ", "12A4x", "1" * 33])
async def test_send_dtmf_rejects_bad_input_without_a_provider_call(digits: str):
    transport = ScriptedTransport([_ok()])
    adapter = TelnyxAdapter(transport=transport)

    with pytest.raises(ProviderValidationError):
        await adapter.send_dtmf("v3:call-1", digits)

    assert transport.attempts == 0


async def test_hangup_and_transfer_delegate_to_the_retrying_path():
    transport = ScriptedTransport([_ok()])
    adapter = TelnyxAdapter(transport=transport)

    await adapter.hangup("v3:call-1")
    await adapter.transfer("v3:call-1", "+15551230000")

    assert transport.calls[0]["url"].endswith("/actions/hangup")
    assert transport.calls[1]["url"].endswith("/actions/transfer")
    for call in transport.calls:
        assert call["headers"]["Idempotency-Key"].startswith("voxdesk-telnyx-")


async def test_unknown_commands_are_refused_before_any_request():
    transport = ScriptedTransport([_ok()])
    adapter = TelnyxAdapter(transport=transport)

    with pytest.raises(ProviderValidationError):
        await adapter.call_control("self_destruct", "v3:call-1", {})

    assert transport.attempts == 0


async def test_a_missing_key_refuses_every_command(monkeypatch):
    monkeypatch.setattr(settings, "telnyx_api_key", "")
    transport = ScriptedTransport([_ok()])
    adapter = TelnyxAdapter(transport=transport)

    with pytest.raises(Exception):
        await adapter.answer("v3:call-1")
    assert transport.attempts == 0


# --------------------------------------------------------------------------- #
# Rate limiting maps to a typed, retryable error
# --------------------------------------------------------------------------- #


async def test_a_rate_limited_call_control_is_typed_retryable():
    transport = ScriptedTransport([HttpResult(429, {})])
    adapter = TelnyxAdapter(transport=transport)
    settings.telnyx_call_control_max_retries = 0
    try:
        with pytest.raises(ProviderRateLimitedError):
            await adapter.answer("v3:call-1")
        assert transport.attempts == 1
    finally:
        settings.telnyx_call_control_max_retries = 2
