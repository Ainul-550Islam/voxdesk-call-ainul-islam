"""Harden the durable workflow schema for locking, publication, and recovery.

Revision: 0031_workflow_persistence_hard
Revises: 0030_voice_runtime

Migration 0028 created the workflow tables. This revision adds the fields that
make the database state machine authoritative: current/published version
references, an optimistic concurrency counter, and a stale-heartbeat index.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# FIX: revision id shortened from the 38-80 char string
# "0031_workflow_persistence_hardening" (originally 35 chars) because Alembic's
# default alembic_version.version_num is VARCHAR(32) and
# PostgreSQL rejects the longer value with
# StringDataRightTruncationError, aborting `alembic upgrade head`.
# No deployed database can have recorded the old id: the write itself
# was impossible on Postgres, so renaming is safe.
revision = "0031_workflow_persistence_hard"
down_revision = "0030_voice_runtime"
branch_labels = None
depends_on = None

_WORKFLOW_TENANT_TABLES = (
    "workflows",
    "workflow_versions",
    "workflow_executions",
    "workflow_idempotency",
)


def _has_column(inspector: sa.Inspector, table: str, column: str) -> bool:
    return column in {item["name"] for item in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    required = set(_WORKFLOW_TENANT_TABLES) | {"execution_checkpoints", "users"}
    missing = required - tables
    if missing:
        raise RuntimeError(
            "workflow hardening requires migration 0028 tables; missing: "
            + ", ".join(sorted(missing))
        )

    workflow_columns = {item["name"] for item in inspector.get_columns("workflows")}
    if "current_version_id" not in workflow_columns:
        op.add_column("workflows", sa.Column("current_version_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            "fk_workflows_current_version",
            "workflows",
            "workflow_versions",
            ["current_version_id"],
            ["id"],
            ondelete="SET NULL",
        )
    if "published_version_id" not in workflow_columns:
        op.add_column("workflows", sa.Column("published_version_id", sa.Uuid(), nullable=True))
        op.create_foreign_key(
            "fk_workflows_published_version",
            "workflows",
            "workflow_versions",
            ["published_version_id"],
            ["id"],
            ondelete="SET NULL",
        )

    execution_columns = {item["name"] for item in inspector.get_columns("workflow_executions")}
    if "concurrency_version" not in execution_columns:
        op.add_column(
            "workflow_executions",
            sa.Column("concurrency_version", sa.Integer(), nullable=False, server_default="1"),
        )

    index_names = {item["name"] for item in inspector.get_indexes("workflow_executions")}
    if "ix_workflow_executions_recovery" not in index_names:
        op.create_index(
            "ix_workflow_executions_recovery",
            "workflow_executions",
            ["tenant_id", "status", "last_heartbeat_at"],
        )

    if bind.dialect.name == "postgresql":
        for table in _WORKFLOW_TENANT_TABLES:
            op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
            op.execute(
                sa.text(
                    f"CREATE POLICY {table}_tenant_isolation ON {table} "
                    "USING (tenant_id::text = current_setting('app.tenant_id', true)) "
                    "WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if bind.dialect.name == "postgresql":
        for table in _WORKFLOW_TENANT_TABLES:
            if table in tables:
                op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
                op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    if "workflow_executions" in tables:
        index_names = {item["name"] for item in inspector.get_indexes("workflow_executions")}
        if "ix_workflow_executions_recovery" in index_names:
            op.drop_index("ix_workflow_executions_recovery", table_name="workflow_executions")
        if _has_column(inspector, "workflow_executions", "concurrency_version"):
            op.drop_column("workflow_executions", "concurrency_version")

    if "workflows" in tables:
        columns = {item["name"] for item in inspector.get_columns("workflows")}
        foreign_keys = {item["name"] for item in inspector.get_foreign_keys("workflows")}
        if "fk_workflows_published_version" in foreign_keys:
            op.drop_constraint("fk_workflows_published_version", "workflows", type_="foreignkey")
        if "published_version_id" in columns:
            op.drop_column("workflows", "published_version_id")
        if "fk_workflows_current_version" in foreign_keys:
            op.drop_constraint("fk_workflows_current_version", "workflows", type_="foreignkey")
        if "current_version_id" in columns:
            op.drop_column("workflows", "current_version_id")
