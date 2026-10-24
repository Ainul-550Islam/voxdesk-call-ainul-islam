"""No product source tree may contain a NUL-only or unexpected empty file.

This asserts the scanner itself against the real checkout, and then pins the
scanner's own behaviour on a temporary tree so a future edit that quietly stops
reporting corruption (for example by widening ``ALLOWED_EMPTY``) fails here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.find_zero_byte_files import (
    ALLOWED_EMPTY,
    _classify,
    delete_findings,
    find_zero_byte_files,
)

ROOT = Path(__file__).resolve().parents[2]

#: The trees the delivery contract names explicitly. ``realtime-engine`` was a
#: retired duplicate of ``services/realtime`` and is only checked when present.
SCANNED_ROOTS = ("app", "services", "realtime-engine", "dashboard/src")


def test_product_source_trees_hold_no_zero_byte_or_nul_only_files():
    present = [name for name in SCANNED_ROOTS if (ROOT / name).exists()]
    assert present, "none of the product source trees exist; the checkout is wrong"

    findings = find_zero_byte_files(ROOT, present)
    assert findings == [], "\n".join(f"{item.kind}: {item.path} ({item.reason})" for item in findings)


def test_repository_wide_scan_is_clean_for_every_scanned_root():
    findings = find_zero_byte_files(ROOT)
    assert findings == [], "\n".join(f"{item.kind}: {item.path} ({item.reason})" for item in findings)


def test_all_nul_file_is_reported(tmp_path: Path):
    corrupt = tmp_path / "app"
    corrupt.mkdir()
    target = corrupt / "corrupt_routes.py"
    target.write_bytes(b"\x00" * 512)

    findings = find_zero_byte_files(tmp_path, ("app",))
    assert [(item.path, item.kind) for item in findings] == [("app/corrupt_routes.py", "nul")]


def test_unexpected_empty_file_is_reported_but_package_markers_are_not(tmp_path: Path):
    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_bytes(b"")
    (package / "py.typed").write_bytes(b"")
    (package / "empty_module.py").write_bytes(b"")

    findings = find_zero_byte_files(tmp_path, ("app",))
    assert [item.path for item in findings] == ["app/empty_module.py"]
    assert findings[0].kind == "empty"
    assert "__init__.py" in ALLOWED_EMPTY


def test_binary_files_are_not_treated_as_corrupt_source(tmp_path: Path):
    package = tmp_path / "app"
    package.mkdir()
    (package / "fixture.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt \x00\x00")

    assert find_zero_byte_files(tmp_path, ("app",)) == []


def test_skipped_trees_are_never_scanned(tmp_path: Path):
    archived = tmp_path / "docs" / "archive" / "snapshot.py"
    archived.parent.mkdir(parents=True)
    archived.write_bytes(b"\x00" * 32)
    vendored = tmp_path / "app" / "node_modules" / "dep.js"
    vendored.parent.mkdir(parents=True)
    vendored.write_bytes(b"\x00" * 32)

    assert find_zero_byte_files(tmp_path) == []


def test_apply_deletes_only_reported_files(tmp_path: Path):
    package = tmp_path / "app"
    package.mkdir()
    corrupt = package / "bad.py"
    corrupt.write_bytes(b"\x00" * 64)
    healthy = package / "good.py"
    healthy.write_text("value = 1\n", encoding="utf-8")

    findings = find_zero_byte_files(tmp_path, ("app",))
    assert delete_findings(findings, tmp_path) == []
    assert not corrupt.exists()
    assert healthy.exists()
    assert find_zero_byte_files(tmp_path, ("app",)) == []


@pytest.mark.parametrize(
    ("payload", "name", "expected_kind"),
    [
        (b"\x00\x00\x00", "module.py", "nul"),
        (b"value = 1\n\x00", "module.py", "nul"),
        (b"", "module.py", "empty"),
        (b"", "__init__.py", None),
        (b"value = 1\n", "module.py", None),
    ],
)
def test_classifier_contract(payload: bytes, name: str, expected_kind: str | None):
    verdict = _classify(payload, name)
    assert (verdict[0] if verdict else None) == expected_kind
