"""Truth test: repository working-tree size budget (<= 60 MiB, target <= 40 MiB), zero zips, and report compaction."""

from __future__ import annotations

from pathlib import Path

from scripts.check_repo_size import MAX_FILE_BYTES, MAX_TREE_BYTES, TARGET_TREE_BYTES, inspect_tree, scan

ROOT = Path(__file__).resolve().parents[2]


def test_repository_respects_size_and_artifact_budget() -> None:
    assert scan(ROOT) == []
    report = inspect_tree(ROOT)
    assert report["ok"] is True, report
    assert int(report["tree_bytes"]) <= MAX_TREE_BYTES
    assert int(report["tree_bytes"]) <= TARGET_TREE_BYTES, (
        f"Expected cleaned tree <= {TARGET_TREE_BYTES} bytes (40 MiB), got {report['tree_bytes']}"
    )
    assert report["findings"] == []

    # Explicit PART 10 deletion & compaction assertions
    assert not (ROOT / "app" / "api" / "pcap_routes.py").exists()
    assert not (ROOT / "services" / "media-plane" / "libvoxdesk_media.a").exists()
    assert not (ROOT / "uploads").exists()
    assert not (ROOT / "FINAL_TEST_RESULTS.json").exists()
    assert not (ROOT / "tests_manifest.txt").exists()
    assert not (ROOT / "FILE_LIST.txt").exists()
    assert not (ROOT / "scripts" / "live_local.env").exists()
    assert (ROOT / "scripts" / "live_local.env.example").exists()

    for compacted in ("reports/PART_0_REPORT.md", "reports/PART_8_REPORT.md"):
        p = ROOT / compacted
        assert p.exists(), f"Missing compacted report {compacted}"
        assert p.stat().st_size <= 100 * 1024, f"{compacted} exceeds 100 KB: {p.stat().st_size}"


def test_check_repo_size_flags_zip_static_lib_and_oversized_file(tmp_path: Path) -> None:
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "bundle.zip").write_bytes(b"PK\x03\x04dummy")
    (tmp_path / "libfoo.a").write_bytes(b"!<arch>\n")
    (tmp_path / "huge.bin").write_bytes(b"y" * (MAX_FILE_BYTES + 32))

    report = inspect_tree(tmp_path)
    assert report["ok"] is False
    kinds_by_path = {(f["path"], f["kind"]) for f in report["findings"]}
    assert ("reports/bundle.zip", "forbidden_archive_or_binary") in kinds_by_path
    assert ("libfoo.a", "forbidden_archive_or_binary") in kinds_by_path
    assert ("huge.bin", "oversized_file") in kinds_by_path
