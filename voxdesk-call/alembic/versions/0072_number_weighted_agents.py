"""Weighted agent routing per phone number (PART 12, P12-23).

Revision ID: 0072_number_weighted_agents
Revises: 0071_flow_components
Create Date: 2026-11-10

Adds a `routing` JSON document to `phone_numbers` and **backfills it from the single binding** that
already exists, so a number that pointed at one agent now carries a one-entry weighted list. The
legacy columns stay: they are the read aliases the rest of the codebase (and every existing API
client) still uses.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0072_number_weighted_agents"
down_revision = "0071_flow_components"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("phone_numbers", sa.Column("routing", sa.JSON(), nullable=True))

    bind = op.get_bind()
    # `phone_numbers` has inbound/outbound agent columns; SMS routing had no column of its own
    # before this migration, so the sms channel starts empty rather than borrowing another one.
    rows = bind.execute(
        sa.text("SELECT id, inbound_agent_id, outbound_agent_id FROM phone_numbers")
    ).fetchall()
    for row_id, inbound, outbound in rows:
        routing = {
            channel: ([{"agent_id": str(agent_id), "weight": 1.0}] if agent_id else [])
            for channel, agent_id in (("inbound", inbound), ("outbound", outbound), ("sms", None))
        }
        if not any(routing.values()):
            continue
        bind.execute(
            sa.text("UPDATE phone_numbers SET routing = :payload WHERE id = :row_id"),
            {"payload": json.dumps(routing), "row_id": row_id},
        )


def downgrade() -> None:
    op.drop_column("phone_numbers", "routing")
