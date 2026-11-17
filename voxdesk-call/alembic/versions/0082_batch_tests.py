"""Batch test cases, runs and results (PART 15, P15-08).

Revision ID: 0082_batch_tests
Revises: 0081_live_scoring_auto_actions
Create Date: 2026-11-11

Cases are unique per `(tenant, agent, name)` so importing the same JSON twice
updates rather than duplicates. A run stores its own totals, so a completed run
stays readable even after its per-case results are pruned.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0082_batch_tests"
down_revision = "0081_live_scoring_auto_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "batch_test_cases",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(120), nullable=False),
        sa.Column("agent_version_number", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("expected_contains", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("forbidden_contains", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("max_latency_ms", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "agent_id", "name", name="uq_batch_case_name"),
    )
    op.create_index(
        "ix_batch_test_cases_agent", "batch_test_cases", ["tenant_id", "agent_id"]
    )

    op.create_table(
        "batch_test_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(120), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(120), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed','cancelled')",
            name="ck_batch_run_status",
        ),
    )

    op.create_table(
        "batch_test_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("batch_test_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_id", UUID(as_uuid=True), nullable=True),
        sa.Column("case_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reply", sa.Text(), nullable=False, server_default=""),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("failure_reason", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_batch_test_results_run", "batch_test_results", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_batch_test_results_run", table_name="batch_test_results")
    op.drop_table("batch_test_results")
    op.drop_table("batch_test_runs")
    op.drop_index("ix_batch_test_cases_agent", table_name="batch_test_cases")
    op.drop_table("batch_test_cases")
