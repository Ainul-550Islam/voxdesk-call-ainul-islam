"""Truth test: README.md, PRICING_AND_TIERS.md, and KNOWN_LIMITATIONS.md match docs/STATS.json."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import re

from scripts import repo_stats

ROOT = Path(__file__).resolve().parents[2]


def test_stats_json_is_single_source_of_truth() -> None:
    stats_path = ROOT / "docs" / "STATS.json"
    assert stats_path.exists(), "docs/STATS.json must exist"
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    assert stats.get("schema") == "voxdesk.repo_stats/v1"

    # Fast live structural checks (without re-spawning pytest inside pytest)
    sources = repo_stats._source_files(ROOT)
    assert stats["source_files"] == len(sources)
    assert stats["source_physical_lines"] == sum(
        len(p.read_text(encoding="utf-8", errors="replace").splitlines()) for p in sources
    )
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        from app.main import app
    assert stats["registered_routes"] == len(app.routes)

    src_files = int(stats["source_files"])
    src_lines = int(stats["source_physical_lines"])
    reg_routes = int(stats["registered_routes"])
    collected = int(stats["collected_pytest_nodes"])
    junit_passed = int(stats["junit_passed_tests"])
    head = str(stats["alembic_head"])
    fm = stats["feature_matrix"]
    assert fm == {"total": 41, "LIVE": 38, "API_ONLY": 1, "NOT_CONFIGURED": 1, "PLANNED": 1}

    # 1. README.md must embed identical numbers in its JSON block
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"```json\s*(\{.*?\})\s*```", readme, re.S)
    assert m is not None, "README.md missing JSON stats block"
    readme_stats = json.loads(m.group(1))
    assert readme_stats["source_files"] == src_files
    assert readme_stats["source_physical_lines"] == src_lines
    assert readme_stats["registered_routes"] == reg_routes
    assert readme_stats["collected_pytest_nodes"] == collected
    assert f"REST API ({reg_routes} routes)" in readme
    assert "`dashboard/` (Vite + React 18) is the **canonical production operator console**" in readme
    assert "`dashboard-next/` (Next.js 16 App Router) is an **experimental" in readme

    # 2. docs/SALES/PRICING_AND_TIERS.md must match docs/STATS.json and have no hard-coded GREEN
    pricing = (ROOT / "docs" / "SALES" / "PRICING_AND_TIERS.md").read_text(encoding="utf-8")
    assert "**GREEN**" not in pricing, "Gate table must read from docs/STATS.json instead of hard-coded GREEN"
    assert f"`{src_files}` (`{src_lines:,}` physical lines)" in pricing
    assert f"`{reg_routes}` registered routes" in pricing
    assert f"`{collected}` collected tests" in pricing
    assert f"`{junit_passed:,}` passed JUnit executions" in pricing
    assert f"`{head}`" in pricing

    # 3. docs/DUE_DILIGENCE/KNOWN_LIMITATIONS.md must match docs/STATS.json
    known = (ROOT / "docs" / "DUE_DILIGENCE" / "KNOWN_LIMITATIONS.md").read_text(encoding="utf-8")
    assert f"`{src_files}` (`{src_lines:,}` physical lines)" in known
    assert f"`{reg_routes}`" in known
    assert f"`{collected:,}`" in known
    assert f"`{junit_passed:,}` passed JUnit test executions" in known
    assert f"`{head}`" in known
