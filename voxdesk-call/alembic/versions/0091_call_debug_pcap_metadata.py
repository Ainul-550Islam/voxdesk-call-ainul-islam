"""Persist SIP gateway PCAP completeness metadata.

Revision ID: 0091_call_debug_pcap_metadata
Revises: 0090_telephony_phone_routing
Create Date: 2026-11-08

Packet count and capture truncation are part of the evidence returned by the
SIP gateway. Keep them beside the stored artifact so later list/download views
and retention audits do not depend on the original POST response.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0091_call_debug_pcap_metadata"
down_revision = "0090_telephony_phone_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "call_debug_artifacts",
        sa.Column("packet_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "call_debug_artifacts",
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("call_debug_artifacts", "truncated")
    op.drop_column("call_debug_artifacts", "packet_count")
