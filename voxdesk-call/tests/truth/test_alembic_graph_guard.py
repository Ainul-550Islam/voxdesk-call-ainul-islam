"""Truth test: the Alembic graph is linear, single-headed and fits ``VARCHAR(32)``.

Two halves, mirroring the other guards in this suite:

* the real ``alembic/versions`` tree must scan clean, and
* a deliberately broken graph must be *detected* — a guard that cannot fail is
  not a guard.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from scripts.verify_alembic_graph import (
    MAX_REVISION_LENGTH,
    ROOT,
    collect,
    heads,
    scan,
    summarise,
)

MIGRATION = """\
revision = {revision!r}
down_revision = {down!r}
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
"""


def _write_migration(versions: Path, name: str, revision: str, down: object) -> None:
    (versions / f"{name}.py").write_text(
        MIGRATION.format(revision=revision, down=down), encoding="utf-8"
    )


def test_the_real_migration_graph_is_a_single_linear_head() -> None:
    findings = scan(ROOT)
    assert findings == [], json.dumps(findings, indent=2)

    summary = summarise(ROOT)
    assert summary["heads"] == [summary["head"]], summary
    # every file on disk is part of the chain: no orphans hiding off-graph
    assert summary["chain_length"] == summary["migration_files"], summary


def test_revision_ids_fit_the_version_num_column() -> None:
    summary = summarise(ROOT)
    assert summary["head"] is not None
    assert len(summary["head"]) <= MAX_REVISION_LENGTH
    for revision in (summary["root"], summary["head"]):
        assert revision is not None and len(revision) <= MAX_REVISION_LENGTH


def test_a_second_head_is_detected(tmp_path: Path) -> None:
    versions = tmp_path / "alembic" / "versions"
    versions.mkdir(parents=True)
    _write_migration(versions, "0001_base", "0001_base", None)
    _write_migration(versions, "0002_left", "0002_left", "0001_base")
    _write_migration(versions, "0002_right", "0002_right", "0001_base")

    kinds = {finding["kind"] for finding in scan(tmp_path)}
    assert "multiple_heads" in kinds
    assert sorted(heads(collect(versions))) == ["0002_left", "0002_right"]


def test_overlong_revision_id_is_detected(tmp_path: Path) -> None:
    versions = tmp_path / "alembic" / "versions"
    versions.mkdir(parents=True)
    long_id = "r" * (MAX_REVISION_LENGTH + 1)
    _write_migration(versions, "0001_base", "0001_base", None)
    _write_migration(versions, "0002_long", long_id, "0001_base")

    findings = scan(tmp_path)
    overlong = [f for f in findings if f["kind"] == "overlong_revision"]
    assert overlong and overlong[0]["length"] == MAX_REVISION_LENGTH + 1


def test_duplicate_revision_id_is_detected(tmp_path: Path) -> None:
    versions = tmp_path / "alembic" / "versions"
    versions.mkdir(parents=True)
    _write_migration(versions, "0001_base", "0001_base", None)
    _write_migration(versions, "0001_copy", "0001_base", None)

    kinds = {finding["kind"] for finding in scan(tmp_path)}
    assert "duplicate_revision" in kinds


def test_dangling_parent_is_detected(tmp_path: Path) -> None:
    versions = tmp_path / "alembic" / "versions"
    versions.mkdir(parents=True)
    _write_migration(versions, "0002_orphan", "0002_orphan", "0001_ghost")

    kinds = {finding["kind"] for finding in scan(tmp_path)}
    assert "dangling_down_revision" in kinds


def test_merge_point_with_two_parents_is_not_a_head(tmp_path: Path) -> None:
    versions = tmp_path / "alembic" / "versions"
    versions.mkdir(parents=True)
    _write_migration(versions, "0001_base", "0001_base", None)
    _write_migration(versions, "0002_left", "0002_left", "0001_base")
    _write_migration(versions, "0003_right", "0003_right", "0002_left")
    _write_migration(versions, "0004_merge", "0004_merge", ("0002_left", "0003_right"))

    findings = scan(tmp_path)
    assert [f for f in findings if f["kind"] in {"multiple_heads", "dangling_down_revision"}] == []
    summary = summarise(tmp_path)
    assert summary["heads"] == ["0004_merge"]


def test_the_cli_prints_an_empty_list_on_the_real_tree() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/verify_alembic_graph.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads(proc.stdout) == []


def test_a_single_migration_without_down_revision_is_reported(tmp_path: Path) -> None:
    versions = tmp_path / "alembic" / "versions"
    versions.mkdir(parents=True)
    (versions / "0001_base.py").write_text(
        textwrap.dedent("revision = '0001_base'\n"), encoding="utf-8"
    )
    kinds = {finding["kind"] for finding in scan(tmp_path)}
    assert "missing_down_revision" in kinds
