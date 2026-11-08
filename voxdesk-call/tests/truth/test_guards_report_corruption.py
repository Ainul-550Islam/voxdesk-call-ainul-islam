"""Truth test: guards must report NUL/UTF-8 corruption cleanly in .git-less trees."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def _run_guard(
    script_rel: str, target_root: Path, extra_args: list[str] | None = None
) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(ROOT / script_rel), "--root", str(target_root)]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(cmd, cwd=target_root, capture_output=True, text=True, check=False)


def test_guards_report_nul_byte_corruption_without_git_or_traceback(tmp_path: Path) -> None:
    assert not (tmp_path / ".git").exists()
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "clean.py").write_text("def ok() -> int:\n    return 42\n", encoding="utf-8")
    (app_dir / "corrupt.py").write_bytes(b"def broken() -> int:\n    return 1\x00\n")

    # 1. verify_no_null_bytes.py
    p_null = _run_guard("scripts/verify_no_null_bytes.py", tmp_path)
    assert p_null.returncode == 1, (p_null.returncode, p_null.stdout, p_null.stderr)
    assert "Traceback (most recent call last)" not in p_null.stderr
    assert "Traceback (most recent call last)" not in p_null.stdout
    null_findings = json.loads(p_null.stdout)
    assert any(
        item.get("file") == "app/corrupt.py" and item.get("reason") == "null byte"
        for item in null_findings
    )

    # 2. verify_no_fake_success.py
    p_fake = _run_guard("scripts/verify_no_fake_success.py", tmp_path)
    assert p_fake.returncode == 1, (p_fake.returncode, p_fake.stdout, p_fake.stderr)
    assert "Traceback (most recent call last)" not in p_fake.stderr
    assert "Traceback (most recent call last)" not in p_fake.stdout
    fake_findings = json.loads(p_fake.stdout)
    assert any(
        isinstance(entry, dict)
        and entry.get("file") == "app/corrupt.py"
        and "null byte" in str(entry.get("reason", ""))
        for entry in fake_findings
    )

    # 3. strip_padding_markers.py --check
    p_pad = _run_guard("scripts/strip_padding_markers.py", tmp_path, ["--check"])
    assert p_pad.returncode == 1, (p_pad.returncode, p_pad.stdout, p_pad.stderr)
    assert "Traceback (most recent call last)" not in p_pad.stderr
    assert "Traceback (most recent call last)" not in p_pad.stdout
    pad_report = json.loads(p_pad.stdout)
    assert "app/corrupt.py" in pad_report
    assert "null byte" in str(pad_report["app/corrupt.py"].get("error", ""))


def test_guards_report_invalid_utf8_without_traceback(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "bad_utf8.py").write_bytes(b"def f():\n    return '\xff\xfe'\n")

    for script_rel, extra in (
        ("scripts/verify_no_null_bytes.py", []),
        ("scripts/verify_no_fake_success.py", []),
        ("scripts/strip_padding_markers.py", ["--check"]),
    ):
        proc = _run_guard(script_rel, tmp_path, extra)
        assert proc.returncode == 1, (script_rel, proc.stdout, proc.stderr)
        assert "Traceback (most recent call last)" not in proc.stderr
        assert "Traceback (most recent call last)" not in proc.stdout
        parsed = json.loads(proc.stdout)
        assert parsed


def test_guards_pass_clean_gitless_tree(tmp_path: Path) -> None:
    assert not (tmp_path / ".git").exists()
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True)
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "service.py").write_text(
        "def compute_total(items: list[int]) -> int:\n    return sum(items)\n",
        encoding="utf-8",
    )

    p_null = _run_guard("scripts/verify_no_null_bytes.py", tmp_path)
    assert p_null.returncode == 0, p_null.stderr
    assert json.loads(p_null.stdout) == []

    p_fake = _run_guard("scripts/verify_no_fake_success.py", tmp_path)
    assert p_fake.returncode == 0, p_fake.stderr
    assert json.loads(p_fake.stdout) == []

    p_pad = _run_guard("scripts/strip_padding_markers.py", tmp_path, ["--check"])
    assert p_pad.returncode == 0, p_pad.stderr
    assert json.loads(p_pad.stdout) == {}
