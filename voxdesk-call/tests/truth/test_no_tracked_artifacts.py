"""The tracked-artifact gate, exercised against real git repositories.

The gate itself (`scripts/verify_no_tracked_artifacts.py`) refuses to let a
generated log, a delivery archive, or anything over a megabyte into a clone a
buyer has to download. A gate nobody tests is a gate that silently stops
working, so the first half of this module builds throwaway git repositories in
``tmp_path`` and proves the gate catches each violation class and honours its
allowlist.

The second half runs the gate against *this* repository. That one is expected to
fail until ``bash scripts/repo_cleanup.sh --apply`` has been run: 677 log files,
17 archives and 13 oversized evidence dumps entered this repository before the
gate existed. It is marked xfail rather than skipped so the state stays visible
in the test report, and non-strictly so that the day someone runs the cleanup the
suite does not break on an unexpected pass.
"""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_no_tracked_artifacts.py"
ALLOWLIST = REPO_ROOT / "scripts" / "tracked_artifact_allowlist.txt"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_no_tracked_artifacts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


module = _load_module()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway git repository with an initial commit."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Truth Test")
    (path / "app.py").write_text("print('hi')\n", encoding="utf-8")
    _git(path, "add", "app.py")
    _git(path, "commit", "-q", "-m", "initial")
    return path


def _commit(repo: Path, rel: str, size: int) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", f"add {rel}")


def test_a_clean_repository_passes(repo: Path) -> None:
    assert module.find_violations(repo) == []


def test_a_tracked_log_is_a_violation(repo: Path) -> None:
    _commit(repo, "logs/collection.log", 100)
    violations = module.find_violations(repo)
    assert [item["path"] for item in violations] == ["logs/collection.log"]
    assert "log" in str(violations[0]["reason"])


def test_a_tracked_archive_is_a_violation(repo: Path) -> None:
    _commit(repo, "reports/delivery.zip", 2048)
    violations = module.find_violations(repo)
    assert [item["path"] for item in violations] == ["reports/delivery.zip"]
    assert ".zip" in str(violations[0]["reason"])


def test_an_oversized_file_is_a_violation(repo: Path) -> None:
    _commit(repo, "reports/results.json", 1_000_001)
    violations = module.find_violations(repo)
    assert [item["path"] for item in violations] == ["reports/results.json"]
    assert "exceeds" in str(violations[0]["reason"])


def test_a_file_just_under_the_limit_is_allowed(repo: Path) -> None:
    _commit(repo, "reports/results.json", 1_000_000)
    assert module.find_violations(repo) == []


def test_an_allowlisted_file_is_exempt(repo: Path, monkeypatch) -> None:
    """The allowlist exempts by exact path, and only while the entry exists."""
    (repo / "scripts").mkdir(exist_ok=True)
    monkeypatch.setattr(module, "ALLOWLIST", repo / "scripts" / "tracked_artifact_allowlist.txt")
    (repo / "scripts" / "tracked_artifact_allowlist.txt").write_text(
        "# a reason for the exception\nreports/results.json\n", encoding="utf-8"
    )
    _commit(repo, "reports/results.json", 5_000_000)
    assert module.find_violations(repo) == []

    # Dropping the entry puts the file back under the rule.
    (repo / "scripts" / "tracked_artifact_allowlist.txt").write_text("", encoding="utf-8")
    assert [item["path"] for item in module.find_violations(repo)] == ["reports/results.json"]


def test_the_shipped_allowlist_is_parsed_and_non_empty() -> None:
    allowed = module.load_allowlist()
    assert allowed, "the shipped allowlist must name the files that stay tracked"
    assert "contracts/openapi.json" in allowed
    for entry in allowed:
        assert not entry.startswith("#"), f"comment leaked into the allowlist: {entry!r}"
        assert (REPO_ROOT / entry).exists(), (
            f"allowlist entry {entry!r} does not exist in the repository; stale entries "
            "hide real violations"
        )


def test_main_returns_zero_on_a_clean_repository(repo: Path, monkeypatch, capsys) -> None:
    """The gate must be silent and green when there is nothing to report."""
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    monkeypatch.setattr(module, "ALLOWLIST", repo / "scripts" / "allowlist.txt")
    assert module.main([]) == 0
    assert "PASS" in capsys.readouterr().out


def test_main_returns_one_and_names_every_violation(repo: Path, monkeypatch, capsys) -> None:
    _commit(repo, "logs/run.log", 10)
    _commit(repo, "reports/delivery.zip", 4096)
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    monkeypatch.setattr(module, "ALLOWLIST", repo / "scripts" / "allowlist.txt")

    assert module.main([]) == 1
    stderr = capsys.readouterr().err
    assert "logs/run.log" in stderr
    assert "reports/delivery.zip" in stderr
    assert "scripts/repo_cleanup.sh" in stderr, (
        "a failure must tell the operator how to fix it"
    )


def test_main_writes_json_only_when_asked(repo: Path, monkeypatch, tmp_path: Path) -> None:
    _commit(repo, "logs/run.log", 10)
    monkeypatch.setattr(module, "REPO_ROOT", repo)
    monkeypatch.setattr(module, "ALLOWLIST", repo / "scripts" / "allowlist.txt")

    out = tmp_path / "nested" / "artifacts.json"
    assert module.main(["--json", str(out)]) == 1
    assert out.exists(), "--json must actually write the file it was given"

    import json

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == "voxdesk.tracked_artifact_check/v1"
    assert payload["ok"] is False
    assert payload["violation_count"] == 1
    assert payload["violations"][0]["path"] == "logs/run.log"
    assert payload["max_bytes"] == 1_000_000


def test_the_cli_is_executable_end_to_end(tmp_path: Path) -> None:
    """`python3 scripts/verify_no_tracked_artifacts.py --json PATH` from a shell."""
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        pytest.skip(
            "the gate inspects what git tracks, so it can only be exercised "
            "inside a checkout"
        )
    out = tmp_path / "artifacts.json"
    result = subprocess.run(
        ["python3", str(SCRIPT), "--json", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode in (0, 1), (
        f"unexpected exit {result.returncode}: {result.stderr}"
    )
    assert out.exists(), "the --json path must be written by the real invocation"
    import json

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema"] == "voxdesk.tracked_artifact_check/v1"
    assert payload["tracked_files_checked"] > 0


@pytest.mark.xfail(
    strict=False,
    reason=(
        "this repository still tracks 677 *.log files, 17 *.zip archives and 13 "
        "files over 1 MB from before the gate existed; run "
        "'bash scripts/repo_cleanup.sh --apply' to clear it"
    ),
)
def test_this_repository_tracks_no_generated_artifacts() -> None:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    violations = module.find_violations(REPO_ROOT)
    assert violations == [], (
        f"{len(violations)} generated artifact(s) are tracked; run "
        "bash scripts/repo_cleanup.sh --apply"
    )
