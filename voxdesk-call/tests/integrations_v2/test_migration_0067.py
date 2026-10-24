"""Tests Alembic migration 0067_integrations_v2 revision contract and full upgrade -> downgrade -> upgrade cycle."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_alembic_0067_revision_and_upgrade_downgrade_roundtrip(tmp_path):
    repo_root = Path(__file__).resolve().parents[2]
    mig_file = repo_root / "alembic" / "versions" / "0067_integrations_v2.py"
    assert mig_file.is_file(), f"Missing migration file: {mig_file}"

    spec = importlib.util.spec_from_file_location("mig_0067", mig_file)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "0067_integrations_v2"
    assert mod.down_revision == "0066_contact_center_v2"

    # Build an isolated Alembic script directory containing a base 0066 stub (tenants + users) + real 0067
    versions_dir = tmp_path / "alembic_env" / "versions"
    versions_dir.mkdir(parents=True)
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
    (versions_dir / "0066_contact_center_v2.py").write_text(
        '''revision = "0066_contact_center_v2"
down_revision = None
from alembic import op
import sqlalchemy as sa

def upgrade() -> None:
    op.create_table("tenants", sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True))
    op.create_table("users", sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True))

def downgrade() -> None:
    op.drop_table("users")
    op.drop_table("tenants")
''',
        encoding="utf-8",
    )
    (versions_dir / "0067_integrations_v2.py").write_text(
        mig_file.read_text(encoding="utf-8"), encoding="utf-8"
    )

    sqlite_path = tmp_path / "test_mig.db"
    db_url = f"sqlite:///{sqlite_path}"
    cfg = Config()
    cfg.set_main_option("script_location", str(tmp_path / "alembic_env"))
    cfg.set_main_option("sqlalchemy.url", db_url)

    expected_tables = {
        "int2_connections",
        "int2_oauth_states",
        "int2_field_mapping_profiles",
        "int2_field_mappings",
        "int2_sync_cursors",
        "int2_sync_runs",
        "int2_writeback_ledger",
        "int2_webhook_events",
        "int2_collab_routes",
        "int2_meetings",
        "int2_tool_enablements",
    }

    command.upgrade(cfg, "0067_integrations_v2")
    eng = create_engine(db_url)
    with eng.connect() as conn:
        present = set(inspect(conn).get_table_names())
        assert expected_tables <= present

    command.downgrade(cfg, "0066_contact_center_v2")
    with eng.connect() as conn:
        after_down = set(inspect(conn).get_table_names())
        assert expected_tables.isdisjoint(after_down)

    command.upgrade(cfg, "0067_integrations_v2")
    with eng.connect() as conn:
        reupgraded = set(inspect(conn).get_table_names())
        assert expected_tables <= reupgraded
    eng.dispose()
