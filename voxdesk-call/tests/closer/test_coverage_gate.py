"""Synthetic-report tests for scripts/closer/coverage_gate.py.

The gate must fail on a total below the floor, on an untested app module that is
not in the baseline, and when the floor is missing. It must pass when both hold.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[2] / "scripts" / "closer" / "coverage_gate.py"


@pytest.fixture()
def gate(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("coverage_gate_under_test", GATE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"modules": {"app/known_gap.py": {"classification": "orphan", "reason": "x"}}})
    )
    monkeypatch.setattr(module, "BASELINE", baseline)
    return module


def _report(tmp_path, percent, files):
    body = {
        name: {"summary": {"num_statements": statements, "covered_lines": covered}}
        for name, (statements, covered) in files.items()
    }
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps({"totals": {"percent_covered": percent}, "files": body}))
    return path


def test_passes_when_total_meets_floor_and_untested_modules_are_baselined(gate, tmp_path):
    report = _report(tmp_path, 70.0, {"app/known_gap.py": (10, 0), "app/covered.py": (10, 9)})
    ok, lines = gate.check(report, 65.0)
    assert ok, lines


def test_fails_below_the_floor(gate, tmp_path):
    report = _report(tmp_path, 60.0, {"app/covered.py": (10, 9)})
    ok, lines = gate.check(report, 65.0)
    assert not ok
    assert any("minimum 65.00%" in line for line in lines)


def test_fails_on_a_new_untested_module(gate, tmp_path):
    report = _report(tmp_path, 90.0, {"app/known_gap.py": (10, 0), "app/brand_new.py": (5, 0)})
    ok, lines = gate.check(report, 65.0)
    assert not ok
    assert any("app/brand_new.py" in line for line in lines)


def test_a_test_file_or_script_is_not_an_app_module(gate, tmp_path):
    report = _report(tmp_path, 90.0, {"app/known_gap.py": (10, 0), "scripts/tool.py": (5, 0)})
    ok, _lines = gate.check(report, 65.0)
    assert ok


def test_a_missing_floor_is_a_failure_not_a_default(gate, monkeypatch, tmp_path):
    fake = tmp_path / "pyproject.toml"
    fake.write_text("[tool.coverage.report]\nexclude_lines = []\n")
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    with pytest.raises(SystemExit) as exc:
        gate.threshold()
    assert "fail_under" in str(exc.value)
