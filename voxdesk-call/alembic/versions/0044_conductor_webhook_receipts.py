"""Persist tenant-scoped Conductor webhook idempotency receipts.

Revision ID: 0044_conductor_webhook_receipts
Revises: 0043_environment_bound_api_keys
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0044_conductor_webhook_receipts"
down_revision = "0043_environment_bound_api_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conductor_webhook_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="fk_conductor_webhook_receipts_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["conductor_proposals.id"],
            name="fk_conductor_webhook_receipts_proposal_id_conductor_proposals",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["conductor_evidence.id"],
            name="fk_conductor_webhook_receipts_evidence_id_conductor_evidence",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conductor_webhook_receipts"),
        sa.UniqueConstraint(
            "tenant_id", "event_id",
            name="uq_conductor_webhook_receipt_tenant_event",
        ),
    )
    op.create_index(
        "ix_conductor_webhook_receipts_tenant_created",
        "conductor_webhook_receipts",
        ["tenant_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conductor_webhook_receipts_tenant_created",
        table_name="conductor_webhook_receipts",
    )
    op.drop_table("conductor_webhook_receipts")
