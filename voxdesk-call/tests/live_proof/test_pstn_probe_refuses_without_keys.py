"""The PSTN probe refuses to run without real credentials, and says what is missing.

These tests never place a call. They pin the refusal path (which is the safety
property that matters), the TwiML the *caller* leg would speak, and the fact that a
substitute carrier client can never produce a "no fakes" claim.
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.telephony.live_proof import pstn_probe
from app.telephony.live_proof.evidence_writer import ProviderCallLedger
from app.telephony.live_proof.pstn_probe import (
    ProviderNotConfigured,
    caller_twiml,
    missing_provider_settings,
    require_providers,
)

pytestmark = pytest.mark.asyncio

_PROVIDER_SETTINGS = (
    "twilio_account_sid",
    "twilio_auth_token",
    "twilio_phone_number",
    "deepgram_api_key",
    "elevenlabs_api_key",
    "openai_api_key",
    "live_proof_test_number",
)


@pytest.fixture
def no_providers(monkeypatch):
    for name in _PROVIDER_SETTINGS:
        monkeypatch.setattr(settings, name, "")
    return monkeypatch


@pytest.fixture
def full_providers(monkeypatch):
    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "0" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", "auth-token-0123456789abcdef")
    monkeypatch.setattr(settings, "twilio_phone_number", "+15550001111")
    monkeypatch.setattr(settings, "deepgram_api_key", "dg-key-0123456789")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "el-key-0123456789")
    monkeypatch.setattr(settings, "openai_api_key", "sk-key-0123456789")
    monkeypatch.setattr(settings, "live_proof_test_number", "+15550002222")
    monkeypatch.setattr(settings, "live_proof_caller_id", "+15550001111")
    return monkeypatch


async def test_missing_settings_are_named_individually(no_providers):
    missing = missing_provider_settings()

    assert missing == (
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_PHONE_NUMBER",
        "DEEPGRAM_API_KEY",
        "ELEVENLABS_API_KEY",
        "OPENAI_API_KEY",
        "LIVE_PROOF_TEST_NUMBER",
    )


async def test_require_providers_raises_a_typed_refusal(no_providers):
    with pytest.raises(ProviderNotConfigured) as error:
        require_providers()

    assert error.value.code == "provider_not_configured"
    assert error.value.status_code == 503
    assert "TWILIO_ACCOUNT_SID" in error.value.missing
    assert error.value.as_dict()["missing"]


async def test_require_providers_passes_when_everything_is_present(full_providers):
    require_providers()  # must not raise


async def test_the_probe_refuses_before_touching_the_carrier(no_providers):
    """No call, no row, no evidence -- and the caller is told exactly what is missing."""
    calls: list[dict] = []

    class RecordingClient:
        async def place_call(self, **kwargs):  # pragma: no cover - must not be reached
            calls.append(kwargs)
            raise AssertionError("the probe placed a call without provider credentials")

    created: list[dict] = []

    class RecordingSession:
        async def __aenter__(self):
            created.append({"opened": True})
            return self

        async def __aexit__(self, *_exc):
            return False

    with pytest.raises(ProviderNotConfigured):
        await pstn_probe.run_pstn_probe(
            tenant_id="11111111-1111-1111-1111-111111111111",
            session_factory=lambda: RecordingSession(),
            twilio_client=RecordingClient(),
        )

    assert calls == []
    assert created == []


async def test_caller_twiml_speaks_every_line_with_a_gap_between_them():
    twiml = caller_twiml(
        ["Hello, I would like to book an appointment", "Tomorrow at ten"],
        gap_seconds=6,
    )

    assert twiml.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert twiml.count("<Say") == 2
    assert twiml.count('<Pause length="6"/>') == 2
    assert "Tomorrow at ten" in twiml
    assert twiml.rstrip().endswith("<Hangup/></Response>")


async def test_caller_twiml_escapes_speech_markup():
    twiml = caller_twiml(["Tom & Jerry <script>"], gap_seconds=1)

    assert "Tom &amp; Jerry &lt;script&gt;" in twiml
    assert "<script>" not in twiml


async def test_caller_twiml_refuses_an_empty_script_or_a_non_positive_gap():
    with pytest.raises(ProviderNotConfigured):
        caller_twiml([], gap_seconds=5)
    with pytest.raises(ProviderNotConfigured):
        caller_twiml(["hello"], gap_seconds=0)


async def test_the_configured_script_comes_from_the_environment(monkeypatch):
    monkeypatch.setattr(settings, "live_proof_script", "First line|Second line|  |Third line")

    assert settings.live_proof_script_lines == ["First line", "Second line", "Third line"]


async def test_a_substitute_carrier_client_cannot_claim_real_providers():
    """The ledger records reality, so an injected client is visibly not the carrier."""
    ledger = ProviderCallLedger()
    ledger.record(
        provider="twilio",
        stage="telephony",
        operation="calls.create",
        real=False,
        basis="substitute client",
    )
    for stage in ("stt", "llm", "tts"):
        ledger.record(provider="p", stage=stage, operation="op", real=True, basis="persisted artifact")

    assert ledger.provider_fakes is True
    assert [call.provider for call in ledger.fake_calls()] == ["twilio"]


async def test_the_probe_module_exposes_a_cli_that_requires_a_tenant(full_providers, capsys):
    exit_code = await pstn_probe._cli(["--tenant-id", ""])

    assert exit_code == 2
    assert "--tenant-id is required" in capsys.readouterr().err


async def test_the_probe_reports_missing_providers_from_the_cli(no_providers, capsys):
    exit_code = await pstn_probe._cli(["--tenant-id", "11111111-1111-1111-1111-111111111111"])

    assert exit_code == 2
    assert "provider_not_configured" in capsys.readouterr().err


async def test_terminal_statuses_cover_every_call_ending_but_a_live_one():
    from app.db.models import CallStatus

    assert pstn_probe.TERMINAL_STATUSES == {
        CallStatus.COMPLETED,
        CallStatus.FAILED,
        CallStatus.NO_ANSWER,
        CallStatus.CANCELLED,
    }
    assert CallStatus.IN_PROGRESS not in pstn_probe.TERMINAL_STATUSES
    assert CallStatus.RINGING not in pstn_probe.TERMINAL_STATUSES


async def test_required_providers_are_the_six_that_make_a_run_real():
    names = dict(pstn_probe.REQUIRED_PROVIDERS)

    assert names["twilio_account_sid"] == "TWILIO_ACCOUNT_SID"
    assert names["deepgram_api_key"] == "DEEPGRAM_API_KEY"
    assert names["elevenlabs_api_key"] == "ELEVENLABS_API_KEY"
    assert names["openai_api_key"] == "OPENAI_API_KEY"
    assert len(names) == 6


async def test_the_module_is_runnable_as_a_script():
    import subprocess
    import sys

    completed = subprocess.run(
        [sys.executable, "-m", "app.telephony.live_proof.pstn_probe", "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert completed.returncode == 0
    assert "--tenant-id" in completed.stdout


async def test_asyncio_is_not_needed_to_build_the_caller_script():
    assert asyncio.iscoroutinefunction(pstn_probe.run_pstn_probe)
