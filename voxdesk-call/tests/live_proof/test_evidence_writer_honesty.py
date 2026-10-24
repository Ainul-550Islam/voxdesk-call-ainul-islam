"""Evidence files must be computable from the run, and must refuse to lie.

The honesty rule under test: ``provider_fakes`` is derived from the ledger. There
is no argument, no keyword and no setting that can force it to ``false``; a run
where one stage has no real provider call is reported as unproven.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from app.telephony.live_proof.evidence_writer import (
    REQUIRED_STAGES,
    EvidenceIntegrityError,
    ProviderCallLedger,
    TurnMeasurement,
    assert_all_real,
    percentile,
    summarise,
    write_evidence,
)


def _ledger(*, real_stages: tuple[str, ...] = REQUIRED_STAGES, fake: bool = False) -> ProviderCallLedger:
    ledger = ProviderCallLedger()
    for stage in real_stages:
        ledger.record(
            provider={"telephony": "twilio", "stt": "deepgram", "llm": "openai", "tts": "elevenlabs"}[stage],
            stage=stage,
            operation=f"{stage}.call",
            real=not fake,
            basis="a real artifact",
        )
    return ledger


def _turns() -> list[TurnMeasurement]:
    return [
        TurnMeasurement(1, "caller", 410.0, 24, "2026-10-24T10:00:00+00:00"),
        TurnMeasurement(2, "agent", 780.5, 61, "2026-10-24T10:00:03+00:00"),
    ]


def test_a_fully_proven_ledger_is_not_fakes():
    ledger = _ledger()

    assert ledger.provider_fakes is False
    assert ledger.missing_real_stages() == ()
    assert "every recorded provider call was real" in ledger.honesty_note()
    assert_all_real(ledger)


def test_one_unproven_stage_makes_the_whole_run_unproven():
    ledger = _ledger(real_stages=("telephony", "stt", "llm"))

    assert ledger.provider_fakes is True
    assert ledger.missing_real_stages() == ("tts",)
    assert "tts" in ledger.honesty_note()
    with pytest.raises(EvidenceIntegrityError):
        assert_all_real(ledger)


def test_a_recorded_fake_call_makes_the_run_unproven_even_if_all_stages_exist():
    ledger = _ledger()
    ledger.record(provider="mock", stage="llm", operation="stub", real=False, basis="canned")

    assert ledger.provider_fakes is True
    assert [call.provider for call in ledger.fake_calls()] == ["mock"]


def test_an_unproven_stage_cannot_be_hidden_by_a_second_real_one():
    ledger = _ledger(real_stages=("telephony", "stt", "llm"))
    ledger.record(provider="elevenlabs", stage="telephony", operation="tts.tagged.telephony", real=True)

    assert ledger.missing_real_stages() == ("tts",)
    assert ledger.provider_fakes is True


def test_a_failed_provider_call_does_not_prove_its_stage():
    ledger = ProviderCallLedger()
    for stage in REQUIRED_STAGES:
        ledger.record(provider="p", stage=stage, operation="call", real=True)
    ledger.record(provider="deepgram", stage="stt", operation="retry", real=True, status="failed")

    assert "stt" in ledger.real_stages()
    assert ledger.provider_fakes is False


def test_unknown_stages_are_refused():
    ledger = ProviderCallLedger()
    with pytest.raises(EvidenceIntegrityError):
        ledger.record(provider="x", stage="made_up", operation="op", real=True)
    with pytest.raises(EvidenceIntegrityError):
        ledger.record(provider="", stage="llm", operation="op", real=True)


def test_evidence_write_refuses_an_empty_ledger(tmp_path):
    with pytest.raises(EvidenceIntegrityError):
        write_evidence(
            run_id="run-1",
            tenant_id="tenant-1",
            evidence_dir=tmp_path,
            ledger=ProviderCallLedger(),
            turns=[],
            stage_metrics={},
        )
    assert list(tmp_path.rglob("*.json")) == []


def test_evidence_file_is_written_hashed_and_self_consistent(tmp_path):
    ledger = _ledger()
    bundle = write_evidence(
        run_id="run-1",
        tenant_id="tenant-1",
        evidence_dir=tmp_path,
        ledger=ledger,
        turns=_turns(),
        stage_metrics={
            "e2e": {"p50_ms": 812.0, "p95_ms": 1180.0, "p99_ms": 1210.0, "samples": 2},
            "tts": {"p50_ms": 210.0, "p95_ms": 240.0, "p99_ms": None, "samples": 2},
        },
        recording_sha256="a" * 64,
        run_meta={"call_sid": "CA123"},
        clock=datetime(2026, 10, 24, 10, 30, tzinfo=timezone.utc),
    )

    written = json.loads(open(bundle.json_path, encoding="utf-8").read())
    raw = open(bundle.json_path, "rb").read()

    assert bundle.provider_fakes is False
    assert written["run_id"] == "run-1"
    assert written["tenant_id"] == "tenant-1"
    assert written["provider_fakes"] is False
    assert written["required_stages"] == list(REQUIRED_STAGES)
    assert written["missing_real_stages"] == []
    assert written["recording_sha256"] == "a" * 64
    assert written["turns"][0]["latency_ms"] == 410.0
    assert written["stage_metrics"]["e2e"]["p95_ms"] == 1180.0
    assert written["runtime"]["python_version"]
    assert bundle.sha256 == hashlib.sha256(raw).hexdigest()
    # The hash is stored next to the run row, not inside the artifact.
    assert "evidence_sha256" not in written


def test_a_run_with_a_missing_stage_is_written_as_unproven(tmp_path):
    bundle = write_evidence(
        run_id="run-2",
        tenant_id="tenant-1",
        evidence_dir=tmp_path,
        ledger=_ledger(real_stages=("telephony", "stt")),
        turns=[],
        stage_metrics={},
        clock=datetime(2026, 10, 24, 11, 0, tzinfo=timezone.utc),
    )

    assert bundle.provider_fakes is True
    payload = json.loads(open(bundle.json_path, encoding="utf-8").read())
    assert payload["provider_fakes"] is True
    assert set(payload["missing_real_stages"]) == {"llm", "tts"}
    assert "unproven stage(s)" in payload["provider_honesty_note"]


def test_the_csv_holds_exactly_the_turn_rows(tmp_path):
    bundle = write_evidence(
        run_id="run-3",
        tenant_id="tenant-1",
        evidence_dir=tmp_path,
        ledger=_ledger(),
        turns=_turns(),
        stage_metrics={},
        clock=datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc),
    )

    lines = open(bundle.csv_path, encoding="utf-8").read().strip().splitlines()

    assert lines[0] == "turn_index,speaker,latency_ms,text_chars,created_at"
    assert lines[1].startswith("1,caller,410.000,24,")
    assert len(lines) == 3


def test_evidence_lands_in_a_date_partitioned_directory(tmp_path):
    bundle = write_evidence(
        run_id="run-4",
        tenant_id="tenant-1",
        evidence_dir=tmp_path,
        ledger=_ledger(),
        turns=[],
        stage_metrics={},
        clock=datetime(2026, 10, 24, 23, 59, tzinfo=timezone.utc),
    )

    assert bundle.json_path.endswith("2026-10-24/run-4.json")
    assert bundle.csv_path.endswith("2026-10-24/run-4.csv")


def test_percentile_is_interpolated_and_none_for_no_data():
    assert percentile([], 50) is None
    assert percentile([10.0], 95) == 10.0
    assert percentile([10.0, 20.0], 50) == 15.0
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 95) == pytest.approx(4.8)


def test_summarise_reports_sample_count_and_nulls_when_empty():
    empty = summarise([])
    assert empty == {"samples": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None, "max_ms": None}

    filled = summarise([100.0, 200.0, 300.0])
    assert filled["samples"] == 3
    assert filled["p50_ms"] == 200.0
    assert filled["max_ms"] == 300.0


def test_ledger_serialises_every_call_for_the_artifact():
    ledger = _ledger()
    rows = ledger.as_list()

    assert len(rows) == 4
    assert {row["stage"] for row in rows} == set(REQUIRED_STAGES)
    assert all(row["real"] is True for row in rows)
    assert all(row["basis"] for row in rows)
    assert ledger.providers() == ["deepgram", "elevenlabs", "openai", "twilio"]
