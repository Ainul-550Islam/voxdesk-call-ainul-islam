"""P17B-04 — the browser gates must stay wired up.

These assertions read the front-end sources instead of driving a browser: the
Playwright suites already do that, and they need a live front door plus a seeded
API. What nothing checked until now is the *wiring* — that the accessibility
gate still covers every console route, that the budget file still forbids the
regressions it was written for, and that CI actually invokes both. A suite that
silently stops covering a page is worse than no suite, because the green tick
implies coverage that is not there.

Run: pytest tests/frontend/test_frontend_gates.py -q
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WEB = REPO / "dashboard-next"
A11Y = WEB / "e2e" / "a11y.spec.ts"
JOURNEYS = WEB / "e2e" / "journeys.spec.ts"
LHCI = WEB / "lighthouserc.json"
CHECK = REPO / "scripts" / "check_frontend.sh"
DASHBOARD_APP = WEB / "app" / "dashboard"


def read(path: Path) -> str:
    assert path.is_file(), f"missing {path.relative_to(REPO)}"
    return path.read_text(encoding="utf-8")


def static_console_routes() -> set[str]:
    """Every non-dynamic route under app/dashboard, as a URL path."""
    routes: set[str] = set()
    for page in DASHBOARD_APP.rglob("page.tsx"):
        if "[" in str(page):
            continue
        rel = page.parent.relative_to(WEB / "app")
        routes.add("/" + "/".join(rel.parts))
    return routes


def audited_console_routes() -> set[str]:
    block = re.search(r"const ALL_CONSOLE_PAGES[^=]*= \[(.*?)\];", read(A11Y), re.S)
    assert block, "ALL_CONSOLE_PAGES list not found in a11y.spec.ts"
    return set(re.findall(r'"([^"]+)"', block.group(1)))


def test_every_console_route_is_audited():
    """The gate must cover the product, not a remembered subset of it."""
    routes = static_console_routes()
    audited = audited_console_routes()
    assert routes, "no console routes discovered — the walker is broken, not the site"
    assert routes - audited == set(), (
        "these console routes render for operators but are never audited: "
        + ", ".join(sorted(routes - audited))
    )


def test_public_pages_are_audited():
    block = re.search(r"const PUBLIC_PAGES[^=]*= \[(.*?)\];", read(A11Y), re.S)
    assert block, "PUBLIC_PAGES list not found in a11y.spec.ts"
    pages = re.findall(r'"([^"]+)"', block.group(1))
    # The marketing site is the part of the product a prospect sees first.
    for required in ("/", "/pricing", "/signup", "/login"):
        assert required in pages, f"{required} dropped out of the public audit list"
    assert len(pages) >= 26, f"public audit list shrank to {len(pages)} pages"


def test_audit_fails_on_serious_or_critical():
    """A gate that only warns is decoration."""
    spec = read(A11Y)
    assert 'SERIOUS = new Set(["serious", "critical"])' in spec or (
        "serious" in spec and "critical" in spec
    )
    assert re.search(r"expect\(blocking\.length[^)]*\)\.toBe\(0\)", spec), (
        "the audit must assert zero blocking violations"
    )


def test_audit_does_not_wait_for_network_idle():
    """Console pages poll; `networkidle` burns the whole test timeout on them."""
    spec = read(A11Y)
    code = "\n".join(
        line for line in spec.splitlines() if not line.strip().startswith("//")
    )
    assert "waitForLoadState(\"networkidle\")" not in code


def test_console_audit_can_be_sharded():
    """Small machines cannot audit the whole console in one process."""
    spec = read(A11Y)
    assert "A11Y_SHARD" in spec
    assert "A11Y_CONSOLE_PAGES" in spec


def test_journeys_cover_the_critical_paths():
    spec = read(JOURNEYS)
    for step in (
        "public front door",
        "session lifecycle",
        "operator journeys",
        "keyboard journey",
    ):
        assert step in spec, f"journey group '{step}' disappeared"


def test_lighthouse_budgets_are_enforced():
    config = json.loads(read(LHCI))["ci"]
    assertions = config["assert"]["assertions"]

    def budget(audit: str) -> tuple[str, dict]:
        level, options = assertions[audit]
        return level, options

    level, options = budget("cumulative-layout-shift")
    assert level == "error", "CLS is the metric this run regressed; keep it blocking"
    assert options["maxNumericValue"] <= 0.1

    level, options = budget("largest-contentful-paint")
    assert level == "error"
    assert options["maxNumericValue"] <= 2500

    level, options = budget("categories:accessibility")
    assert level == "error"
    assert options["minScore"] >= 0.9

    level, options = budget("categories:performance")
    assert level == "error"
    assert options["minScore"] >= 0.9

    urls = config["collect"]["url"]
    assert len(urls) >= 4
    for url in urls:
        assert url.startswith("http"), url


def test_lighthouse_covers_the_marketing_front_door():
    from urllib.parse import urlparse

    config = json.loads(read(LHCI))["ci"]
    paths = {urlparse(u).path or "/" for u in config["collect"]["url"]}
    for required in ("/", "/pricing", "/login"):
        assert required in paths, f"Lighthouse does not measure {required}"


def test_ci_runs_both_browser_suites():
    script = read(CHECK)
    assert "e2e/journeys.spec.ts" in script, "journeys are not wired into CI"
    assert "e2e/a11y.spec.ts" in script, "the accessibility gate is not wired into CI"
    assert "E2E_BASE_URL" in script, (
        "the browser steps must be explicitly opt-in, not silently absent"
    )
    # The console half is the expensive half; it must be sharded.
    assert "A11Y_SHARD=1/2" in script and "A11Y_SHARD=2/2" in script


def test_generated_artifacts_are_ignored():
    ignore = read(WEB / ".gitignore")
    for pattern in ("test-results/", "lighthouse-report/", ".lighthouseci/"):
        assert pattern in ignore, f"{pattern} would be committed"


def test_no_remote_stylesheet_import_remains():
    """A blocked cross-origin @import is a layout-shift generator."""
    offenders = []
    for sheet in (WEB / "app" / "globals.css", WEB / "styles" / "enterprise.css"):
        if not sheet.is_file():
            continue
        for line in sheet.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("@import") and "http" in line:
                offenders.append(f"{sheet.name}: {line.strip()[:80]}")
    assert offenders == [], "remote @import will be blocked by CSP: " + "; ".join(offenders)
