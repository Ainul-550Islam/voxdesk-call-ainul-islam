"""Add Call/TelephonyCallSession experiment assignment columns and experiment_call_outcomes table (Part 1G / Gate G1).

Revision ID: 0057_experiment_assignment
Revises: 0056_crm_connection_unify
Create Date: 2026-10-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0057_experiment_assignment"
down_revision = "0056_crm_connection_unify"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(c["name"] == column_name for c in inspector.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "calls"):
        with op.batch_alter_table("calls") as batch_op:
            if not _column_exists(bind, "calls", "experiment_id"):
                batch_op.add_column(
                    sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=True)
                )
                batch_op.create_index("ix_calls_experiment_id", ["experiment_id"])
            if not _column_exists(bind, "calls", "variant_id"):
                batch_op.add_column(
                    sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=True)
                )
                batch_op.create_index("ix_calls_variant_id", ["variant_id"])

    if _table_exists(bind, "telephony_call_sessions"):
        with op.batch_alter_table("telephony_call_sessions") as batch_op:
            if not _column_exists(bind, "telephony_call_sessions", "experiment_id"):
                batch_op.add_column(
                    sa.Column("experiment_id", postgresql.UUID(as_uuid=True), nullable=True)
                )
                batch_op.create_index(
                    "ix_telephony_call_sessions_experiment_id", ["experiment_id"]
                )
            if not _column_exists(bind, "telephony_call_sessions", "variant_id"):
                batch_op.add_column(
                    sa.Column("variant_id", postgresql.UUID(as_uuid=True), nullable=True)
                )
                batch_op.create_index(
                    "ix_telephony_call_sessions_variant_id", ["variant_id"]
                )

    if not _table_exists(bind, "experiment_call_outcomes"):
        op.create_table(
            "experiment_call_outcomes",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "tenant_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "experiment_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("experiments.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "variant_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("experiment_variants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("call_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("call_sid", sa.String(96), nullable=False, server_default=""),
            sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("csat", sa.Float(), nullable=True),
            sa.Column("cost", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("experiment_id", "call_id", name="uq_exp_call_outcome"),
        )
        op.create_index(
            "ix_exp_call_outcomes_variant",
            "experiment_call_outcomes",
            ["experiment_id", "variant_id"],
        )
        op.create_index(
            "ix_exp_call_outcomes_tenant",
            "experiment_call_outcomes",
            ["tenant_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "experiment_call_outcomes"):
        op.drop_index("ix_exp_call_outcomes_tenant", table_name="experiment_call_outcomes")
        op.drop_index("ix_exp_call_outcomes_variant", table_name="experiment_call_outcomes")
        op.drop_table("experiment_call_outcomes")

    if _table_exists(bind, "telephony_call_sessions"):
        with op.batch_alter_table("telephony_call_sessions") as batch_op:
            if _column_exists(bind, "telephony_call_sessions", "variant_id"):
                batch_op.drop_index("ix_telephony_call_sessions_variant_id")
                batch_op.drop_column("variant_id")
            if _column_exists(bind, "telephony_call_sessions", "experiment_id"):
                batch_op.drop_index("ix_telephony_call_sessions_experiment_id")
                batch_op.drop_column("experiment_id")

    if _table_exists(bind, "calls"):
        with op.batch_alter_table("calls") as batch_op:
            if _column_exists(bind, "calls", "variant_id"):
                batch_op.drop_index("ix_calls_variant_id")
                batch_op.drop_column("variant_id")
            if _column_exists(bind, "calls", "experiment_id"):
                batch_op.drop_index("ix_calls_experiment_id")
                batch_op.drop_column("experiment_id")
