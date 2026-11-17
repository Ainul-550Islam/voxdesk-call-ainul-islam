"""Per-agent guardrail policies and block events (PART 15, P15-09).

Revision ID: 0083_guardrail_categories
Revises: 0082_batch_tests
Create Date: 2026-11-11

`guardrail_policies` holds the per-agent category switches and the hard-stop
phrase; `agent_id = '*'` is the tenant default that a specific agent overrides.

`guardrail_events` stores a hash of the blocked text and the detector's reason,
never the text itself. An audit trail of harmful output must not become a second
copy of that output.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0083_guardrail_categories"
down_revision = "0082_batch_tests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guardrail_policies",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(120), nullable=False, server_default="*"),
        sa.Column("enabled_categories", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("block_jailbreak", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "block_prompt_extraction", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "hard_stop_phrase",
            sa.Text(),
            nullable=False,
            server_default="I can't help with that, but I can connect you to a person.",
        ),
        sa.Column("judge_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "agent_id", name="uq_guardrail_policy_agent"),
    )

    op.create_table(
        "guardrail_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("call_id", UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", sa.String(120), nullable=True),
        sa.Column("stage", sa.String(16), nullable=False, server_default="input"),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("category", sa.String(40), nullable=True),
        sa.Column("text_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_guardrail_events_tenant_created", "guardrail_events", ["tenant_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_guardrail_events_tenant_created", table_name="guardrail_events")
    op.drop_table("guardrail_events")
    op.drop_table("guardrail_policies")
