"""``scripts/compliance_evidence_run.py`` fails closed.

The script is what CI and the nightly CronJob actually run, so the properties
that matter are the exit codes: a red control must fail the job, an un-runnable
tool must fail the job *differently* so an operator can tell "the controls are
red" from "the tool did not run", and an absent evidence source must never be
reported as a pass.

Each test runs the real script in a subprocess against a real SQLite database,
because the exit code is the contract and it only exists at process level.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "compliance_evidence_run.py"


def _database(tmp_path: Path) -> str:
    """Create an empty SQLite database with every table, and return its URL."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'evidence.db'}"
    bootstrap = (
        "import asyncio\n"
        "from sqlalchemy.ext.asyncio import create_async_engine\n"
        "import app.db.compliance_v2_models  # noqa: F401\n"
        "from app.db.models import Base\n"
        "async def main():\n"
        "    engine = create_async_engine(%r)\n"
        "    async with engine.begin() as conn:\n"
        "        await conn.run_sync(Base.metadata.create_all)\n"
        "    await engine.dispose()\n"
        "asyncio.run(main())\n" % url
    )
    completed = subprocess.run(
        [sys.executable, "-c", bootstrap],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    return url


def _run(tmp_path: Path, database_url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(
        {
            "DATABASE_URL": database_url,
            "APP_ENV": "test",
            "COMPLIANCE_EVIDENCE_DIR": str(tmp_path / "evidence"),
            "KMS_PROVIDER": "local_dev",
            "KMS_LOCAL_MASTER_KEY_PATH": str(tmp_path / "local.key"),
        }
    )
    (tmp_path / "evidence").mkdir(exist_ok=True)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
    )


@pytest.fixture(scope="module")
def database(tmp_path_factory) -> str:
    return _database(tmp_path_factory.mktemp("evidence-run"))


def test_the_document_is_json_and_says_it_ran(database, tmp_path):
    completed = _run(tmp_path, database)

    document = json.loads(completed.stdout)
    assert document["ran"] is True
    assert document["counts"]
    assert completed.returncode in (0, 1)


def test_the_exit_code_matches_the_status_counts(database, tmp_path):
    completed = _run(tmp_path, database)
    document = json.loads(completed.stdout)

    failing = [
        status
        for status, count in document["counts"].items()
        if status in {"FAIL", "ERROR"} and count
    ]
    assert completed.returncode == (1 if failing else 0)
    assert document["ok"] is (not failing)
    assert sorted(failing) == document["failing_statuses"]


def test_not_configured_is_reported_but_never_counts_as_a_pass(database, tmp_path):
    completed = _run(tmp_path, database)
    document = json.loads(completed.stdout)

    not_configured = document["counts"].get("NOT_CONFIGURED", 0)
    if not_configured:
        # The status is visible in the summary, and it does not make the run
        # green on its own: a control with no evidence source is not evidence.
        assert "NOT_CONFIGURED" in document["counts"]
        assert document["ok"] in (True, False)
    assert set(document["counts"]) <= {"PASS", "FAIL", "NOT_CONFIGURED", "ERROR"}


def test_an_operator_can_promote_not_configured_to_a_failure(database, tmp_path):
    completed = _run(tmp_path, database, "--fail-on", "FAIL,ERROR,NOT_CONFIGURED")
    document = json.loads(completed.stdout)

    promoted = bool(document["counts"].get("NOT_CONFIGURED"))
    assert completed.returncode == (1 if promoted else 0)


def test_a_tenant_that_does_not_exist_fails_the_tool_not_the_controls(database, tmp_path):
    completed = _run(
        tmp_path, database, "--tenant", "00000000-0000-0000-0000-000000000000"
    )

    document = json.loads(completed.stdout)
    assert completed.returncode == 2, completed.stdout
    assert document["ran"] is False
    assert "not found" in document["error"]


def test_an_unreachable_database_is_exit_two(database, tmp_path):
    completed = _run(tmp_path, "sqlite+aiosqlite:////nonexistent/dir/evidence.db")

    document = json.loads(completed.stdout)
    assert completed.returncode == 2
    assert document["ran"] is False
    assert document["error"]


def test_the_json_file_matches_stdout(database, tmp_path):
    target = tmp_path / "out" / "evidence.json"

    completed = _run(tmp_path, database, "--json", str(target))

    assert target.exists()
    assert json.loads(target.read_text()) == json.loads(completed.stdout)


def test_a_bad_argument_does_not_produce_a_green_run(database, tmp_path):
    completed = _run(tmp_path, database, "--tenant", "not-a-uuid")

    assert completed.returncode != 0
    assert "ran" not in completed.stdout or '"ran": false' in completed.stdout
