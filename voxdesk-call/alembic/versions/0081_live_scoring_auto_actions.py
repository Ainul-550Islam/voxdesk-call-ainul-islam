"""Live call scores and auto-action rules (PART 15, P15-02/03).

Revision ID: 0081_live_scoring_auto_actions
Revises: 0080_alert_rules
Create Date: 2026-11-11

Three tables.

`call_live_scores` stores the component sub-scores and the weights that produced
them, so a score of 41 is explainable after the tenant has changed its weights.

`auto_action_runs` is unique on `(tenant_id, call_id, rule_id, sequence)`. That
key *is* the loop guard: a second attempt at the same sequence cannot be inserted,
and `sequence` is what lets a rule with `max_per_call = 2` apply twice without
allowing an unbounded loop.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0081_live_scoring_auto_actions"
down_revision = "0080_alert_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "call_live_scores",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("call_id", UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", sa.String(120), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("components", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("weights", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("band", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("score BETWEEN 0 AND 100", name="ck_live_score_range"),
        sa.CheckConstraint("band IN ('ok','watch','critical')", name="ck_live_score_band"),
    )
    op.create_index(
        "ix_call_live_scores_call_created", "call_live_scores", ["call_id", "created_at"]
    )

    op.create_table(
        "auto_action_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("trigger", sa.String(32), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("keywords", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("action_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("scope_agent_id", sa.String(120), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("max_per_call", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_auto_action_rule_name"),
        sa.CheckConstraint(
            "trigger IN ('score_below','sentiment_below','keyword','loop_detected','latency_above')",
            name="ck_auto_action_trigger",
        ),
        sa.CheckConstraint(
            "action IN ('transfer','webhook','alert','end_call')", name="ck_auto_action_action"
        ),
        sa.CheckConstraint("max_per_call BETWEEN 1 AND 5", name="ck_auto_action_max"),
    )

    op.create_table(
        "auto_action_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("call_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "rule_id",
            UUID(as_uuid=True),
            sa.ForeignKey("auto_action_rules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "tenant_id", "call_id", "rule_id", "sequence", name="uq_auto_action_run"
        ),
        sa.CheckConstraint(
            "status IN ('pending','applied','skipped','failed')", name="ck_auto_action_run_status"
        ),
    )
    op.create_index("ix_auto_action_runs_call", "auto_action_runs", ["call_id"])


def downgrade() -> None:
    op.drop_index("ix_auto_action_runs_call", table_name="auto_action_runs")
    op.drop_table("auto_action_runs")
    op.drop_table("auto_action_rules")
    op.drop_index("ix_call_live_scores_call_created", table_name="call_live_scores")
    op.drop_table("call_live_scores")
