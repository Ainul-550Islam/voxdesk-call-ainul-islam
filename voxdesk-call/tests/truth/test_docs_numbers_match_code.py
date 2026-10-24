"""Numbers quoted in the docs are measured, never remembered.

The README statistics block and any route/test/migration-head claim in
``docs/SALES/*.md`` must equal what the code actually registers. The check is
run against the real checkout; the unit-level tests below pin the comparison
logic so a broken matcher cannot make this pass by finding nothing.
"""

from __future__ import annotations

from pathlib import Path

from scripts.verify_docs_match_code import (
    Mismatch,
    compare_readme,
    compare_sales_docs,
    measure_migration_head,
    measure_routes,
    readme_claims,
    verify,
)

ROOT = Path(__file__).resolve().parents[2]


def test_documented_numbers_match_the_code():
    problems, measured = verify(ROOT)
    assert measured["registered_routes"] > 0
    assert measured["migration_head"], "a single Alembic head must be measurable"
    assert problems == [], "\n".join(
        f"{item.document}:{item.line} {item.claim} documented={item.documented} measured={item.measured}"
        for item in problems
    )


def test_readme_publishes_every_measured_statistic():
    claims, problems = readme_claims(ROOT)
    assert problems == []
    assert set(claims) == {
        "source_files",
        "source_physical_lines",
        "registered_routes",
        "collected_pytest_nodes",
    }


def test_route_count_and_migration_head_are_read_from_the_real_objects():
    from app.main import app

    assert measure_routes(ROOT) == len(app.routes)
    head = measure_migration_head(ROOT)
    assert head.startswith("00"), head
    assert len(head) <= 32, "alembic_version.version_num is VARCHAR(32)"


def test_a_stale_readme_number_is_reported(tmp_path: Path, monkeypatch):
    (tmp_path / "README.md").write_text(
        "# VoxDesk\n\n```json\n{\n"
        '  "source_files": 1,\n'
        '  "source_physical_lines": 2,\n'
        '  "registered_routes": 3,\n'
        '  "collected_pytest_nodes": 4\n'
        "}\n```\n",
        encoding="utf-8",
    )
    measured = {
        "source_files": 1,
        "source_physical_lines": 2,
        "registered_routes": 999,
        "collected_pytest_nodes": 4,
    }
    problems = compare_readme(tmp_path, measured=measured)
    assert [(item.claim, item.documented, item.measured) for item in problems] == [
        ("registered_routes", "3", "999")
    ]


def test_missing_readme_statistics_are_a_failure(tmp_path: Path):
    (tmp_path / "README.md").write_text("# VoxDesk\n", encoding="utf-8")
    problems = compare_readme(tmp_path, measured={"registered_routes": 1})
    assert {item.claim for item in problems} >= {"registered_routes", "collected_pytest_nodes"}


def test_sales_prose_claims_are_compared(tmp_path: Path):
    sales = tmp_path / "docs" / "SALES"
    sales.mkdir(parents=True)
    (sales / "SHEET.md").write_text(
        "The platform registers 1089 routes today.\n"
        "The backend ships 4711 tests.\n"
        "alembic head: 0062_drop_pcap_artifacts\n",
        encoding="utf-8",
    )
    measured = {"routes": 1182, "tests": 4711, "migration_head": "0062_drop_pcap_artifacts"}

    problems = compare_sales_docs(tmp_path, measured=measured)
    assert problems == [
        Mismatch("docs/SALES/SHEET.md", 1, "routes", "1089", "1182"),
    ]


def test_no_sales_document_contradicts_the_code():
    from scripts.verify_docs_match_code import measure_collected_tests

    del measure_collected_tests  # imported only to document that this test is cheap
    problems = compare_sales_docs(
        ROOT,
        measured={
            "routes": measure_routes(ROOT),
            "tests": None,
            "migration_head": measure_migration_head(ROOT),
        },
    )
    assert problems == []
