"""A2P 10DLC and toll-free registration (PART 13, P13-08).

Revision ID: 0075_messaging_registration
Revises: 0074_concurrency_country_policy
Create Date: 2026-11-10

One table, because the three registration kinds share a lifecycle and a
carrier reference: `messaging_registrations.kind` says which one, `.state` is
the state machine in `app.telephony.messaging_registration`, and `.payload`
keeps exactly what was submitted so a rejection can be explained months later.

No backfill: a tenant with no row has no registration, which correctly blocks
outbound SMS until they complete the wizard.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0075_messaging_registration"
down_revision = "0074_concurrency_country_policy"
branch_labels = None
depends_on = None

STATES = ("draft", "submitted", "in_review", "approved", "rejected", "suspended")
KINDS = ("a2p_brand", "a2p_campaign", "toll_free_verification")


def upgrade() -> None:
    op.create_table(
        "messaging_registrations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False, server_default="a2p_brand"),
        sa.Column("state", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("carrier_reference", sa.String(120), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("rejection_reason", sa.String(400), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "state IN (" + ",".join(f"'{state}'" for state in STATES) + ")",
            name="ck_messaging_registration_state",
        ),
        sa.CheckConstraint(
            "kind IN (" + ",".join(f"'{kind}'" for kind in KINDS) + ")",
            name="ck_messaging_registration_kind",
        ),
    )
    op.create_index(
        "ix_messaging_registrations_tenant_state",
        "messaging_registrations",
        ["tenant_id", "state"],
    )
    op.create_index(
        "ix_messaging_registrations_carrier_ref",
        "messaging_registrations",
        ["carrier_reference"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_messaging_registrations_carrier_ref", table_name="messaging_registrations"
    )
    op.drop_index(
        "ix_messaging_registrations_tenant_state", table_name="messaging_registrations"
    )
    op.drop_table("messaging_registrations")
