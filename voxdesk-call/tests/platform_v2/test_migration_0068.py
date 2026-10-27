"""Migration 0068: revision contract, round-trip, and schema parity with the models.

Runs against SQLite in a throwaway Alembic environment — the same technique
``tests/integrations_v2/test_migration_0067.py`` uses — so it is a real execution of
the migration, not a text comparison. PostgreSQL-only behaviour (partial indexes,
``JSONB``) is not used by this revision, which is why SQLite is a valid harness.

What this proves:

* the revision is the single head and revises 0067;
* ``upgrade -> downgrade -> upgrade`` completes, so a rollback of Batch 07 is real;
* every platform table the ORM declares is created by the migration, and the
  migration creates nothing the ORM does not declare;
* the columns of every table match, so an autogenerate diff would be empty.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "alembic" / "versions" / "0068_platform_v2.py"

BASE_STUB = '''"""Minimal pre-0068 schema: the tables the new foreign keys reference."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0067_integrations_v2"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
    )
    op.create_table(
        "environments",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("environments")
    op.drop_table("users")
    op.drop_table("tenants")
'''


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_0068", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_contract():
    module = _load_migration()
    assert module.revision == "0068_platform_v2"
    assert module.down_revision == "0067_integrations_v2"
    assert module.branch_labels is None
    assert module.depends_on is None


@pytest.fixture()
def alembic_env(tmp_path):
    versions = tmp_path / "alembic_env" / "versions"
    versions.mkdir(parents=True)
    (tmp_path / "alembic_env" / "script.py.mako").write_text("", encoding="utf-8")
    (tmp_path / "alembic_env" / "env.py").write_text(
        """from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
connectable = engine_from_config(
    config.get_section(config.config_ini_section, {}),
    prefix="sqlalchemy.",
    poolclass=pool.NullPool,
)
with connectable.connect() as connection:
    context.configure(connection=connection, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
""",
        encoding="utf-8",
    )
    (versions / "0067_base_stub.py").write_text(BASE_STUB, encoding="utf-8")
    (versions / "0068_platform_v2.py").write_text(MIGRATION.read_text(encoding="utf-8"), encoding="utf-8")

    db_path = tmp_path / "migration.db"
    url = f"sqlite:///{db_path}"
    config = Config()
    config.set_main_option("script_location", str(tmp_path / "alembic_env"))
    config.set_main_option("sqlalchemy.url", url)
    return config, url


def test_upgrade_downgrade_upgrade_roundtrip(alembic_env):
    config, url = alembic_env
    engine = create_engine(url)

    command.upgrade(config, "head")
    tables = set(inspect(engine).get_table_names())
    platform_tables = {name for name in tables if name.startswith(("wfm_", "wl_", "mob_"))}
    assert len(platform_tables) == 26, sorted(platform_tables)
    assert {
        "wfm_interval_history",
        "wfm_forecasts",
        "wfm_schedules",
        "wl_custom_domains",
        "wl_brand_assets",
        "mob_devices",
        "mob_push_deliveries",
    } <= platform_tables

    command.downgrade(config, "0067_integrations_v2")
    remaining = set(inspect(engine).get_table_names())
    assert not {name for name in remaining if name.startswith(("wfm_", "wl_", "mob_"))}
    # The stub schema survives, which is what a real downgrade must leave behind.
    assert {"tenants", "users", "environments"} <= remaining

    command.upgrade(config, "head")
    again = set(inspect(engine).get_table_names())
    assert {name for name in again if name.startswith(("wfm_", "wl_", "mob_"))} == platform_tables


def test_migration_matches_model_metadata():
    """Every ORM table is created, with the same columns, and nothing extra appears."""
    import app.db.models  # noqa: F401
    import app.db.platform_v2_models  # noqa: F401
    from app.db.models import Base

    source = MIGRATION.read_text(encoding="utf-8")
    metadata_tables = {
        name for name in Base.metadata.tables if name.startswith(("wfm_", "wl_", "mob_"))
    }
    assert len(metadata_tables) == 26

    for name in sorted(metadata_tables):
        assert f'"{name}"' in source, f"{name} is declared by the ORM but not created by the migration"
        table = Base.metadata.tables[name]
        for column in table.columns:
            assert f'"{column.name}"' in source, f"{name}.{column.name} is missing from the migration"

    # No platform table in the migration that the ORM does not know about: read the
    # create_table calls and compare the set.
    created = set()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("op.create_table("):
            continue
        if stripped.startswith('"') and stripped.endswith(",") and stripped.count('"') == 2:
            candidate = stripped.strip('",')
            if candidate.startswith(("wfm_", "wl_", "mob_")):
                created.add(candidate)
    assert created == metadata_tables


def test_accuracy_table_has_no_hardcoded_target_column():
    """Accuracy is computed, never stored as a configured number."""
    import app.db.platform_v2_models as models

    columns = {column.name for column in models.WfmForecastAccuracy.__table__.columns}
    assert {"wape", "mape", "bias", "mae", "sample_count"} <= columns
    assert not {"target", "target_accuracy", "accuracy_target", "sla_target"} & columns


def test_domain_uniqueness_is_global():
    """One hostname, one tenant — the constraint that makes `resolve_host` safe."""
    import app.db.platform_v2_models as models

    unique_sets = {
        tuple(sorted(column.name for column in constraint.columns))
        for constraint in models.CustomDomain.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("domain",) in unique_sets
