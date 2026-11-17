"""Truth test: every evidence artifact states, machine-readably, whether it is real.

PART 11's acceptance command is::

    jq -r '.provider_fakes' evidence/latency/*/real_study.json evidence/demo/*.json

That only means something if the field exists **at the top level** of every file. Today
one trace kept it nested inside ``execution_result`` and seven had no flag at all, so the
command would print ``null`` and any reviewer would read silence as "real". This test
turns the acceptance command into a gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "evidence" / "demo"
LATENCY = ROOT / "evidence" / "latency"
MOCK = ROOT / "evidence" / "mock"

#: Tiers allowed to appear on an evidence artifact (spec R17).
TIERS = {"LIVE_PROVIDER", "LIVE_FAKE", "API_ONLY", "NOT_CONFIGURED", "PLANNED"}

#: A real carrier/provider call leaves at least one of these behind.
REAL_CALL_MARKERS = ("call_sid", "provider_call_id", "call_id", "twilio_call_sid")


def _json_files(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.json")) if directory.is_dir() else []


@pytest.mark.parametrize("path", _json_files(DEMO), ids=lambda p: p.name)
def test_every_demo_trace_states_its_provenance(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))

    assert "provider_fakes" in doc, (
        f"{path.name} has no top-level `provider_fakes`; nested flags are invisible to "
        "`jq -r .provider_fakes` and to every reviewer reading the file"
    )
    assert isinstance(doc["provider_fakes"], bool), f"{path.name}: provider_fakes must be a JSON boolean"

    if path.name == "manifest.json":
        assert doc["provider_fakes"] is True or doc.get("recording_status") == "real_calls_captured"
        return

    assert doc.get("evidence_tier") in TIERS, f"{path.name}: evidence_tier {doc.get('evidence_tier')!r} not in {sorted(TIERS)}"
    assert str(doc.get("provenance") or "").strip(), f"{path.name} must explain how it was produced"


@pytest.mark.parametrize("path", _json_files(DEMO), ids=lambda p: p.name)
def test_a_trace_claiming_real_calls_carries_a_call_identifier(path: Path) -> None:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("provider_fakes") is not False:
        return
    blob = json.dumps(doc)
    assert any(marker in blob for marker in REAL_CALL_MARKERS), (
        f"{path.name} claims provider_fakes: false but carries no call identifier "
        f"({', '.join(REAL_CALL_MARKERS)}); a real carrier call always has one"
    )


def test_the_demo_manifest_matches_the_traces_it_indexes() -> None:
    manifest = json.loads((DEMO / "manifest.json").read_text(encoding="utf-8"))
    traces = [p for p in _json_files(DEMO) if p.name != "manifest.json"]
    assert manifest.get("total_calls") == len(traces), (
        f"manifest says {manifest.get('total_calls')} calls, {len(traces)} traces exist"
    )
    flags = {json.loads(p.read_text(encoding="utf-8")).get("provider_fakes") for p in traces}
    if flags == {True}:
        assert manifest.get("provider_fakes") is True, "all traces are synthetic, the manifest must say so"
        assert manifest.get("not_product_performance") is True
    if flags == {False}:
        assert manifest.get("provider_fakes") is False


def test_the_buyer_docs_do_not_call_synthetic_traces_real_recordings() -> None:
    """A trace set that is synthetic must not be described as captured real audio."""
    traces = [p for p in _json_files(DEMO) if p.name != "manifest.json"]
    if not traces:
        pytest.skip("no demo traces in this tree")
    synthetic = all(json.loads(p.read_text(encoding="utf-8")).get("provider_fakes") for p in traces)
    if not synthetic:
        return
    script = (ROOT / "docs" / "DEMO" / "SCRIPT.md").read_text(encoding="utf-8")
    lowered = script.lower()
    for claim in ("8 required real recordings", "eight real recordings captured"):
        assert claim not in lowered, f"docs/DEMO/SCRIPT.md still claims {claim!r} while the traces are synthetic"
    assert "synthetic" in lowered, "the demo script must disclose that the tracked traces are synthetic"


def test_recorded_latency_studies_declare_provider_fakes() -> None:
    studies = sorted(LATENCY.glob("*/real_study.json")) if LATENCY.is_dir() else []
    for path in studies:
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc.get("provider_fakes") is False, (
            f"{path.relative_to(ROOT)}: a file named real_study.json must be a real run"
        )
        assert (path.parent / "real_study.csv").is_file(), f"{path.parent.name}: CSV companion missing"
        assert doc.get("run_date") or doc.get("date"), "the study must date itself"


def test_mock_benchmarks_are_labelled_as_mock() -> None:
    files = _json_files(MOCK)
    if not files:
        pytest.skip("no mock benchmarks in this tree")
    for path in files:
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc.get("provider_fakes") is True, f"{path.name}: mock data must carry provider_fakes: true"
        assert doc.get("not_product_performance") is True, f"{path.name}: mock data must refuse to be read as product performance"
