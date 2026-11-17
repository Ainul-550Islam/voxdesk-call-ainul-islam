"""Workflow library installs and durable step runs (PART 14, P14-04).

Revision ID: 0077_workflow_library
Revises: 0076_call_debug_artifacts
Create Date: 2026-11-11

`workflow_installs` records what a tenant installed and who reviewed the
permissions; `workflow_step_runs` records every attempted step with an
idempotency key that is unique per tenant, which is what makes "retry" safe.

No backfill: an install row is created by the install endpoint, and the executor
has nothing to do until one exists.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0077_workflow_library"
down_revision = "0076_call_debug_artifacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_installs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workflow_key", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("allowed_actions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("permission_review", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("installed_by", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "workflow_key", name="uq_workflow_install_key"),
    )
    op.create_index(
        "ix_workflow_installs_tenant_enabled", "workflow_installs", ["tenant_id", "enabled"]
    )

    op.create_table(
        "workflow_step_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "install_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workflow_installs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("call_id", UUID(as_uuid=True), nullable=True),
        sa.Column("step", sa.String(80), nullable=False),
        sa.Column("phase", sa.String(16), nullable=False, server_default="post_call"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_preview", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_workflow_step_idempotency"),
        sa.CheckConstraint(
            "status IN ('pending','running','succeeded','failed','skipped')",
            name="ck_workflow_step_status",
        ),
        sa.CheckConstraint("phase IN ('pre_call','post_call')", name="ck_workflow_step_phase"),
    )
    op.create_index(
        "ix_workflow_step_runs_tenant_status", "workflow_step_runs", ["tenant_id", "status"]
    )
    op.create_index("ix_workflow_step_runs_call", "workflow_step_runs", ["call_id"])


def downgrade() -> None:
    op.drop_index("ix_workflow_step_runs_call", table_name="workflow_step_runs")
    op.drop_index("ix_workflow_step_runs_tenant_status", table_name="workflow_step_runs")
    op.drop_table("workflow_step_runs")
    op.drop_index("ix_workflow_installs_tenant_enabled", table_name="workflow_installs")
    op.drop_table("workflow_installs")
