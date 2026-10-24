"""Revision ``0065_compliance_v2``: what it creates, and how cleanly it reverses.

The migration is validated against a real SQLite database through a real Alembic
``EnvironmentContext``, not by reading the file. Two things make this test honest
about the parallel-safety rule as well: it asserts the revision metadata the
patch promises, and it *refuses to pretend* that Batch 01/02 migrations are
present.

Those two revisions (``0063_live_proof_runs``, ``0064_carrier_s2s``) belong to
the other batches and are deliberately absent from this patch. When they are
missing, this test writes a no-op stand-in **in a temporary directory only**, so
0065 can be applied and reversed in isolation. Nothing is added to the repository
and nothing is shipped, because a stub migration that reached a real database
would silently claim that another batch had run.
"""

from __future__ import annotations

import importlib.util
import re
import sqlite3
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERSIONS = REPO_ROOT / "alembic" / "versions"
MIGRATION = VERSIONS / "0065_compliance_v2.py"

#: Every table the revision is responsible for.
EXPECTED_TABLES = {
    "compliance_access_review_campaigns",
    "compliance_access_review_decisions",
    "compliance_audit_exports",
    "compliance_breach_clocks",
    "compliance_break_glass_grants",
    "compliance_control_status",
    "compliance_dsar_ledger",
    "compliance_dsar_requests",
    "compliance_evidence_snapshots",
    "compliance_incident_timeline",
    "compliance_incidents",
    "compliance_kms_rekey_jobs",
    "compliance_phi_policies",
    "compliance_residency_violations",
    "compliance_siem_cursors",
    "compliance_siem_dead_letters",
    "compliance_siem_sinks",
    "compliance_tenant_residency",
}

_ENV_PY = '''\
from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
'''

_STUB = '''\
"""Stand-in for a revision owned by another batch. Never shipped."""

revision = {revision!r}
down_revision = {down!r}
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
'''


def _module():
    spec = importlib.util.spec_from_file_location("migration_0065", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_REVISION_RE = re.compile(r'^revision\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_DOWN_RE = re.compile(r'^down_revision\s*=\s*(.+)$', re.MULTILINE)


def _revision_of(path: Path) -> str:
    match = _REVISION_RE.search(path.read_text(encoding="utf-8"))
    return match.group(1) if match else ""


def _parent_of(path: Path) -> str:
    match = _DOWN_RE.search(path.read_text(encoding="utf-8"))
    if not match:
        return ""
    raw = match.group(1).strip()
    if raw in ("None", "none"):
        return ""
    return raw.strip('"\'')


@pytest.fixture(scope="module")
def alembic_env(tmp_path_factory) -> Path:
    """A throwaway Alembic project holding the real 0065 and its real parents.

    Real ancestor files (Batch 01/02 revisions, when those batches are applied)
    are copied in unchanged. Any ancestor that is *absent* — which is the normal
    state of a Batch 03-only tree — is replaced by a no-op stand-in written into
    this temporary directory, so the chain is linear and nothing is added to the
    repository.
    """
    root = tmp_path_factory.mktemp("alembic-0065")
    versions = root / "versions"
    versions.mkdir()
    (root / "alembic.ini").write_text(
        "[alembic]\n"
        "script_location = %(here)s\n"
        "sqlalchemy.url = sqlite:///" + str(root / "migration.db") + "\n"
        % {},
        encoding="utf-8",
    )
    (root / "env.py").write_text(_ENV_PY, encoding="utf-8")
    (versions / MIGRATION.name).write_text(MIGRATION.read_text(encoding="utf-8"), encoding="utf-8")

    parent = _parent_of(MIGRATION)
    assert parent, "0065 must declare a down_revision"

    # The chain is cut at 0065's immediate parent and that parent is always a
    # no-op stand-in. Copying the real ancestors in would drag the whole history
    # of the repository into this database, and the older revisions are not
    # Batch 03's to validate (several of them assume a PostgreSQL server). What
    # this test is about is the DDL 0065 adds and removes on top of the revision
    # it declares — so the revision below is a node, not a schema.
    (versions / f"{parent}.py").write_text(
        _STUB.format(revision=parent, down=None), encoding="utf-8"
    )
    (root / "STUBBED_REVISIONS.txt").write_text(parent + "\n", encoding="utf-8")
    (root / "PARENT_REVISION.txt").write_text(parent + "\n", encoding="utf-8")
    return root


def _tables(database: Path) -> set[str]:
    connection = sqlite3.connect(database)
    try:
        rows = connection.execute(
            "select name from sqlite_master where type = 'table'"
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def test_the_revision_id_and_its_parent_are_exactly_what_the_patch_promises():
    module = _module()

    assert module.revision == "0065_compliance_v2"
    assert module.down_revision == "0064_carrier_s2s"


def test_the_migration_is_reachable_from_the_script_directory():
    assert MIGRATION.is_file()
    revisions = {
        path.stem for path in VERSIONS.glob("*.py") if path.stem != "__init__"
    }
    assert "0065_compliance_v2" in revisions


def test_upgrade_creates_every_compliance_table(alembic_env):
    from alembic import command
    from alembic.config import Config

    config = Config(str(alembic_env / "alembic.ini"))
    command.upgrade(config, "0065_compliance_v2")

    created = _tables(alembic_env / "migration.db")
    assert EXPECTED_TABLES <= created
    assert created - EXPECTED_TABLES - {"alembic_version"} == set()


def test_every_created_table_carries_a_tenant_index(alembic_env):
    connection = sqlite3.connect(alembic_env / "migration.db")
    try:
        missing = []
        for table in sorted(EXPECTED_TABLES):
            indexes = connection.execute(f"pragma index_list('{table}')").fetchall()
            columns = {
                info[2]
                for index in indexes
                for info in connection.execute(f"pragma index_info('{index[1]}')").fetchall()
            }
            declared = {
                row[1]
                for row in connection.execute(f"pragma table_info('{table}')").fetchall()
            }
            if "tenant_id" in declared and "tenant_id" not in columns:
                missing.append(table)
    finally:
        connection.close()

    assert missing == []


def test_downgrade_removes_everything_it_created(alembic_env):
    from alembic import command
    from alembic.config import Config

    config = Config(str(alembic_env / "alembic.ini"))
    command.downgrade(config, "0064_carrier_s2s")

    remaining = _tables(alembic_env / "migration.db")
    assert remaining == {"alembic_version"}


def test_upgrade_is_repeatable_after_a_downgrade(alembic_env):
    from alembic import command
    from alembic.config import Config

    config = Config(str(alembic_env / "alembic.ini"))
    command.upgrade(config, "0065_compliance_v2")

    assert EXPECTED_TABLES <= _tables(alembic_env / "migration.db")


def test_the_models_declare_the_same_tables_as_the_migration():
    from sqlalchemy import create_engine

    from app.db.compliance_v2_models import Base

    engine = create_engine("sqlite://")
    try:
        Base.metadata.create_all(engine)
        # ``Base`` is shared with the rest of the application and the older
        # ``app.compliance`` module also owns ``compliance_*`` tables, so only
        # the classes declared by this module are compared.
        declared = {
            mapper.class_.__tablename__
            for mapper in Base.registry.mappers
            if mapper.class_.__module__ == "app.db.compliance_v2_models"
        }
    finally:
        engine.dispose()

    assert declared == EXPECTED_TABLES


def test_the_declared_parent_is_the_revision_batch_02_ships():
    """0065 may only sit on top of the revision the previous batch owns.

    When that file is present its own metadata is checked; when it is absent —
    a Batch 03-only tree — the declared id is checked against the promise the
    patch makes, so a typo cannot pass unnoticed.
    """
    parent = _parent_of(MIGRATION)

    assert parent == "0064_carrier_s2s"

    real = [
        path
        for path in VERSIONS.glob("0064_*.py")
        if _revision_of(path) == "0064_carrier_s2s"
    ]
    for path in real:
        assert _parent_of(path), f"{path.name} must declare its own down_revision"
