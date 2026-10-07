"""Durable checkpoints for canonical call post-processing.

Revision ID: 0051_post_call_pipeline
Revises: 0050_unify_webhooks

Downgrade removes new checkpoints, not existing calls, transcripts or usage.
Already queued POST_CALL jobs require stopping/draining the new worker before
running older code; schema rollback cannot undo an external model invocation.
"""
from alembic import op
import sqlalchemy as sa

revision = "0051_post_call_pipeline"
down_revision = "0050_unify_webhooks"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "post_call_step_runs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("pipeline_version", sa.Integer(), nullable=False),
        sa.Column("step", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(64), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("telemetry", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("call_id", "step", "pipeline_version", name="uq_post_call_step_version"),
        sa.ForeignKeyConstraint(["tenant_id", "environment_id"], ["environments.tenant_id", "environments.id"], name="fk_post_call_step_environment"),
        sa.CheckConstraint("attempts >= 0 AND pipeline_version >= 1", name="ck_post_call_step_counts"),
        sa.CheckConstraint("status IN ('running','completed','failed','blocked','not_configured','unsupported')", name="ck_post_call_step_status"),
    )
    op.create_index("ix_post_call_step_scope", "post_call_step_runs", ["tenant_id", "environment_id", "call_id"])


def downgrade():
    op.drop_index("ix_post_call_step_scope", table_name="post_call_step_runs")
    op.drop_table("post_call_step_runs")
