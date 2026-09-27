"""durable workflow execution

Replaces the in-memory _REGISTRY/_RUNS/_LAST_RUN_AT in
app/services/workflow_service.py with PostgreSQL-backed durable
workflows, versions, executions, checkpoints, and idempotency.

Revision: 0028
Down from: 0027_durable_jobs_outbox
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028_durable_workflow"
down_revision = "0027_durable_jobs_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # --- workflows ---
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(128), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_workflows_tenant_slug"),
    )
    op.create_index("ix_workflows_tenant", "workflows", ["tenant_id"])
    op.create_index("ix_workflows_status", "workflows", ["status"])

    # --- workflow_versions (immutable after publish) ---
    op.create_table(
        "workflow_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("graph_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("checksum", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("workflow_id", "version_number", name="uq_workflow_versions"),
    )
    op.create_index("ix_workflow_versions_workflow", "workflow_versions", ["workflow_id"])
    op.create_index("ix_workflow_versions_tenant", "workflow_versions", ["tenant_id"])

    # --- workflow_executions ---
    op.create_table(
        "workflow_executions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_version_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="created"),
        sa.Column("current_node_id", sa.String(128), nullable=True),
        sa.Column("input_payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("output_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error_metadata", sa.JSON(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checkpoint_data", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workflow_version_id"], ["workflow_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.Index("ix_workflow_executions_tenant_status", "tenant_id", "status"),
        sa.Index("ix_workflow_executions_workflow", "workflow_id"),
    )

    # --- execution_checkpoints ---
    op.create_table(
        "execution_checkpoints",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("checkpoint_data", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["execution_id"], ["workflow_executions.id"], ondelete="CASCADE"),
        sa.Index("ix_checkpoints_execution", "execution_id"),
    )

    # --- workflow_idempotency (execution creation dedup) ---
    op.create_table(
        "workflow_idempotency",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(256), nullable=False, server_default=""),
        sa.Column("result_ref", sa.String(128), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="in_progress"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "operation", "idempotency_key", name="uq_workflow_idempotency"),
        sa.Index("ix_workflow_idempotency_key", "tenant_id", "idempotency_key"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for tbl in ("workflow_idempotency", "execution_checkpoints", "workflow_executions", "workflow_versions", "workflows"):
        if tbl in inspector.get_table_names():
            op.drop_table(tbl)
