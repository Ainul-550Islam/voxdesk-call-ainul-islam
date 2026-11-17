"""Request-a-call demo tables (PART 18, P18-03).

Revision ID: 0088_demo_call_requests
Revises: 0087_agency_client_links
Create Date: 2026-11-13

The spec's PART 18 tree lists `app/api/public_demo_routes.py` but no migration.
A public endpoint that dials a real telephone number needs durable state for two
reasons that cannot be solved in memory: the rate limits and the daily cost cap
must survive a restart (a process-local counter would reset every deploy, which
is exactly when an attacker would strike), and the consent text actually agreed
to must outlive the request that carried it.

`demo_destination_verifications.number` is unique: a number is verified once,
globally — the table is the allow-list of numbers the demo may dial.
`demo_call_requests` holds salted hashes of both the number and the IP so the
counting queries never need a cleartext index.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0088_demo_call_requests"
down_revision = "0087_agency_client_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demo_destination_verifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_demo_destination_verifications_number",
        "demo_destination_verifications",
        ["number"],
        unique=True,
    )

    op.create_table(
        "demo_call_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("number_hash", sa.String(length=64), nullable=False),
        sa.Column("ip_hash", sa.String(length=64), nullable=False),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("consent_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("consent_version", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("max_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("provider_ref", sa.String(length=255), nullable=True),
        sa.Column("failure_reason", sa.String(length=400), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_demo_call_requests_number_hash", "demo_call_requests", ["number_hash"])
    op.create_index("ix_demo_call_requests_ip_hash", "demo_call_requests", ["ip_hash"])
    op.create_index("ix_demo_call_requests_created_at", "demo_call_requests", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_demo_call_requests_created_at", table_name="demo_call_requests")
    op.drop_index("ix_demo_call_requests_ip_hash", table_name="demo_call_requests")
    op.drop_index("ix_demo_call_requests_number_hash", table_name="demo_call_requests")
    op.drop_table("demo_call_requests")
    op.drop_index(
        "ix_demo_destination_verifications_number",
        table_name="demo_destination_verifications",
    )
    op.drop_table("demo_destination_verifications")
