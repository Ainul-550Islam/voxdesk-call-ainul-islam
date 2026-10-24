"""Add live_proof_runs table for the live PSTN proof harness (Batch 01, Block B).

Revision ID: 0063_live_proof_runs
Revises: 0062_drop_pcap_artifacts
Create Date: 2026-10-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0063_live_proof_runs"
down_revision = "0062_drop_pcap_artifacts"
branch_labels = None
depends_on = None

_TABLE = "live_proof_runs"
_INDEXES = (
    ("ix_live_proof_runs_tenant_created", ["tenant_id", "created_at"]),
    ("ix_live_proof_runs_tenant_status", ["tenant_id", "status"]),
    ("ix_live_proof_run_call", ["call_id"]),
)


def _has_table(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_index(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(ix["name"] == index_name for ix in inspector.get_indexes(table_name))


def _create_table(bind) -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("provider_set", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("requested_to_number", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("call_sid", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column("turns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("e2e_p50_ms", sa.Float(), nullable=True),
        sa.Column("e2e_p95_ms", sa.Float(), nullable=True),
        sa.Column("e2e_p99_ms", sa.Float(), nullable=True),
        sa.Column("stt_ttfb_p50_ms", sa.Float(), nullable=True),
        sa.Column("llm_ttfb_p50_ms", sa.Float(), nullable=True),
        sa.Column("tts_ttfb_p50_ms", sa.Float(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("provider_fakes", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("evidence_path", sa.String(length=320), nullable=False, server_default=""),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("recording_sha256", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("requested_by", sa.Uuid(), nullable=True),
        sa.Column("error", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','unproven','failed','aborted')",
            name="ck_live_proof_run_status",
        ),
        sa.CheckConstraint("cost_usd >= 0", name="ck_live_proof_run_cost"),
        sa.CheckConstraint("turns >= 0", name="ck_live_proof_run_turns"),
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE):
        _create_table(bind)
    for index_name, columns in _INDEXES:
        if not _has_index(bind, _TABLE, index_name):
            op.create_index(index_name, _TABLE, columns)


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE):
        return
    for index_name, _columns in _INDEXES:
        if _has_index(bind, _TABLE, index_name):
            op.drop_index(index_name, table_name=_TABLE)
    op.drop_table(_TABLE)
