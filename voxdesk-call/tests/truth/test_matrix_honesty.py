"""Feature-matrix honesty & sales-claim verification tests (Capability 16 / Step 5 R8)."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_every_live_capability_in_manifest_has_real_test_files_and_functions() -> None:
    manifest_path = REPO_ROOT / "tests" / "truth" / "feature_manifest.yaml"
    assert manifest_path.exists()
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}

    entries = manifest.get("features") or manifest.get("capabilities") or []
    assert isinstance(entries, list) and len(entries) >= 15

    active_count = 0
    for item in entries:
        status = str(item.get("status") or "").upper()
        if status not in ("LIVE", "API_ONLY", "FULL"):
            continue
        active_count += 1
        tests = item.get("evidence_tests") or item.get("tests") or []
        assert tests, f"Feature {item.get('id') or item.get('name')} is {status} without evidence_tests"
        for node_id in tests:
            parts = str(node_id).split("::")
            rel_file = parts[0]
            test_path = REPO_ROOT / rel_file
            assert test_path.exists(), f"Missing evidence test file {rel_file} for {item.get('name')}"
            fn_part = parts[-1].split("[", 1)[0] if len(parts) > 1 else ""
            src = test_path.read_text(encoding="utf-8")
            assert f"def {fn_part}" in src, f"Missing test function {fn_part} in {rel_file}"

    assert active_count >= 15


def test_sales_docs_contain_no_overstated_slack_teams_or_100pct_claims() -> None:
    sales_dir = REPO_ROOT / "docs" / "SALES"
    assert sales_dir.exists()

    forbidden_patterns = [
        re.compile(r"\b100%\s+(?:feature\s+)?complete\b", re.IGNORECASE),
        re.compile(r"\bSlack\s+(?:and|&)\s+Teams\s+(?:live|outbound\s+messaging\s+supported)\b", re.IGNORECASE),
        re.compile(r"\bzero\s+known\s+limitations\b", re.IGNORECASE),
    ]

    md_files = list(sales_dir.glob("*.md"))
    assert md_files, "Expected Markdown files in docs/SALES/"
    for md in md_files:
        text = md.read_text(encoding="utf-8")
        for pat in forbidden_patterns:
            match = pat.search(text)
            assert match is None, f"Overstated claim {match.group(0)!r} found in {md.name}"
