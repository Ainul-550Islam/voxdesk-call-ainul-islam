"""Add truthful provider-confirmed cancellation status for legacy calls.

Revision ID: 0046_callstatus_cancelled
Revises: 0045_request_idem_receipts
"""
from __future__ import annotations

from alembic import op

revision = "0046_callstatus_cancelled"
down_revision = "0045_request_idem_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # PostgreSQL enum additions require an autocommit transaction. This is
    # additive and does not rewrite or delete existing call rows.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE callstatus ADD VALUE IF NOT EXISTS 'CANCELLED'")


def downgrade() -> None:
    # PostgreSQL cannot remove one enum value in place. Keeping the additive
    # value is safer than rebuilding callstatus and risking data loss.
    return None
