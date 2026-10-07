"""Add queryable scope and correlation fields to the existing audit ledger.

Revision ID: 0042_audit_scope_and_redaction
Revises: 0041_telephony_voice_runtime
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0042_audit_scope_and_redaction"
down_revision = "0041_telephony_voice_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column("actor_type", sa.String(length=32), nullable=False, server_default="system"),
    )
    op.execute(sa.text("UPDATE audit_logs SET actor_type = 'human' WHERE actor_user_id IS NOT NULL"))
    op.add_column(
        "audit_logs",
        sa.Column("event_type", sa.String(length=128), nullable=False, server_default="governance_event"),
    )
    op.add_column("audit_logs", sa.Column("environment_id", sa.Uuid(), nullable=True))
    op.add_column("audit_logs", sa.Column("resource_type", sa.String(length=64), nullable=True))
    op.add_column("audit_logs", sa.Column("resource_id", sa.String(length=160), nullable=True))
    op.add_column("audit_logs", sa.Column("request_id", sa.String(length=64), nullable=True))
    op.add_column(
        "audit_logs",
        sa.Column("result", sa.String(length=24), nullable=False, server_default="success"),
    )
    op.execute(
        sa.text(
            "UPDATE audit_logs SET event_type = lower(CAST(action AS TEXT)) "
            "WHERE event_type = 'governance_event'"
        )
    )
    op.create_index(
        "ix_audit_tenant_environment_time",
        "audit_logs",
        ["tenant_id", "environment_id", "created_at"],
    )
    op.create_index(
        "ix_audit_tenant_event_time",
        "audit_logs",
        ["tenant_id", "event_type", "created_at"],
    )
    op.create_index(
        "ix_audit_tenant_resource",
        "audit_logs",
        ["tenant_id", "resource_type", "resource_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_tenant_resource", table_name="audit_logs")
    op.drop_index("ix_audit_tenant_event_time", table_name="audit_logs")
    op.drop_index("ix_audit_tenant_environment_time", table_name="audit_logs")
    op.drop_column("audit_logs", "result")
    op.drop_column("audit_logs", "request_id")
    op.drop_column("audit_logs", "resource_id")
    op.drop_column("audit_logs", "resource_type")
    op.drop_column("audit_logs", "environment_id")
    op.drop_column("audit_logs", "event_type")
    op.drop_column("audit_logs", "actor_type")
