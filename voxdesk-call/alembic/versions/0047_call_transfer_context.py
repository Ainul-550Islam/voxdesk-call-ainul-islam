"""Persist tenant-scoped warm-transfer context on calls.

Revision ID: 0047_call_transfer_context
Revises: 0046_callstatus_cancelled
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0047_call_transfer_context"
down_revision = "0046_callstatus_cancelled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "calls",
        sa.Column(
            "transfer_context",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("calls", "transfer_context")
