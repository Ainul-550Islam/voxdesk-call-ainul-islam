"""Truth & regression guards for PART 11 (real-provider governance, PSTN DSP, mock isolation, and bench/)."""

from __future__ import annotations

import ast
import json
import math
from datetime import date, timedelta
from pathlib import Path
import struct
import subprocess
import sys
import wave

import pytest
import yaml

from bench.run_bench import evaluate_tool_calls, load_all_scenarios, wilson_ci_95
from loadtest.voice_capacity_test import estimate_cost_per_concurrent_call
from scripts.generate_feature_matrix import (
    FRESHNESS_MAX_DAYS,
    junit_execution_date,
    main as matrix_main,
)
from scripts.measure_pstn_latency import analyze_dual_channel_wav

ROOT = Path(__file__).resolve().parents[2]


def test_feature_manifest_declares_evidence_tier_and_provider_evidence() -> None:
    manifest = yaml.safe_load((ROOT / "tests" / "truth" / "feature_manifest.yaml").read_text(encoding="utf-8"))
    features = manifest["features"]
    assert len(features) == 41
    for feat in features:
        assert feat.get("evidence_tier") in {
            "LIVE_PROVIDER",
            "LIVE_FAKE",
            "API_ONLY",
            "NOT_CONFIGURED",
            "PLANNED",
        }, "PART 11 R17 fixes the tier vocabulary; a private tier name makes the matrix unreadable to buyers"
        assert isinstance(feat.get("provider_evidence"), list)


def test_manifest_provider_nodes_name_real_test_functions() -> None:
    manifest = yaml.safe_load((ROOT / "tests" / "truth" / "feature_manifest.yaml").read_text(encoding="utf-8"))
    for feature in manifest["features"]:
        for requirement in feature.get("provider_evidence") or []:
            node = requirement.get("test")
            if node is None:
                continue  # Explicitly unconfigured providers have no qualifying test yet.
            parts = str(node).split("::")
            assert len(parts) >= 2 and parts[0].startswith("tests/real/"), node
            source = ROOT / parts[0]
            assert source.is_file(), node
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            function_names = {
                item.name
                for item in ast.walk(tree)
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            test_name = parts[-1].split("[", 1)[0]
            assert test_name in function_names, f"provider evidence points at no real test: {node}"


def test_generate_feature_matrix_check_and_seven_day_freshness_rule(tmp_path: Path) -> None:
    # The committed docs must be the render of the committed evidence, and that has to be
    # checkable on any day: `--reference-date evidence` pins the render to the date inside
    # `evidence/junit/feature_evidence.xml`. The previous version of this guard hardcoded
    # `2026-11-07`, so it passed exactly once and a buyer's fresh export could never pass it.
    assert matrix_main(["--check", "--reference-date", "evidence"]) == 0

    evidence_date = junit_execution_date(ROOT / "evidence" / "junit" / "feature_evidence.xml")
    assert evidence_date, "the guard needs committed JUnit evidence to age against"
    run_day = date.fromisoformat(evidence_date)

    # One day past the window the same tree must fail: the rule is enforced, not narrated.
    stale = (run_day + timedelta(days=FRESHNESS_MAX_DAYS + 1)).isoformat()
    assert matrix_main(["--check", "--reference-date", stale]) == 1
    # Evidence dated after the reference date is a time-travelling claim, not fresh proof.
    future = (run_day - timedelta(days=1)).isoformat()
    assert matrix_main(["--check", "--reference-date", future]) == 1

    # Drift in output file must also fail --check non-zero
    drifted = tmp_path / "FEATURE_MATRIX_VERIFIED.md"
    drifted.write_text("# drifted\n", encoding="utf-8")
    assert matrix_main(["--check", "--reference-date", "evidence", "--output", str(drifted)]) == 1


def test_evidence_status_and_real_integrations_ledger_exist() -> None:
    """The nightly ledger is only assertable once real provider keys exist.

    Without keys no ledger can honestly exist, so the ledger half of this test skips
    with the reason on record instead of being satisfied by a fabricated file. The
    published tier column (`docs/SALES/FEATURE_MATRIX_VERIFIED.md`) is what shows the
    gap in the meantime: no feature is granted `LIVE_PROVIDER`.
    """
    status_md = (ROOT / "docs" / "EVIDENCE_STATUS.md").read_text(encoding="utf-8")
    assert "provider_fakes" in status_md
    assert "real_integrations.json" in status_md

    ledgers = sorted((ROOT / "evidence" / "real-integrations").glob("*/real_integrations.json"))
    if not ledgers:
        pytest.skip(
            "no evidence/real-integrations/<date>/real_integrations.json: PART 11 G11 needs owner "
            "carrier + provider keys (see reports/PART_11_REPORT.md, section 'Blocked')"
        )
    doc = json.loads(ledgers[-1].read_text(encoding="utf-8"))
    assert doc["provider_fakes"] is False
    assert set(doc["providers"]).issuperset(
        {"twilio", "telnyx", "deepgram", "elevenlabs", "cartesia", "openai", "anthropic", "salesforce", "hubspot"}
    )
    passed = [name for name, entry in doc["providers"].items() if str(entry.get("status")) == "PASS"]
    if not passed:
        # The ledger exists only because a key-less run recorded its own skips. That is a
        # useful artefact but it is not the G11 evidence the task asks for.
        pytest.skip(
            "real_integrations.json exists but no provider PASSed (all SKIPPED_NO_CREDENTIALS): "
            "PART 11 G11 still needs owner carrier + provider keys"
        )

    # Structured PART 11 evidence must be committable (not gitignored) while binary
    # .wav demo recordings stay ignored.
    probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, capture_output=True, text=True)
    if probe.returncode == 0:
        for required_rel in (
            str(ledgers[-1].relative_to(ROOT)),
            "evidence/demo/SHA256SUMS",
            "evidence/capacity/voice_capacity_ramp.json",
            "evidence/mock/benchmark_200_calls.json",
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

    studies = sorted((ROOT / "evidence" / "latency").glob("*/real_study.json"))
    if studies:
        for study in studies:
            assert json.loads(study.read_text(encoding="utf-8"))["provider_fakes"] is False
    else:
        # Keep the mock numbers out of the real benchmark doc, which is the point this
        # half of the test can still prove without provider keys.
        assert "provider_fakes" not in real_bench.lower() or "mock" in real_bench.lower()

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


# --- P11-01: the tier is earned from artifacts, never asserted in the manifest -----


def _feature(declared: str = "LIVE_PROVIDER") -> dict:
    return {
        "id": 1,
        "name": "End-to-end latency",
        "status": "LIVE",
        "evidence_tier": declared,
        "provider_evidence": [
            {
                "provider": "deepgram",
                "test": "tests/real/test_stt_tts_llm_stream.py::test_deepgram_streaming_stt_ttfb",
            },
            {
                "provider": "openai",
                "test": "tests/real/test_stt_tts_llm_stream.py::test_openai_streaming_llm_ttft",
            },
        ],
        "evidence_tests": ["tests/agent/test_latency_observer.py::test_latency_observer_computes_percentiles_and_metrics"],
    }


def test_live_provider_tier_is_granted_only_from_a_fresh_real_ledger() -> None:
    from datetime import date

    from scripts.generate_feature_matrix import grant_tier

    ref = date(2026, 11, 9)
    passing = {
        "run_date": "2026-11-08",
        "provider_fakes": False,
        "providers": {
            "deepgram": {
                "status": "PASS",
                "provider_fakes": False,
                "test_node": "tests/real/test_stt_tts_llm_stream.py::test_deepgram_streaming_stt_ttfb",
            },
            "openai": {
                "status": "PASS",
                "provider_fakes": False,
                "test_node": "tests/real/test_stt_tts_llm_stream.py::test_openai_streaming_llm_ttft",
            },
        },
    }

    assert grant_tier(_feature(), passing, reference_date=ref) == "LIVE_PROVIDER"
    # no ledger at all -> the code path is still proven, the provider is not
    assert grant_tier(_feature(), None, reference_date=ref) == "LIVE_FAKE"
    # older than the 7 day window
    stale = dict(passing, run_date="2026-10-08")
    assert grant_tier(_feature(), stale, reference_date=ref) == "LIVE_FAKE"
    # one provider failed / was skipped for credentials
    for bad in ("FAIL", "SKIPPED_NO_CREDENTIALS", "MISSING"):
        doc = {
            "run_date": "2026-11-08",
            "provider_fakes": False,
            "providers": {
                "deepgram": passing["providers"]["deepgram"],
                "openai": {**passing["providers"]["openai"], "status": bad},
            },
        }
        assert grant_tier(_feature(), doc, reference_date=ref) == "LIVE_FAKE", bad
    # a mocked ledger can never upgrade a row, whichever way it is supplied
    fakes = dict(passing, provider_fakes=True)
    assert grant_tier(_feature(), fakes, reference_date=ref) == "LIVE_FAKE"
    # the tier is capped by the row's own test status
    assert grant_tier(_feature(), passing, reference_date=ref, test_status="API_ONLY") == "API_ONLY"


def test_provider_pass_from_another_test_node_does_not_upgrade_feature() -> None:
    from datetime import date

    from scripts.generate_feature_matrix import evaluate_provider_cell, grant_tier

    reference = date(2026, 11, 9)
    unrelated = {
        "run_date": "2026-11-08",
        "provider_fakes": False,
        "providers": {
            "deepgram": {
                "status": "PASS",
                "provider_fakes": False,
                "test_node": "tests/real/test_stt_tts_llm_stream.py::test_deepgram_streaming_stt_ttfb",
            },
            "openai": {
                "status": "PASS",
                "provider_fakes": False,
                "test_node": "tests/real/test_s2s_live.py::test_openai_realtime_s2s_audio_roundtrip_and_latency",
            },
        },
    }
    assert grant_tier(_feature(), unrelated, reference_date=reference, test_status="LIVE") == "LIVE_FAKE"
    assert evaluate_provider_cell(_feature(), unrelated, reference_date=reference) == (
        "UNVERIFIED_REAL (2026-11-08)"
    )


def test_telnyx_call_control_pass_does_not_satisfy_media_audio_evidence() -> None:
    from datetime import date

    from scripts.generate_feature_matrix import evaluate_provider_cell, grant_tier

    media_test_node = "tests/real/test_telnyx_media_websocket.py::test_authenticated_pipeline_audio"
    feature = {
        "id": 19,
        "name": "Multi-carrier live media",
        "status": "LIVE",
        "evidence_tier": "LIVE_PROVIDER",
        "provider_evidence": [
            {
                "provider": "telnyx",
                "capability": "telnyx_bidirectional_media_audio",
                "test": media_test_node,
            }
        ],
        "evidence_tests": ["tests/telephony/test_telnyx_media_stream.py::test_parse_start_frame_reads_the_media_format"],
    }
    reference = date(2026, 11, 8)
    call_control_only = {
        "run_date": "2026-11-08",
        "provider_fakes": False,
        "providers": {
            "telnyx": {
                "status": "PASS",
                "provider_fakes": False,
                "test_node": "tests/real/test_telnyx_live.py::test_telnyx_call_control_create_lookup_hangup_request",
                "details": {
                    "evidence_scope": "call_control_create_lookup_hangup_request",
                    "media_stream_requested": False,
                    "media_websocket_observed": False,
                },
            }
        },
    }
    assert grant_tier(feature, call_control_only, reference_date=reference, test_status="LIVE") == "LIVE_FAKE"
    assert evaluate_provider_cell(feature, call_control_only, reference_date=reference) == (
        "UNVERIFIED_REAL (2026-11-08)"
    )

    # Only a witnessed authenticated socket, non-silent audio in both directions,
    # proof that inbound audio reached this call's pipeline, and verified cleanup qualify.
    qualifying_details = {
        "evidence_scope": "telnyx_bidirectional_media_audio",
        "media_websocket_observed": True,
        "media_token_verified": True,
        "call_bound": True,
        "inbound_media_frames": 2,
        "inbound_non_silent_frames": 1,
        "pipeline_inbound_audio_frames": 2,
        "outbound_media_frames": 2,
        "outbound_non_silent_frames": 1,
        "cleanup_confirmed": True,
    }
    qualifying = {
        **call_control_only,
        "providers": {
            "telnyx": {
                "status": "PASS",
                "provider_fakes": False,
                "test_node": media_test_node,
                "details": qualifying_details,
            }
        },
    }
    assert grant_tier(feature, qualifying, reference_date=reference, test_status="LIVE") == "LIVE_PROVIDER"
    assert evaluate_provider_cell(feature, qualifying, reference_date=reference) == (
        "VERIFIED_REAL (2026-11-08: telnyx)"
    )


def test_telnyx_live_media_manifest_requirement_is_outstanding() -> None:
    manifest = yaml.safe_load((ROOT / "tests" / "truth" / "feature_manifest.yaml").read_text(encoding="utf-8"))
    feature = next(row for row in manifest["features"] if row["id"] == 19)
    telnyx = next(item for item in feature["provider_evidence"] if item["provider"] == "telnyx")
    assert feature["status"] == "NOT_CONFIGURED"
    assert telnyx["capability"] == "telnyx_bidirectional_media_audio"
    assert telnyx["test"] is None


def test_webhook_receiver_evidence_requires_valid_and_invalid_signature_observations() -> None:
    from datetime import date

    from scripts.generate_feature_matrix import (
        evaluate_provider_cell,
        grant_tier,
        provider_requirement_satisfied,
    )

    manifest = yaml.safe_load((ROOT / "tests" / "truth" / "feature_manifest.yaml").read_text(encoding="utf-8"))
    features = {row["id"]: row for row in manifest["features"]}
    webhook = features[25]
    requirement = webhook["provider_evidence"][0]
    billing = features[40]
    assert requirement["capability"] == "outbound_webhook_signature_verification"
    assert billing["evidence_tier"] == "LIVE_FAKE"
    assert billing["provider_evidence"] == []

    details = {
        "evidence_scope": "outbound_webhook_signature_verification",
        "remote_signature_verified": True,
        "remote_invalid_signature_rejected": True,
        "invalid_signature_http_status": 401,
        "echoed_signature_verified": True,
        "exact_body_verified": True,
    }
    passing = {
        "run_date": "2026-11-08",
        "provider_fakes": False,
        "providers": {
            "webhook_receiver": {
                "status": "PASS",
                "provider_fakes": False,
                "test_node": requirement["test"],
                "details": details,
            }
        },
    }
    reference = date(2026, 11, 8)
    assert provider_requirement_satisfied(requirement, passing["providers"])
    assert grant_tier(webhook, passing, reference_date=reference, test_status="LIVE") == "LIVE_PROVIDER"
    assert evaluate_provider_cell(webhook, passing, reference_date=reference) == (
        "VERIFIED_REAL (2026-11-08: webhook_receiver)"
    )

    # A positive-only 2xx/echo, an accepted invalid signature, or a different test
    # node may not be promoted as remote verification evidence.
    for invalid_details in (
        {**details, "remote_invalid_signature_rejected": False},
        {**details, "invalid_signature_http_status": 200},
        {**details, "exact_body_verified": False},
    ):
        document = {
            **passing,
            "providers": {
                "webhook_receiver": {**passing["providers"]["webhook_receiver"], "details": invalid_details}
            },
        }
        assert grant_tier(webhook, document, reference_date=reference, test_status="LIVE") == "LIVE_FAKE"
        assert evaluate_provider_cell(webhook, document, reference_date=reference) == (
            "UNVERIFIED_REAL (2026-11-08)"
        )

    wrong_node = {
        **passing,
        "providers": {
            "webhook_receiver": {
                **passing["providers"]["webhook_receiver"],
                "test_node": "tests/real/test_webhook_receiver.py::test_old_positive_only_receiver_check",
            }
        },
    }
    assert grant_tier(webhook, wrong_node, reference_date=reference, test_status="LIVE") == "LIVE_FAKE"

    skipped = {
        **passing,
        "providers": {
            "webhook_receiver": {
                **passing["providers"]["webhook_receiver"],
                "status": "SKIPPED_NO_CREDENTIALS",
                "test_node": "tests/real/test_webhook_receiver.py::test_old_positive_only_receiver_check",
            }
        },
    }
    assert grant_tier(webhook, skipped, reference_date=reference, test_status="LIVE") == "LIVE_FAKE"
    assert evaluate_provider_cell(webhook, skipped, reference_date=reference) == (
        "PENDING_CREDENTIALS (2026-11-08)"
    )


def test_mocked_ledger_is_rejected_by_the_loader(tmp_path: Path) -> None:
    import yaml as _yaml  # noqa: F401  (keeps the import list stable for readers)

    from scripts.generate_feature_matrix import load_latest_real_evidence

    day = tmp_path / "2026-11-08"
    day.mkdir()
    (day / "real_integrations.json").write_text(json.dumps({"provider_fakes": True, "providers": {}}), encoding="utf-8")
    assert load_latest_real_evidence(tmp_path) is None, "provider_fakes:true must never be read as real evidence"

    (day / "real_integrations.json").write_text(
        json.dumps({"provider_fakes": False, "run_date": "2026-11-08", "providers": {}}), encoding="utf-8"
    )
    assert load_latest_real_evidence(tmp_path) is not None


def test_published_matrix_mentions_live_provider_only_inside_table_rows() -> None:
    """The spec's acceptance command greps this file; prose would inflate the count."""
    published = (ROOT / "docs" / "SALES" / "FEATURE_MATRIX_VERIFIED.md").read_text(encoding="utf-8")
    for line in published.splitlines():
        if "LIVE_PROVIDER" in line:
            assert line.startswith("| "), f"tier name leaked into prose: {line[:80]!r}"
            assert line.count("|") >= 7, f"not a 6-column data row: {line[:80]!r}"


def test_a_ledger_of_skipped_providers_is_never_rendered_as_fresh(tmp_path: Path) -> None:
    """A dated ledger whose every provider skipped on credentials is not evidence."""
    from datetime import date

    from scripts.generate_feature_matrix import render_evidence_status

    skipped = {
        "schema": "voxdesk.real_integrations/v1",
        "run_date": "2026-11-08",
        "provider_fakes": False,
        "providers": {
            "deepgram": {"status": "SKIPPED_NO_CREDENTIALS"},
            "openai": {"status": "SKIPPED_NO_CREDENTIALS"},
        },
        "_artifact_path": tmp_path / "2026-11-08" / "real_integrations.json",
    }
    (tmp_path / "2026-11-08").mkdir()
    skipped["_artifact_path"].write_text(
        json.dumps({k: v for k, v in skipped.items() if not k.startswith("_")}), encoding="utf-8"
    )

    doc = render_evidence_status(skipped, repo_root=tmp_path, reference_date=date(2026, 11, 9))
    assert "NO PROVIDER RAN" in doc
    assert "FRESH" not in doc, "an all-skipped ledger must not read as fresh evidence"
    assert "`NO RUN`" in doc

    passing = {k: v for k, v in skipped.items() if k != "providers"}
    passing["providers"] = {"deepgram": {"status": "PASS"}, "openai": {"status": "PASS"}}
    doc_ok = render_evidence_status(passing, repo_root=tmp_path, reference_date=date(2026, 11, 9))
    assert "FRESH" in doc_ok and "NO PROVIDER RAN" not in doc_ok


def test_the_committed_ledger_describes_its_own_run() -> None:
    ledgers = sorted((ROOT / "evidence" / "real-integrations").glob("*/real_integrations.json"))
    if not ledgers:
        pytest.skip("no ledger in this tree")
    doc = json.loads(ledgers[-1].read_text(encoding="utf-8"))
    assert doc.get("run_status") in {"ALL_PASS", "PARTIAL", "NO_PROVIDER_RAN"}, (
        "the ledger must state what the run achieved, not only what it attempted"
    )
    counts = doc.get("counts") or {}
    assert counts.get("total") == len(doc["providers"])
    assert counts.get("pass", 0) + counts.get("fail", 0) + counts.get("skipped_no_credentials", 0) + counts.get(
        "other", 0
    ) == counts.get("total")
