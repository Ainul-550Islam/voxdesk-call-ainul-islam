"""Create number_trust_profiles table for provider-sourced STIR/SHAKEN, Branded Caller ID (CNAM), spam status, and A2P 10DLC trust metadata (Part 7 / Gate G9).

Revision ID: 0061_number_trust_profile
Revises: 0060_agent_turn_settings
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0061_number_trust_profile"
down_revision = "0060_agent_turn_settings"
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
    if not _has_table(bind, "number_trust_profiles"):
        op.create_table(
            "number_trust_profiles",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("phone_number_id", sa.Uuid(), nullable=False, unique=True),
            sa.Column("e164", sa.String(length=32), nullable=False),
            sa.Column(
                "provider",
                sa.String(length=32),
                nullable=False,
                server_default="twilio",
            ),
            sa.Column("shaken_attestation", sa.String(length=16), nullable=True),
            sa.Column("branded_caller_name", sa.String(length=128), nullable=True),
            sa.Column(
                "spam_status",
                sa.String(length=32),
                nullable=False,
                server_default="unknown",
            ),
            sa.Column(
                "a2p_registration",
                sa.String(length=32),
                nullable=False,
                server_default="unregistered",
            ),
            sa.Column("provider_bundle_sid", sa.String(length=96), nullable=True),
            sa.Column("provider_payload", sa.JSON(), nullable=True),
            sa.Column("last_checked", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "shaken_attestation IS NULL OR shaken_attestation IN ('A', 'B', 'C', 'unverified')",
                name="ck_number_trust_profiles_attestation",
            ),
            sa.CheckConstraint(
                "spam_status IN ('clean', 'flagged', 'likely_spam', 'unknown')",
                name="ck_number_trust_profiles_spam_status",
            ),
            sa.CheckConstraint(
                "a2p_registration IN ('approved', 'pending', 'rejected', 'unregistered', 'not_applicable')",
                name="ck_number_trust_profiles_a2p",
            ),
        )

    if not _has_index(bind, "number_trust_profiles", "ix_number_trust_profiles_tenant_id"):
        op.create_index(
            "ix_number_trust_profiles_tenant_id",
            "number_trust_profiles",
            ["tenant_id"],
        )
    if not _has_index(bind, "number_trust_profiles", "ix_number_trust_profiles_phone_number_id"):
        op.create_index(
            "ix_number_trust_profiles_phone_number_id",
            "number_trust_profiles",
            ["phone_number_id"],
            unique=True,
        )
    if not _has_index(bind, "number_trust_profiles", "ix_number_trust_profiles_e164"):
        op.create_index(
            "ix_number_trust_profiles_e164",
            "number_trust_profiles",
            ["e164"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "number_trust_profiles"):
        for ix_name in (
            "ix_number_trust_profiles_e164",
            "ix_number_trust_profiles_phone_number_id",
            "ix_number_trust_profiles_tenant_id",
        ):
            if _has_index(bind, "number_trust_profiles", ix_name):
                op.drop_index(ix_name, table_name="number_trust_profiles")
        op.drop_table("number_trust_profiles")
