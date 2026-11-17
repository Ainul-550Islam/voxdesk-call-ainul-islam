"""Agent folders and multiple drafts (PART 12, P12-25).

Revision ID: 0073_agent_folders_drafts
Revises: 0072_number_weighted_agents
Create Date: 2026-11-10

`agents.current_draft_config` stays exactly where it is; what changes is that a draft is no longer
implicit. `agent_drafts` gives an agent several named drafts, each with its own config document and
an optional folder, and `agents.folder_id` places the agent in the tree. The existing draft column
is left untouched so nothing that reads it has to change on the day this ships.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0073_agent_folders_drafts"
down_revision = "0072_number_weighted_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_folders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_folders.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "tenant_id", "parent_id", "name", name="uq_agent_folders_tenant_parent_name"
        ),
    )
    op.create_index("ix_agent_folders_tenant", "agent_folders", ["tenant_id"])

    op.add_column(
        "agents",
        sa.Column(
            "folder_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_folders.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.create_table(
        "agent_drafts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("label", sa.String(120), nullable=False, server_default="main"),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("agent_id", "label", name="uq_agent_drafts_agent_label"),
    )
    op.create_index("ix_agent_drafts_agent", "agent_drafts", ["agent_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_drafts_agent", table_name="agent_drafts")
    op.drop_table("agent_drafts")
    op.drop_column("agents", "folder_id")
    op.drop_index("ix_agent_folders_tenant", table_name="agent_folders")
    op.drop_table("agent_folders")
