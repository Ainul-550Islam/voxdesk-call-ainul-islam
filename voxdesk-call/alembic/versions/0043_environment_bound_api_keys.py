"""Bind machine API keys to an optional tenant-owned environment.

Revision ID: 0043_environment_bound_api_keys
Revises: 0042_audit_scope_and_redaction
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0043_environment_bound_api_keys"
down_revision = "0042_audit_scope_and_redaction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.add_column(sa.Column("environment_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key(
            "fk_api_keys_tenant_environment",
            "environments",
            ["tenant_id", "environment_id"],
            ["tenant_id", "id"],
            ondelete="CASCADE",
        )
        batch_op.create_index(
            "ix_api_keys_tenant_environment_active",
            ["tenant_id", "environment_id", "revoked_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_index("ix_api_keys_tenant_environment_active")
        batch_op.drop_constraint("fk_api_keys_tenant_environment", type_="foreignkey")
        batch_op.drop_column("environment_id")
