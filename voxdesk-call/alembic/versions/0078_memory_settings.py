"""Memory settings and the running contact brief (PART 14, P14-05).

Revision ID: 0078_memory_settings
Revises: 0077_workflow_library
Create Date: 2026-11-11

Two tables. `memory_settings` is the tenant switchboard (save, recall, brief,
workspace prompt, retention). `contact_briefs` is the derived summary the agent
reads at the start of a call; the underlying facts stay in
`contact_memory_entries`, so clearing a brief never destroys a saved fact.

Retention is enforced by the existing purge job plus the `retention_days` value
here; nothing is deleted on write.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0078_memory_settings"
down_revision = "0077_workflow_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_settings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("save_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("recall_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("brief_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("workspace_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("retention_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("updated_by", sa.String(120), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "retention_days BETWEEN 1 AND 3650", name="ck_memory_retention_range"
        ),
    )

    op.create_table(
        "contact_briefs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("brief", sa.Text(), nullable=False, server_default=""),
        sa.Column("calls_behind", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("messages_behind", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("open_items", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("pii_redacted", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("edited_by", sa.String(120), nullable=True),
        sa.Column("generated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "contact_id", name="uq_contact_brief_contact"),
    )
    op.create_index("ix_contact_briefs_tenant", "contact_briefs", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_contact_briefs_tenant", table_name="contact_briefs")
    op.drop_table("contact_briefs")
    op.drop_table("memory_settings")
