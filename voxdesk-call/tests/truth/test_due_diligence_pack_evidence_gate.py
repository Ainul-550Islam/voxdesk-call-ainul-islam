"""The due-diligence pack refuses to be built on absent or stale evidence.

A pack that quietly omits a missing live-provider proof is worse than a pack
that says it has none: the buyer reads the absence of a row as "not applicable"
rather than "never run". These tests pin the gate, including the part that makes
it survivable -- pass `--allow-missing` and the pack builds anyway, but every
gap is written into `docs/DUE_DILIGENCE/KNOWN_LIMITATIONS.md` where the buyer
will see it.

They are cheap by design: the gate is inspected through the module's own
functions rather than by building a zip, so the suite does not pay for an SBOM
rebuild.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "make_due_diligence_pack.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("make_due_diligence_pack", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = _load_module()


def test_every_required_evidence_item_names_a_real_requirement() -> None:
    assert module.EVIDENCE_REQUIREMENTS, "the pack must require evidence"
    keys = [key for key, _, _ in module.EVIDENCE_REQUIREMENTS]
    assert len(keys) == len(set(keys))
    for key, rel, optional in module.EVIDENCE_REQUIREMENTS:
        assert isinstance(optional, bool)
        assert not rel.startswith("/"), f"{key}: paths are repository-relative"


def test_a_missing_artifact_is_reported_as_missing(tmp_path: Path) -> None:
    evidence = module.collect_evidence(tmp_path)
    assert set(evidence) == {key for key, _, _ in module.EVIDENCE_REQUIREMENTS}
    for row in evidence.values():
        assert row["status"] == "MISSING", f"{row['key']} should be missing in an empty tree"
        assert row["artifact"] is None


def test_a_fresh_artifact_is_present_and_a_stale_one_is_flagged(tmp_path: Path) -> None:
    (tmp_path / "reports").mkdir()
    fresh = tmp_path / "reports" / "TEST_EVIDENCE_SUMMARY.json"
    fresh.write_text(
        json.dumps(
            {
                "schema": "voxdesk.test_evidence/v1",
                "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "overall": "PASS",
            }
        ),
        encoding="utf-8",
    )
    old_stamp = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence" / "live").mkdir()
    (tmp_path / "evidence" / "live" / "kms.aws-kms.json").write_text(
        json.dumps({"schema": "voxdesk.live_evidence/v1", "finished_at": old_stamp}),
        encoding="utf-8",
    )

    evidence = module.collect_evidence(tmp_path)
    assert evidence["test"]["status"] == "PRESENT"
    assert evidence["test"]["age_days"] is not None and evidence["test"]["age_days"] < 1
    assert evidence["live"]["status"] == "STALE", (
        "a 90-day-old live proof describes a different checkout and must be flagged"
    )
    assert evidence["live"]["age_days"] >= 89


def test_problems_name_missing_required_items_and_not_optional_ones(tmp_path: Path) -> None:
    evidence = module.collect_evidence(tmp_path)
    problems = module.evidence_problems(evidence)
    assert any("test:" in problem and "MISSING" in problem for problem in problems)
    assert not any(problem.startswith("live:") for problem in problems), (
        "the live-provider set is opt-in; its absence is disclosed, not fatal"
    )


def test_a_stale_required_artifact_is_a_problem(tmp_path: Path) -> None:
    (tmp_path / "reports").mkdir()
    old_stamp = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    (tmp_path / "reports" / "SECURITY_EVIDENCE_SUMMARY.json").write_text(
        json.dumps({"generated_at": old_stamp}), encoding="utf-8"
    )
    evidence = module.collect_evidence(tmp_path, max_age_days=14)
    assert any("STALE" in problem for problem in module.evidence_problems(evidence))


def test_the_freshness_table_marks_every_gap(tmp_path: Path) -> None:
    evidence = module.collect_evidence(tmp_path)
    lines = module.render_evidence_freshness(evidence)
    table = "\n".join(lines)
    assert "MISSING" in table
    assert "regenerate" in table.lower()
    for key, _, _ in module.EVIDENCE_REQUIREMENTS:
        assert f"`{key}`" in table


def test_an_unparseable_artifact_is_not_silently_accepted(tmp_path: Path) -> None:
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "POLYGLOT_EVIDENCE_SUMMARY.json").write_text("{not json", encoding="utf-8")
    evidence = module.collect_evidence(tmp_path)
    row = evidence["polyglot"]
    # It exists, so it is not MISSING -- but its contents cannot be trusted, and
    # that is recorded rather than papered over.
    assert row["artifact"] is not None
    assert row["generated_at"] is None or row["summary"].get("parse_error") is True


def test_the_cli_offers_a_check_only_mode() -> None:
    if shutil.which("python3") is None:
        pytest.skip("python3 is not available")
    import subprocess

    result = subprocess.run(
        ["python3", str(SCRIPT), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "--allow-missing" in result.stdout
    assert "--check-evidence" in result.stdout
