"""Every route module is mounted, a helper, or an explicitly reasoned exception.

Runs the real scanner against the real checkout (no fixture repository), so a
route module that lands without an ``include_router`` call in ``app/main.py``
fails CI here instead of being discovered by a buyer.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from scripts.verify_no_dead_routers import (
    ALLOWLIST,
    ALLOWLIST_BASELINE,
    classify_modules,
    defines_router,
    mounted_names,
    scan,
    summary,
)

ROOT = Path(__file__).resolve().parents[2]


def test_no_orphan_route_modules_and_no_filler_markers():
    problems = scan(ROOT)
    assert problems == [], "\n".join(
        f"{item['kind']}: {item.get('path', item.get('module'))}: {item.get('reason', item.get('text', ''))}"
        for item in problems
    )


def test_route_module_population_is_classified():
    counts = summary(ROOT)
    assert counts.get("mounted", 0) > 100, "app/main.py should mount the product surface"
    assert "orphan" not in counts, counts


def test_every_allowlisted_module_exists_and_still_builds_a_router():
    verdicts = {verdict.module: verdict for verdict in classify_modules(ROOT)}
    for module in ALLOWLIST:
        assert module in verdicts, f"{module} is allowlisted but no longer exists"
        assert verdicts[module].defines_router, f"{module} no longer defines a router"
        assert verdicts[module].status == "allowlisted", verdicts[module]


def test_allowlist_cannot_grow_without_editing_the_frozen_baseline():
    assert set(ALLOWLIST) == set(ALLOWLIST_BASELINE)


def test_strict_mode_reports_the_known_backlog():
    problems = [item for item in scan(ROOT, strict=True) if item["kind"] == "allowlisted_dead_router"]
    assert {item["module"] for item in problems} == set(ALLOWLIST)


def test_entrypoint_mounting_is_read_with_ast_not_imported():
    mounted, imported = mounted_names(ROOT / "app/main.py")
    assert "telephony_router" in mounted
    assert imported["telephony_router"] == "app.telephony.twilio_handler"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from fastapi import APIRouter\nrouter = APIRouter()\n", True),
        ("import fastapi\nrouter = fastapi.APIRouter(prefix='/x')\n", True),
        ("from fastapi import APIRouter\nrouter: object = APIRouter()\n", True),
        ("from fastapi import APIRouter\nVALUE = 1\n", False),
        ("def build():\n    return APIRouter()\n", False),
    ],
)
def test_router_detection_contract(source: str, expected: bool):
    assert defines_router(ast.parse(textwrap.dedent(source))) is expected


def test_filler_markers_are_detected_in_a_planted_module(tmp_path: Path):
    package = tmp_path / "app" / "api"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "main.py").write_text("app = object()\n", encoding="utf-8")
    planted = package / "generated_routes.py"
    planted.write_text(
        "# File: generated — 1050+ lines production — NO SKIP FULL CODE\n"
        "from fastapi import APIRouter\n"
        "router = APIRouter()\n"
        "\n"
        "@router.get('/endpoint-7')\n"
        "async def endpoint_7():\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )

    problems = scan(tmp_path)
    kinds = {item["kind"] for item in problems}
    assert "orphan_route_module" in kinds
    assert "filler_marker" in kinds
    markers = {item["marker"] for item in problems if item["kind"] == "filler_marker"}
    assert {"generated endpoint path", "line-count banner"} <= markers
