"""Unify SalesforceConnection with CrmIntegration and add durable OAuth PKCE state (Part 1F / Gate G1).

Revision ID: 0056_crm_connection_unify
Revises: 0055_batch_as_campaign
Create Date: 2026-10-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0056_crm_connection_unify"
down_revision = "0055_batch_as_campaign"
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
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE crmprovidertype ADD VALUE IF NOT EXISTS 'SALESFORCE'")

    if _table_exists(bind, "salesforce_connections"):
        with op.batch_alter_table("salesforce_connections") as batch_op:
            if not _column_exists(bind, "salesforce_connections", "crm_integration_id"):
                batch_op.add_column(
                    sa.Column(
                        "crm_integration_id",
                        postgresql.UUID(as_uuid=True),
                        nullable=True,
                    )
                )

    if not _table_exists(bind, "salesforce_oauth_states"):
        op.create_table(
            "salesforce_oauth_states",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "tenant_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("state", sa.String(128), nullable=False),
            sa.Column("code_verifier", sa.String(256), nullable=False, server_default=""),
            sa.Column("redirect_uri", sa.String(500), nullable=False, server_default=""),
            sa.Column("instance_url", sa.String(500), nullable=False, server_default=""),
            sa.Column(
                "scope",
                sa.String(500),
                nullable=False,
                server_default="api refresh_token openid",
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("state", name="uq_sf_oauth_state"),
        )
        op.create_index(
            "ix_sf_oauth_states_tenant",
            "salesforce_oauth_states",
            ["tenant_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "salesforce_oauth_states"):
        op.drop_index("ix_sf_oauth_states_tenant", table_name="salesforce_oauth_states")
        op.drop_table("salesforce_oauth_states")

    if _table_exists(bind, "salesforce_connections"):
        with op.batch_alter_table("salesforce_connections") as batch_op:
            if _column_exists(bind, "salesforce_connections", "crm_integration_id"):
                batch_op.drop_column("crm_integration_id")
