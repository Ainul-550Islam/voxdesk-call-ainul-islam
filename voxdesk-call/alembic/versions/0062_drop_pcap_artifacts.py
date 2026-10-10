"""Drop pcap_artifacts table and indexes (Part 8 / Gate G9 — closes W-10 PcapArtifact).

Revision ID: 0062_drop_pcap_artifacts
Revises: 0061_number_trust_profile
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0062_drop_pcap_artifacts"
down_revision = "0061_number_trust_profile"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_index(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(ix["name"] == index_name for ix in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "pcap_artifacts"):
        for ix_name in ("ix_pcap_call", "ix_pcap_tenant"):
            if _has_index(bind, "pcap_artifacts", ix_name):
                op.drop_index(ix_name, table_name="pcap_artifacts")
        op.drop_table("pcap_artifacts")


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "pcap_artifacts"):
        op.create_table(
            "pcap_artifacts",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("call_id", sa.Uuid(), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False, server_default="twilio"),
            sa.Column("capture_type", sa.String(length=32), nullable=False, server_default="sip"),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("storage_key", sa.String(length=240), nullable=False, server_default=""),
            sa.Column("checksum", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("retention_deadline", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_index(bind, "pcap_artifacts", "ix_pcap_tenant"):
        op.create_index("ix_pcap_tenant", "pcap_artifacts", ["tenant_id"])
    if not _has_index(bind, "pcap_artifacts", "ix_pcap_call"):
        op.create_index("ix_pcap_call", "pcap_artifacts", ["call_id"])
