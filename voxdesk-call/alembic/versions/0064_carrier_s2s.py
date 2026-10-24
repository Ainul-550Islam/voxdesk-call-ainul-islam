"""Add Batch 02 carrier media tables: s2s_sessions and carrier_connection_checks.

Revision ID: 0064_carrier_s2s
Revises: 0063_live_proof_runs
Create Date: 2026-10-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0064_carrier_s2s"
down_revision = "0063_live_proof_runs"
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

    if not _has_table(bind, "s2s_sessions"):
        op.create_table(
            "s2s_sessions",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "environment_id",
                sa.Uuid(),
                sa.ForeignKey("environments.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "call_id",
                sa.Uuid(),
                sa.ForeignKey("calls.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("model", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
            sa.Column("failover_reason", sa.String(length=64), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column(
                "started_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("input_audio_s", sa.Float(), nullable=False, server_default="0"),
            sa.Column("output_audio_s", sa.Float(), nullable=False, server_default="0"),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_micro_usd", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_known", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
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
        )
    if not _has_index(bind, "s2s_sessions", "ix_s2s_sessions_tenant_id"):
        op.create_index("ix_s2s_sessions_tenant_id", "s2s_sessions", ["tenant_id"])
    if not _has_index(bind, "s2s_sessions", "ix_s2s_sessions_environment_id"):
        op.create_index(
            "ix_s2s_sessions_environment_id", "s2s_sessions", ["environment_id"]
        )
    if not _has_index(bind, "s2s_sessions", "ix_s2s_sessions_call_id"):
        op.create_index("ix_s2s_sessions_call_id", "s2s_sessions", ["call_id"])
    if not _has_index(bind, "s2s_sessions", "ix_s2s_sessions_provider"):
        op.create_index("ix_s2s_sessions_provider", "s2s_sessions", ["provider"])
    if not _has_index(bind, "s2s_sessions", "ix_s2s_sessions_status"):
        op.create_index("ix_s2s_sessions_status", "s2s_sessions", ["status"])
    if not _has_index(bind, "s2s_sessions", "ix_s2s_sessions_tenant_status"):
        op.create_index(
            "ix_s2s_sessions_tenant_status", "s2s_sessions", ["tenant_id", "status"]
        )
    if not _has_index(bind, "s2s_sessions", "ix_s2s_sessions_tenant_started"):
        op.create_index(
            "ix_s2s_sessions_tenant_started", "s2s_sessions", ["tenant_id", "started_at"]
        )

    if not _has_table(bind, "carrier_connection_checks"):
        op.create_table(
            "carrier_connection_checks",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "environment_id",
                sa.Uuid(),
                sa.ForeignKey("environments.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("carrier", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("reason", sa.String(length=64), nullable=True),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("idempotency_key_digest", sa.String(length=64), nullable=True),
            sa.Column(
                "checked_by_user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "checked_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "tenant_id",
                "carrier",
                "idempotency_key_digest",
                name="uq_carrier_connection_checks_tenant_carrier_key",
            ),
        )
    if not _has_index(bind, "carrier_connection_checks", "ix_carrier_connection_checks_tenant_id"):
        op.create_index(
            "ix_carrier_connection_checks_tenant_id",
            "carrier_connection_checks",
            ["tenant_id"],
        )
    if not _has_index(
        bind, "carrier_connection_checks", "ix_carrier_connection_checks_environment_id"
    ):
        op.create_index(
            "ix_carrier_connection_checks_environment_id",
            "carrier_connection_checks",
            ["environment_id"],
        )
    if not _has_index(bind, "carrier_connection_checks", "ix_carrier_connection_checks_carrier"):
        op.create_index(
            "ix_carrier_connection_checks_carrier", "carrier_connection_checks", ["carrier"]
        )
    if not _has_index(bind, "carrier_connection_checks", "ix_carrier_connection_checks_ok"):
        op.create_index(
            "ix_carrier_connection_checks_ok", "carrier_connection_checks", ["ok"]
        )
    if not _has_index(bind, "carrier_connection_checks", "ix_carrier_connection_checks_status"):
        op.create_index(
            "ix_carrier_connection_checks_status", "carrier_connection_checks", ["status"]
        )
    if not _has_index(bind, "carrier_connection_checks", "ix_carrier_connection_checks_checked_at"):
        op.create_index(
            "ix_carrier_connection_checks_checked_at",
            "carrier_connection_checks",
            ["checked_at"],
        )
    if not _has_index(
        bind, "carrier_connection_checks", "ix_carrier_connection_checks_tenant_checked"
    ):
        op.create_index(
            "ix_carrier_connection_checks_tenant_checked",
            "carrier_connection_checks",
            ["tenant_id", "checked_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "carrier_connection_checks"):
        op.drop_table("carrier_connection_checks")
    if _has_table(bind, "s2s_sessions"):
        op.drop_table("s2s_sessions")
