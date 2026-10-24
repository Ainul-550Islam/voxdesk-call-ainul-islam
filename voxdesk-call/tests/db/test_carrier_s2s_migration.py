"""The Batch 02 migration: ``0064_carrier_s2s``.

The migration is exercised against a real (in-memory) database: it must
create the two Batch 02 tables with the columns the models declare, it must
be safe to run twice, and it must remove everything it created when it is
downgraded.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = _REPO_ROOT / "alembic" / "versions" / "0064_carrier_s2s.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0064_carrier_s2s", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def migration():
    return _load_migration()


@pytest.fixture()
def connection():
    engine = sa.create_engine("sqlite://")
    conn = engine.connect()
    try:
        yield conn
    finally:
        conn.close()
        engine.dispose()


def _run(migration_module, connection, direction: str) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        if direction == "upgrade":
            migration_module.upgrade()
        else:
            migration_module.downgrade()


def _columns(connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    return {column["name"] for column in inspector.get_columns(table_name)}


def _tables(connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------
def test_the_revision_chain_is_correct(migration) -> None:
    assert migration.revision == "0064_carrier_s2s"
    assert migration.down_revision == "0063_live_proof_runs"
    assert migration.branch_labels is None
    assert migration.depends_on is None


# ---------------------------------------------------------------------------
# The upgrade
# ---------------------------------------------------------------------------
def test_the_upgrade_creates_the_two_tables(migration, connection) -> None:
    _run(migration, connection, "upgrade")
    tables = _tables(connection)
    assert "s2s_sessions" in tables
    assert "carrier_connection_checks" in tables


def test_the_session_table_carries_the_measured_columns(migration, connection) -> None:
    _run(migration, connection, "upgrade")
    columns = _columns(connection, "s2s_sessions")
    for name in (
        "id",
        "tenant_id",
        "environment_id",
        "call_id",
        "provider",
        "model",
        "status",
        "failover_reason",
        "error",
        "started_at",
        "ended_at",
        "duration_ms",
        "input_audio_s",
        "output_audio_s",
        "input_tokens",
        "output_tokens",
        "cost_micro_usd",
        "cost_known",
        "metadata_json",
        "created_at",
        "updated_at",
    ):
        assert name in columns, f"{name} is missing from s2s_sessions"


def test_the_check_table_carries_the_outcome_columns(migration, connection) -> None:
    _run(migration, connection, "upgrade")
    columns = _columns(connection, "carrier_connection_checks")
    for name in (
        "id",
        "tenant_id",
        "environment_id",
        "carrier",
        "status",
        "ok",
        "reason",
        "detail",
        "idempotency_key_digest",
        "checked_by_user_id",
        "checked_at",
        "created_at",
    ):
        assert name in columns, f"{name} is missing from carrier_connection_checks"


def test_the_upgrade_creates_the_indexes(migration, connection) -> None:
    _run(migration, connection, "upgrade")
    inspector = sa.inspect(connection)
    session_indexes = {ix["name"] for ix in inspector.get_indexes("s2s_sessions")}
    check_indexes = {ix["name"] for ix in inspector.get_indexes("carrier_connection_checks")}
    assert "ix_s2s_sessions_tenant_id" in session_indexes
    assert "ix_s2s_sessions_tenant_status" in session_indexes
    assert "ix_s2s_sessions_tenant_started" in session_indexes
    assert "ix_carrier_connection_checks_tenant_id" in check_indexes
    assert "ix_carrier_connection_checks_ok" in check_indexes
    assert "ix_carrier_connection_checks_tenant_checked" in check_indexes


def test_the_upgrade_is_safe_to_run_twice(migration, connection) -> None:
    _run(migration, connection, "upgrade")
    _run(migration, connection, "upgrade")
    assert "s2s_sessions" in _tables(connection)
    assert "carrier_connection_checks" in _tables(connection)


# ---------------------------------------------------------------------------
# The downgrade
# ---------------------------------------------------------------------------
def test_the_downgrade_removes_the_two_tables(migration, connection) -> None:
    _run(migration, connection, "upgrade")
    _run(migration, connection, "downgrade")
    tables = _tables(connection)
    assert "s2s_sessions" not in tables
    assert "carrier_connection_checks" not in tables


def test_the_downgrade_is_safe_without_the_tables(migration, connection) -> None:
    _run(migration, connection, "downgrade")
    assert "s2s_sessions" not in _tables(connection)


def test_the_upgrade_after_a_downgrade_recreates_the_tables(migration, connection) -> None:
    _run(migration, connection, "upgrade")
    _run(migration, connection, "downgrade")
    _run(migration, connection, "upgrade")
    tables = _tables(connection)
    assert "s2s_sessions" in tables
    assert "carrier_connection_checks" in tables


# ---------------------------------------------------------------------------
# The head
# ---------------------------------------------------------------------------
def _revisions() -> dict[str, tuple[str, str]]:
    """Every migration of the chain: revision -> (file, down_revision)."""
    import re

    revisions: dict[str, tuple[str, str]] = {}
    for path in sorted((_REPO_ROOT / "alembic" / "versions").glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        revision = re.search(
            r'^revision\s*[:=]\s*["\']([^"\']+)["\']', text, re.MULTILINE
        )
        if revision is None:
            continue
        down = re.search(r'^down_revision\s*[:=]\s*(.+)$', text, re.MULTILINE)
        down_value = ""
        if down is not None:
            down_value = down.group(1).strip().strip('"\'')
        revisions[revision.group(1)] = (path.name, down_value)
    return revisions


def test_batch_02_adds_no_head_of_its_own() -> None:
    """No revision points at ``0064_carrier_s2s``: the head is unchanged."""
    successors = [
        name
        for revision, (name, down) in _revisions().items()
        if down == "0064_carrier_s2s" and revision != "0064_carrier_s2s"
    ]
    assert successors == [], (
        "a revision follows 0064_carrier_s2s: " + ", ".join(successors)
    )


def test_the_revision_is_reachable_from_the_batch_01_revision() -> None:
    """``0064_carrier_s2s`` is the only revision that follows ``0063``."""
    successors = [
        revision
        for revision, (_name, down) in _revisions().items()
        if down == "0063_live_proof_runs"
    ]
    assert successors == ["0064_carrier_s2s"], (
        "the revision that follows 0063_live_proof_runs must be "
        "0064_carrier_s2s, found: " + ", ".join(sorted(successors))
    )
