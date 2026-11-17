"""The real PSTN tooling must stay opt-in and route calls through VoxDesk."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts import measure_pstn_latency, run_real_call_study
from scripts.real_pstn_support import (
    LIVE_PSTN_REQUIRED_ENV,
    create_voxdesk_outbound_call,
    missing_live_pstn_env,
    wait_for_voxdesk_transcript,
)


class _Response:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.mark.asyncio
async def test_call_creation_uses_the_authenticated_voxdesk_outbound_api() -> None:
    seen: dict[str, Any] = {}

    class Client:
        async def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> _Response:
            seen.update(url=url, headers=headers, body=json)
            return _Response(
                201,
                {"id": "call-row-1", "call_sid": "CA" + "a" * 32, "status": "ringing"},
            )

    call = await create_voxdesk_outbound_call(
        Client(),  # type: ignore[arg-type]
        public_base_url="https://voxdesk.example.test/",
        api_key="test-api-key",
        to_number="+14155552671",
        from_number="+14155550100",
        idempotency_key="real-pstn-test-0001",
    )

    assert seen["url"] == "https://voxdesk.example.test/api/calls"
    assert seen["headers"]["Authorization"] == "Bearer test-api-key"
    assert seen["headers"]["Idempotency-Key"] == "real-pstn-test-0001"
    assert seen["body"]["provider"] == "twilio"
    assert seen["body"]["record"] is True
    assert seen["body"]["recording_channels"] == "dual"
    assert call["id"] == "call-row-1"
    assert call["call_sid"].startswith("CA")


@pytest.mark.asyncio
async def test_transcript_helper_requires_both_real_call_speakers() -> None:
    class Client:
        async def get(self, url: str, *, headers: dict[str, str]) -> _Response:
            assert url.endswith("/api/calls/call-row-1/replay")
            assert headers["Authorization"] == "Bearer test-api-key"
            return _Response(
                200,
                {
                    "transcript": "user: hi\nassistant: hello",
                    "timeline": [
                        {"event": "transcript_turn", "speaker": "user"},
                        {"event": "transcript_turn", "speaker": "assistant"},
                    ],
                },
            )

    doc = await wait_for_voxdesk_transcript(
        Client(),  # type: ignore[arg-type]
        public_base_url="https://voxdesk.example.test",
        api_key="test-api-key",
        call_id="call-row-1",
        timeout_seconds=0.1,
    )
    assert "assistant" in doc["transcript"]


def test_live_pstn_gate_requires_explicit_opt_in_and_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in LIVE_PSTN_REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    assert "VOXDESK_REAL_INTEGRATION=1" in missing_live_pstn_env()
    assert "VOXDESK_REAL_API_KEY" in missing_live_pstn_env()

    monkeypatch.setenv("VOXDESK_REAL_INTEGRATION", "0")
    assert "VOXDESK_REAL_INTEGRATION=1" in missing_live_pstn_env()


def test_live_pstn_opt_in_is_trimmed_and_case_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in LIVE_PSTN_REQUIRED_ENV:
        monkeypatch.setenv(name, "placeholder")
    monkeypatch.setenv("VOXDESK_REAL_INTEGRATION", "  YeS  ")
    assert missing_live_pstn_env() == []


def test_latency_live_mode_refuses_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in (*LIVE_PSTN_REQUIRED_ENV, "DEEPGRAM_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert measure_pstn_latency.main(["--live"]) == 2
    assert "explicit opt-in" in capsys.readouterr().err


def test_cli_refuses_without_credentials_before_starting_live_study(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in run_real_call_study.REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        run_real_call_study,
        "_execute_real_study",
        lambda **_: pytest.fail("live runner must not be entered without credentials"),
    )

    out_dir = tmp_path / "study"
    assert run_real_call_study.main(["--output-dir", str(out_dir)]) == 2
    assert "REFUSING" in capsys.readouterr().err
    assert not out_dir.exists()


def test_cli_rejects_zero_calls_before_preflight() -> None:
    with pytest.raises(SystemExit) as caught:
        run_real_call_study.main(["--calls", "0"])
    assert caught.value.code == 2


def test_cli_reports_unexpected_execution_failure_without_a_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in run_real_call_study.REQUIRED_ENV:
        monkeypatch.setenv(name, "1" if name == "VOXDESK_REAL_INTEGRATION" else "placeholder")

    async def fail_before_dial(**_: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        raise RuntimeError("controlled preflight failure")

    monkeypatch.setattr(run_real_call_study, "_execute_real_study", fail_before_dial)
    out_dir = tmp_path / "study"
    assert run_real_call_study.main(["--output-dir", str(out_dir)]) == 2
    captured = capsys.readouterr()
    assert "REFUSED/FAILED: RuntimeError: controlled preflight failure" in captured.err
    assert "Traceback" not in captured.err
    assert not out_dir.exists()


def test_unconfigured_study_artifact_is_not_a_provider_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*LIVE_PSTN_REQUIRED_ENV, "DEEPGRAM_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    assert run_real_call_study.main(
        [
            "--output-dir",
            str(tmp_path),
            "--record-unconfigured",
            "--calls",
            "3",
            "--caller-channel",
            "1",
            "--agent-channel",
            "0",
        ]
    ) == 0
    study = json.loads((tmp_path / "real_study.json").read_text(encoding="utf-8"))
    assert study["status"] == "NOT_RUN_MISSING_CREDENTIALS"
    assert study["provider_fakes"] is False
    assert study["channel_mapping"] == {
        "caller_channel": 1,
        "agent_channel": 0,
        "verified_by_live_calibration": False,
    }
    assert study["calls_requested"] == 3
    assert study["calls_attempted"] == 0
    assert study["calls_completed"] == 0
    assert study["caller_leg_e2e_ms"]["p50_ms"] is None
    assert not list(tmp_path.glob("*.wav")), "unconfigured mode must not fabricate recordings"
