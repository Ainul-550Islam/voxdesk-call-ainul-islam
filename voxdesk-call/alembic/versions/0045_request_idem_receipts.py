"""Add durable, request-scoped idempotency receipts.

Revision ID: 0045_request_idem_receipts
Revises: 0044_conductor_webhook_receipts
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0045_request_idem_receipts"
down_revision = "0044_conductor_webhook_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "request_idempotency_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("environment_scope", sa.String(length=36), nullable=False, server_default="tenant"),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("key_digest", sa.String(length=64), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="in_progress"),
        sa.Column("resource_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("resource_id", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("error_category", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"],
            name="fk_request_idempotency_receipts_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "environment_id"], ["environments.tenant_id", "environments.id"],
            name="fk_request_idempotency_tenant_environment",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_request_idempotency_receipts"),
        sa.UniqueConstraint(
            "tenant_id", "environment_scope", "operation", "key_digest",
            name="uq_request_idempotency_scope_operation_key",
        ),
    )
    op.create_index(
        "ix_request_idempotency_tenant_status",
        "request_idempotency_receipts",
        ["tenant_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_request_idempotency_tenant_status",
        table_name="request_idempotency_receipts",
    )
    op.drop_table("request_idempotency_receipts")
