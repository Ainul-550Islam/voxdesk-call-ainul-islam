"""AI QA analyst runs (PART 15, P15-05).

Revision ID: 0084_qa_analyst_metrics
Revises: 0083_guardrail_categories
Create Date: 2026-11-11

One table. A run stores its metrics, its issue clusters and the metered units it
consumed, so the analyst's cost is attributable per run instead of appearing as
an unexplained line in a provider bill.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0084_qa_analyst_metrics"
down_revision = "0083_guardrail_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qa_analyst_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(120), nullable=True),
        sa.Column("calls_analysed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("clusters", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("metered_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("window_start", sa.DateTime(), nullable=True),
        sa.Column("window_end", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_qa_analyst_runs_tenant", "qa_analyst_runs", ["tenant_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_qa_analyst_runs_tenant", table_name="qa_analyst_runs")
    op.drop_table("qa_analyst_runs")
