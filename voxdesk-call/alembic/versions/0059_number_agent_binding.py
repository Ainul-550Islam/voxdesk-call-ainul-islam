"""Add inbound/outbound agent binding columns on phone_numbers and calls.

Revision ID: 0059_number_agent_binding
Revises: 0058_call_latency_stats
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0059_number_agent_binding"
down_revision = "0058_call_latency_stats"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_column(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def _has_index(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(ix["name"] == index_name for ix in inspector.get_indexes(table_name))


def upgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "phone_numbers"):
        for col_name, col_def in [
            ("provider_sid", sa.Column("provider_sid", sa.String(80), nullable=False, server_default="")),
            ("friendly_name", sa.Column("friendly_name", sa.String(120), nullable=True)),
            ("country_code", sa.Column("country_code", sa.String(8), nullable=False, server_default="US")),
            ("number_type", sa.Column("number_type", sa.String(24), nullable=False, server_default="local")),
            ("is_primary", sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false"))),
            ("voice_url", sa.Column("voice_url", sa.String(500), nullable=True)),
            ("status_callback_url", sa.Column("status_callback_url", sa.String(500), nullable=True)),
            ("sms_url", sa.Column("sms_url", sa.String(500), nullable=True)),
            ("provisioned_at", sa.Column("provisioned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())),
            ("inbound_agent_id", sa.Column("inbound_agent_id", sa.Uuid(), nullable=True)),
            ("inbound_agent_version", sa.Column("inbound_agent_version", sa.Integer(), nullable=True)),
            ("outbound_agent_id", sa.Column("outbound_agent_id", sa.Uuid(), nullable=True)),
        ]:
            if not _has_column(bind, "phone_numbers", col_name):
                op.add_column("phone_numbers", col_def)
        if not _has_index(bind, "phone_numbers", "ix_phone_numbers_inbound_agent_id"):
            op.create_index(
                "ix_phone_numbers_inbound_agent_id",
                "phone_numbers",
                ["inbound_agent_id"],
            )
        if not _has_index(bind, "phone_numbers", "ix_phone_numbers_outbound_agent_id"):
            op.create_index(
                "ix_phone_numbers_outbound_agent_id",
                "phone_numbers",
                ["outbound_agent_id"],
            )

    if _has_table(bind, "recording_policies"):
        for col_name, col_def in [
            ("environment_id", sa.Column("environment_id", sa.Uuid(), nullable=True)),
            ("consent_mode", sa.Column("consent_mode", sa.String(24), nullable=False, server_default="one_party")),
            ("disclosure_text", sa.Column("disclosure_text", sa.Text(), nullable=False, server_default="This call may be recorded for quality and training purposes.")),
            ("raw_access_roles", sa.Column("raw_access_roles", sa.JSON(), nullable=False, server_default='["owner", "admin"]')),
            ("redact_pii", sa.Column("redact_pii", sa.Boolean(), nullable=False, server_default=sa.text("true"))),
        ]:
            if not _has_column(bind, "recording_policies", col_name):
                op.add_column("recording_policies", col_def)

    if _has_table(bind, "telephony_phone_numbers"):
        if not _has_column(bind, "telephony_phone_numbers", "inbound_agent_version"):
            op.add_column(
                "telephony_phone_numbers",
                sa.Column("inbound_agent_version", sa.Integer(), nullable=True),
            )

    if _has_table(bind, "calls"):
        if not _has_column(bind, "calls", "agent_id"):
            op.add_column(
                "calls",
                sa.Column("agent_id", sa.Uuid(), nullable=True),
            )
        if not _has_column(bind, "calls", "agent_version_id"):
            op.add_column(
                "calls",
                sa.Column("agent_version_id", sa.Uuid(), nullable=True),
            )
        if not _has_index(bind, "calls", "ix_calls_agent_id"):
            op.create_index("ix_calls_agent_id", "calls", ["agent_id"])
        if not _has_index(bind, "calls", "ix_calls_agent_version_id"):
            op.create_index("ix_calls_agent_version_id", "calls", ["agent_version_id"])


def downgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "calls"):
        if _has_index(bind, "calls", "ix_calls_agent_version_id"):
            op.drop_index("ix_calls_agent_version_id", table_name="calls")
        if _has_index(bind, "calls", "ix_calls_agent_id"):
            op.drop_index("ix_calls_agent_id", table_name="calls")
        if _has_column(bind, "calls", "agent_version_id"):
            op.drop_column("calls", "agent_version_id")
        if _has_column(bind, "calls", "agent_id"):
            op.drop_column("calls", "agent_id")

    if _has_table(bind, "telephony_phone_numbers"):
        if _has_column(bind, "telephony_phone_numbers", "inbound_agent_version"):
            op.drop_column("telephony_phone_numbers", "inbound_agent_version")

    if _has_table(bind, "recording_policies"):
        for col_name in (
            "redact_pii",
            "raw_access_roles",
            "disclosure_text",
            "consent_mode",
            "environment_id",
        ):
            if _has_column(bind, "recording_policies", col_name):
                op.drop_column("recording_policies", col_name)

    if _has_table(bind, "phone_numbers"):
        if _has_index(bind, "phone_numbers", "ix_phone_numbers_outbound_agent_id"):
            op.drop_index("ix_phone_numbers_outbound_agent_id", table_name="phone_numbers")
        if _has_index(bind, "phone_numbers", "ix_phone_numbers_inbound_agent_id"):
            op.drop_index("ix_phone_numbers_inbound_agent_id", table_name="phone_numbers")
        for col_name in (
            "outbound_agent_id",
            "inbound_agent_version",
            "inbound_agent_id",
            "provisioned_at",
            "sms_url",
            "status_callback_url",
            "voice_url",
            "is_primary",
            "number_type",
            "country_code",
            "friendly_name",
            "provider_sid",
        ):
            if _has_column(bind, "phone_numbers", col_name):
                op.drop_column("phone_numbers", col_name)
