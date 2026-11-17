"""Add per-channel weighted routing to telephony phone numbers.

Revision ID: 0090_telephony_phone_routing
Revises: 0089_voice_call_runtime_controls
Create Date: 2026-11-08

Revision 0072 adds routing to the legacy ``phone_numbers`` table. The live
telephony API uses ``telephony_phone_numbers`` as well, so this revision adds
the same JSON contract there and backfills the existing single-agent bindings.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0090_telephony_phone_routing"
down_revision = "0089_voice_call_runtime_controls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "telephony_phone_numbers",
        sa.Column("routing", sa.JSON(), nullable=True),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, inbound_agent_id, outbound_agent_id "
            "FROM telephony_phone_numbers"
        )
    ).fetchall()
    for row_id, inbound, outbound in rows:
        routing = {
            channel: ([{"agent_id": str(agent_id), "weight": 1.0}] if agent_id else [])
            for channel, agent_id in (
                ("inbound", inbound),
                ("outbound", outbound),
                ("sms", None),
            )
        }
        if any(routing.values()):
            bind.execute(
                sa.text(
                    "UPDATE telephony_phone_numbers "
                    "SET routing = :payload WHERE id = :row_id"
                ),
                {"payload": json.dumps(routing), "row_id": row_id},
            )


def downgrade() -> None:
    op.drop_column("telephony_phone_numbers", "routing")
