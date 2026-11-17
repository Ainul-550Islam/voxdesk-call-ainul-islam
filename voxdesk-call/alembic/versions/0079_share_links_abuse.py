"""Share links and public abuse budgets (PART 14, P14-02/03).

Revision ID: 0079_share_links_abuse
Revises: 0078_memory_settings
Create Date: 2026-11-11

`share_links` stores only hashes of the token and the passcode, an expiry and a
usage cap; there is no plaintext secret column to leak. `public_abuse_budgets`
is the per-key per-day counter that a budget cap actually reads -- without a
counter, "capped" means "hopefully".

The captcha configuration itself lives on the existing
`public_widget_keys.widget_config` JSON: it is per-key configuration, not a new
entity, and adding a second table for three fields would let the two disagree.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0079_share_links_abuse"
down_revision = "0078_memory_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "share_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_id", sa.String(120), nullable=False),
        sa.Column("label", sa.String(120), nullable=False, server_default=""),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("passcode_hash", sa.String(128), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint("max_uses >= 1", name="ck_share_links_max_uses"),
        sa.CheckConstraint("used_count >= 0", name="ck_share_links_used"),
        sa.CheckConstraint("max_seconds BETWEEN 30 AND 3600", name="ck_share_links_seconds"),
    )
    op.create_index("ix_share_links_tenant_agent", "share_links", ["tenant_id", "agent_id"])
    op.create_index("ix_share_links_expires", "share_links", ["expires_at"])

    op.create_table(
        "public_abuse_budgets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "public_key_id",
            UUID(as_uuid=True),
            sa.ForeignKey("public_widget_keys.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("seconds_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("refused", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("public_key_id", "day", name="uq_abuse_budget_key_day"),
        sa.CheckConstraint("requests >= 0", name="ck_abuse_budget_requests"),
        sa.CheckConstraint("seconds_used >= 0", name="ck_abuse_budget_seconds"),
    )
    op.create_index("ix_public_abuse_budgets_day", "public_abuse_budgets", ["day"])


def downgrade() -> None:
    op.drop_index("ix_public_abuse_budgets_day", table_name="public_abuse_budgets")
    op.drop_table("public_abuse_budgets")
    op.drop_index("ix_share_links_expires", table_name="share_links")
    op.drop_index("ix_share_links_tenant_agent", table_name="share_links")
    op.drop_table("share_links")
