"""Saved call-history views (PART 15, P15-07).

Revision ID: 0085_call_saved_views
Revises: 0084_qa_analyst_metrics
Create Date: 2026-11-11

The spec's PART 15 lists five migrations. This sixth one exists because saved
views need a store and the schema had none: a filter set that lives only in a
browser cannot be shared, and "saved view" that is not shared is not the feature
the spec asked for. The uniqueness key is `(tenant, name)` -- two people saving
"Last week" must not silently shadow each other, they must rename.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0085_call_saved_views"
down_revision = "0084_qa_analyst_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "call_saved_views",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("columns", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_call_saved_view_name"),
    )
    op.create_index("ix_call_saved_views_tenant", "call_saved_views", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_call_saved_views_tenant", table_name="call_saved_views")
    op.drop_table("call_saved_views")
