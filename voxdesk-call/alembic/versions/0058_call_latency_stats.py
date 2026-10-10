"""Create call_latency_stats table for per-call voice pipeline latency telemetry (Sub-Phase 2A).

Revision ID: 0058_call_latency_stats
Revises: 0057_experiment_assignment
Create Date: 2026-10-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0058_call_latency_stats"
down_revision = "0057_experiment_assignment"
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return name in sa.inspect(bind).get_table_names()


def _column_exists(bind, table: str, column: str) -> bool:
    if not _table_exists(bind, table):
        return False
    return any(col["name"] == column for col in sa.inspect(bind).get_columns(table))


def _index_exists(bind, table: str, index_name: str) -> bool:
    if not _table_exists(bind, table):
        return False
    return any(idx["name"] == index_name for idx in sa.inspect(bind).get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "call_latency_stats"):
        op.create_table(
            "call_latency_stats",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "call_id",
                sa.Uuid(),
                sa.ForeignKey("calls.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("turn_idx", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("turns", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("stt_ms", sa.Float(), nullable=True),
            sa.Column("llm_ttfb_ms", sa.Float(), nullable=True),
            sa.Column("tts_ttfb_ms", sa.Float(), nullable=True),
            sa.Column("e2e_ms", sa.Float(), nullable=True),
            sa.Column("stt_ttfb_p50_ms", sa.Float(), nullable=True),
            sa.Column("stt_ttfb_p95_ms", sa.Float(), nullable=True),
            sa.Column("llm_ttfb_p50_ms", sa.Float(), nullable=True),
            sa.Column("llm_ttfb_p95_ms", sa.Float(), nullable=True),
            sa.Column("tts_ttfb_p50_ms", sa.Float(), nullable=True),
            sa.Column("tts_ttfb_p95_ms", sa.Float(), nullable=True),
            sa.Column("e2e_p50_ms", sa.Float(), nullable=True),
            sa.Column("e2e_p95_ms", sa.Float(), nullable=True),
            sa.Column("e2e_p99_ms", sa.Float(), nullable=True),
            sa.Column("e2e_max_ms", sa.Float(), nullable=True),
            sa.Column("interrupted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("interruptions", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    else:
        for col_name, col_type in [
            ("turn_idx", sa.Integer()),
            ("stt_ms", sa.Float()),
            ("llm_ttfb_ms", sa.Float()),
            ("tts_ttfb_ms", sa.Float()),
            ("e2e_ms", sa.Float()),
            ("e2e_max_ms", sa.Float()),
            ("interrupted", sa.Boolean()),
        ]:
            if not _column_exists(bind, "call_latency_stats", col_name):
                op.add_column("call_latency_stats", sa.Column(col_name, col_type, nullable=True))

    if not _index_exists(bind, "call_latency_stats", "ix_call_latency_stats_call_id"):
        op.create_index("ix_call_latency_stats_call_id", "call_latency_stats", ["call_id"])
    if not _index_exists(bind, "call_latency_stats", "ix_call_latency_stats_tenant_id"):
        op.create_index("ix_call_latency_stats_tenant_id", "call_latency_stats", ["tenant_id"])
    if not _index_exists(bind, "call_latency_stats", "ix_call_latency_stats_tenant_call"):
        op.create_index(
            "ix_call_latency_stats_tenant_call",
            "call_latency_stats",
            ["tenant_id", "call_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "call_latency_stats"):
        for idx_name in (
            "ix_call_latency_stats_tenant_call",
            "ix_call_latency_stats_tenant_id",
            "ix_call_latency_stats_call_id",
        ):
            if _index_exists(bind, "call_latency_stats", idx_name):
                op.drop_index(idx_name, table_name="call_latency_stats")
        op.drop_table("call_latency_stats")
