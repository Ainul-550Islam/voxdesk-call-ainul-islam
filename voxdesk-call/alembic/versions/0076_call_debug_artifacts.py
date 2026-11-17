"""Per-call debug captures (PART 13, P13-11).

Revision ID: 0076_call_debug_artifacts
Revises: 0075_messaging_registration
Create Date: 2026-11-10

The dropped `pcap_artifacts` table is *not* reused: it had no retention window,
so a "temporary" debug capture became permanent by default. This table makes the
expiry mandatory (`expires_at NOT NULL`), records who looked at the file and
when, and keeps the checksum so a downloaded capture can be proven to be the one
the gateway produced.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0076_call_debug_artifacts"
down_revision = "0075_messaging_registration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "call_debug_artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("call_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="pcap"),
        sa.Column("storage_key", sa.String(400), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("captured_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("purged_at", sa.DateTime(), nullable=True),
        sa.Column("last_accessed_by", sa.String(120), nullable=True),
        sa.Column("last_accessed_at", sa.DateTime(), nullable=True),
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_call_debug_artifacts_size"),
    )
    op.create_index(
        "ix_call_debug_artifacts_tenant_call", "call_debug_artifacts", ["tenant_id", "call_id"]
    )
    op.create_index(
        "ix_call_debug_artifacts_expires", "call_debug_artifacts", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_call_debug_artifacts_expires", table_name="call_debug_artifacts")
    op.drop_index("ix_call_debug_artifacts_tenant_call", table_name="call_debug_artifacts")
    op.drop_table("call_debug_artifacts")
