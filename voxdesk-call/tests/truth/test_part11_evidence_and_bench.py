"""Truth & regression guards for PART 11 (real-provider governance, PSTN DSP, mock isolation, and bench/)."""

from __future__ import annotations

import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import wave

import yaml

from bench.run_bench import evaluate_tool_calls, load_all_scenarios, wilson_ci_95
from loadtest.voice_capacity_test import estimate_cost_per_concurrent_call
from scripts.generate_feature_matrix import main as matrix_main
from scripts.measure_pstn_latency import analyze_dual_channel_wav

ROOT = Path(__file__).resolve().parents[2]


def test_feature_manifest_declares_evidence_tier_and_provider_evidence() -> None:
    manifest = yaml.safe_load((ROOT / "tests" / "truth" / "feature_manifest.yaml").read_text(encoding="utf-8"))
    features = manifest["features"]
    assert len(features) == 41
    for feat in features:
        assert feat.get("evidence_tier") in {
            "provider_gated",
            "in_process_e2e",
            "local_service_e2e",
            "compliance_attestation",
        }
        assert isinstance(feat.get("provider_evidence"), list)


def test_generate_feature_matrix_check_and_seven_day_freshness_rule(tmp_path: Path) -> None:
    # Current repo must pass --check at reference date 2026-11-07
    assert matrix_main(["--check", "--reference-date", "2026-11-07"]) == 0

    # Advancing reference date > 7 days past JUnit timestamp must fail --check non-zero
    assert matrix_main(["--check", "--reference-date", "2026-11-25"]) == 1

    # Drift in output file must also fail --check non-zero
    drifted = tmp_path / "FEATURE_MATRIX_VERIFIED.md"
    drifted.write_text("# drifted\n", encoding="utf-8")
    assert matrix_main(["--check", "--reference-date", "2026-11-07", "--output", str(drifted)]) == 1


def test_evidence_status_and_real_integrations_ledger_exist() -> None:
    status_md = (ROOT / "docs" / "EVIDENCE_STATUS.md").read_text(encoding="utf-8")
    assert "provider_fakes" in status_md
    assert "real_integrations.json" in status_md

    ledgers = sorted((ROOT / "evidence" / "real-integrations").glob("*/real_integrations.json"))
    assert ledgers, "Expected at least one dated real_integrations.json ledger"
    doc = json.loads(ledgers[-1].read_text(encoding="utf-8"))
    assert doc["provider_fakes"] is False
    assert set(doc["providers"]).issuperset(
        {"twilio", "telnyx", "deepgram", "elevenlabs", "cartesia", "openai", "anthropic", "salesforce", "hubspot"}
    )

    # Verify structured PART 11 evidence ledgers are committable (not gitignored),
    # while binary .wav demo recordings remain gitignored.
    probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, capture_output=True, text=True)
    if probe.returncode == 0:
        for required_rel in (
            "evidence/real-integrations/2026-11-07/real_integrations.json",
            "evidence/real-integrations/2026-11-07/junit.xml",
            "evidence/latency/2026-11-07/real_study.json",
            "evidence/latency/2026-11-07/real_study.csv",
            "evidence/demo/SHA256SUMS",
            "evidence/capacity/voice_capacity_denoise_on.json",
            "evidence/capacity/voice_capacity_denoise_off.json",
        ):
            chk = subprocess.run(["git", "check-ignore", "-q", required_rel], cwd=ROOT)
            assert chk.returncode == 1, f"{required_rel} must not be gitignored"
        wav_chk = subprocess.run(["git", "check-ignore", "-q", "evidence/demo/01_inbound_call_audio.wav"], cwd=ROOT)
        assert wav_chk.returncode == 0, "binary .wav demo audio must stay gitignored"


def test_measure_pstn_latency_dual_channel_caller_leg_onset(tmp_path: Path) -> None:
    sr = 8000
    duration_s = 2.0
    total_frames = int(sr * duration_s)
    # Caller speaks on ch0 from 100ms..500ms; Agent replies on ch1 starting at 920ms -> latency ~420ms
    frames = bytearray()
    for i in range(total_frames):
        t_ms = (i / sr) * 1000.0
        ch0 = int(12000 * math.sin(2.0 * math.pi * 440.0 * (i / sr))) if 100.0 <= t_ms < 500.0 else 0
        ch1 = int(12000 * math.sin(2.0 * math.pi * 520.0 * (i / sr))) if 920.0 <= t_ms < 1450.0 else 0
        frames.extend(struct.pack("<hh", ch0, ch1))

    wav_file = tmp_path / "dual_call.wav"
    with wave.open(str(wav_file), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(frames))

    report = analyze_dual_channel_wav(
        wav_file,
        provider="twilio",
        model="gpt-4o-mini",
        region="us-east-1",
        codec="PCMU/8000",
    )
    assert report["provider_fakes"] is False
    assert report["measurement_leg"] == "caller"
    assert report["provider"] == "twilio"
    assert report["model"] == "gpt-4o-mini"
    assert report["region"] == "us-east-1"
    assert report["codec"] == "PCMU/8000"
    assert report["matched_turns"] == 1
    assert 400.0 <= float(report["p50_ms"]) <= 440.0


def test_mock_latency_isolation_and_demo_release_assets() -> None:
    mock_json = ROOT / "evidence" / "mock" / "benchmark_200_calls.json"
    assert mock_json.is_file()
    mock_doc = json.loads(mock_json.read_text(encoding="utf-8"))
    assert mock_doc["provider_fakes"] is True
    assert mock_doc["not_product_performance"] is True
    assert not (ROOT / "evidence" / "latency" / "2026-10-08").exists()

    # Only docs/LATENCY_BENCHMARK_MOCK.md inside docs/ may reference `evidence/mock/`
    for md_path in (ROOT / "docs").rglob("*.md"):
        content = md_path.read_text(encoding="utf-8")
        if "evidence/mock" in content:
            assert md_path.name == "LATENCY_BENCHMARK_MOCK.md", (
                f"Unexpected reference to evidence/mock in {md_path.relative_to(ROOT)}"
            )

    real_bench = (ROOT / "docs" / "LATENCY_BENCHMARK.md").read_text(encoding="utf-8")
    assert "285.3" not in real_bench
    assert "318.7" not in real_bench

    real_study = json.loads(
        (ROOT / "evidence" / "latency" / "2026-11-07" / "real_study.json").read_text(encoding="utf-8")
    )
    assert real_study["provider_fakes"] is False

    # Demo folder must track manifest + SHA256SUMS + 8 JSON transcripts and zero binary .wav files
    demo_dir = ROOT / "evidence" / "demo"
    assert list(demo_dir.glob("*.wav")) == []
    assert (demo_dir / "SHA256SUMS").is_file()
    manifest = json.loads((demo_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["total_calls"] == 8
    for item in manifest["calls"]:
        trace = json.loads((ROOT / item["trace_and_transcript_file"]).read_text(encoding="utf-8"))
        assert len(trace.get("transcript") or []) >= 3
        assert "audio_release_asset" in trace


def test_workflow_benchmark_100_scenarios_and_results_schema() -> None:
    scenarios = load_all_scenarios(ROOT / "bench" / "scenarios")
    assert len(scenarios) == 100
    verticals = {s["vertical"] for s in scenarios}
    assert verticals == {"healthcare_scheduling", "ecommerce_support", "financial_services"}
    for s in scenarios:
        assert s.get("persona")
        assert s.get("goal")
        assert isinstance(s.get("expected_tool_calls"), list) and len(s["expected_tool_calls"]) >= 1
        assert isinstance(s.get("pass_criteria"), list) and len(s["pass_criteria"]) >= 2

    low, high = wilson_ci_95(85, 100)
    assert 75.0 < low < 85.0 < high < 92.0
    eval_ok = evaluate_tool_calls(
        [{"name": "lookup_order", "required_args": {"order_id": "ORD-0001"}}],
        [{"name": "lookup_order", "args": {"order_id": "ORD-0001"}}],
    )
    assert eval_ok["passed"] is True

    res_doc = json.loads((ROOT / "bench" / "results" / "2026-11-07.json").read_text(encoding="utf-8"))
    assert res_doc["self_reported"] is True
    assert res_doc["summary"]["total_scenarios"] == 100
    assert res_doc["summary"]["vertical_count"] == 3
    assert len(res_doc["summary"]["wilson_95_ci_pct"]) == 2


def test_capacity_real_providers_gate_and_cost_estimator() -> None:
    cost_on = estimate_cost_per_concurrent_call(denoise_enabled=True)
    cost_off = estimate_cost_per_concurrent_call(denoise_enabled=False)
    assert cost_on["safe_concurrent_calls_per_2vcpu_worker"] == 20
    assert cost_off["safe_concurrent_calls_per_2vcpu_worker"] == 30
    assert cost_on["compute_cost_per_concurrent_call_hour_usd"] > cost_off["compute_cost_per_concurrent_call_hour_usd"]

    proc = subprocess.run(
        [sys.executable, str(ROOT / "loadtest" / "voice_capacity_test.py"), "--steps", "10", "--real-providers"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert proc.returncode == 2
    assert "DEEPGRAM_API_KEY" in proc.stderr
