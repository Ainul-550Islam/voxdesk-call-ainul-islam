"""Prompt 5: Public Widget Keys, Public Widget Sessions, and Public Contact Sales tables.

Revision ID: 0040_public_widget_keys
Revises: 0039_conductor_control_plane
Create Date: 2026-10-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect

# Offline SQL generation cannot introspect a database. Upgrade renders assume
# the objects are absent; downgrade renders assume they are present.
_OFFLINE_ASSUME_SCHEMA_EXISTS = False


revision: str = "0040_public_widget_keys"
down_revision: Union[str, None] = "0039_conductor_control_plane"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    if context.is_offline_mode():
        return _OFFLINE_ASSUME_SCHEMA_EXISTS
    bind = op.get_bind()
    return table_name in inspect(bind).get_table_names()


def _index_exists(table_name: str, index_name: str) -> bool:
    if context.is_offline_mode():
        return _OFFLINE_ASSUME_SCHEMA_EXISTS
    bind = op.get_bind()
    if not _table_exists(table_name):
        return False
    return any(ix["name"] == index_name for ix in inspect(bind).get_indexes(table_name))


def upgrade() -> None:
    global _OFFLINE_ASSUME_SCHEMA_EXISTS
    _OFFLINE_ASSUME_SCHEMA_EXISTS = False
    # 1. public_widget_keys
    if not _table_exists("public_widget_keys"):
        op.create_table(
            "public_widget_keys",
            sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.UUID(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "environment_id",
                sa.UUID(),
                sa.ForeignKey("environments.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "environment_name",
                sa.String(length=64),
                nullable=False,
                server_default="production",
            ),
            sa.Column("agent_id", sa.String(length=120), nullable=False),
            sa.Column(
                "agent_kind",
                sa.String(length=24),
                nullable=False,
                server_default="voice",
            ),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("key_prefix", sa.String(length=40), nullable=False),
            sa.Column("key_hash", sa.String(length=128), nullable=False),
            sa.Column(
                "status",
                sa.String(length=24),
                nullable=False,
                server_default="active",
            ),
            sa.Column("allowed_origins", sa.JSON(), nullable=False),
            sa.Column("allowed_capabilities", sa.JSON(), nullable=False),
            sa.Column(
                "rate_limit_per_minute",
                sa.Integer(),
                nullable=False,
                server_default="30",
            ),
            sa.Column(
                "session_ttl_seconds",
                sa.Integer(),
                nullable=False,
                server_default="900",
            ),
            sa.Column(
                "require_published_agent",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
            sa.Column("widget_config", sa.JSON(), nullable=False),
            sa.Column("rotated_from_key_id", sa.UUID(), nullable=True),
            sa.Column("rotated_to_key_id", sa.UUID(), nullable=True),
            sa.Column(
                "created_by",
                sa.UUID(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "revoked_by",
                sa.UUID(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("revoke_reason", sa.String(length=255), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_used_origin", sa.String(length=255), nullable=True),
            sa.Column("last_used_ip", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("key_hash", name="uq_public_widget_keys_hash"),
        )
    if not _index_exists("public_widget_keys", "ix_public_widget_keys_tenant_id"):
        op.create_index(
            "ix_public_widget_keys_tenant_id",
            "public_widget_keys",
            ["tenant_id"],
        )
    if not _index_exists("public_widget_keys", "ix_public_widget_keys_tenant_agent"):
        op.create_index(
            "ix_public_widget_keys_tenant_agent",
            "public_widget_keys",
            ["tenant_id", "agent_id", "status"],
        )
    if not _index_exists("public_widget_keys", "ix_public_widget_keys_prefix"):
        op.create_index(
            "ix_public_widget_keys_prefix",
            "public_widget_keys",
            ["key_prefix"],
        )

    # 2. public_widget_sessions
    if not _table_exists("public_widget_sessions"):
        op.create_table(
            "public_widget_sessions",
            sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.UUID(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "environment_id",
                sa.UUID(),
                sa.ForeignKey("environments.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "public_key_id",
                sa.UUID(),
                sa.ForeignKey("public_widget_keys.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("agent_id", sa.String(length=120), nullable=False),
            sa.Column(
                "agent_kind",
                sa.String(length=24),
                nullable=False,
                server_default="voice",
            ),
            sa.Column("agent_version_id", sa.UUID(), nullable=True),
            sa.Column(
                "agent_version_number",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column(
                "mode",
                sa.String(length=24),
                nullable=False,
                server_default="chat",
            ),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="ready",
            ),
            sa.Column("session_token_hash", sa.String(length=128), nullable=False),
            sa.Column("origin", sa.String(length=255), nullable=False),
            sa.Column("visitor_id", sa.String(length=120), nullable=True),
            sa.Column("client_ip", sa.String(length=64), nullable=True),
            sa.Column("user_agent", sa.String(length=512), nullable=True),
            sa.Column("chat_session_id", sa.UUID(), nullable=True),
            sa.Column("test_run_id", sa.UUID(), nullable=True),
            sa.Column(
                "transport",
                sa.String(length=32),
                nullable=False,
                server_default="http_chat",
            ),
            sa.Column(
                "turns_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "max_turns",
                sa.Integer(),
                nullable=False,
                server_default="30",
            ),
            sa.Column("transcript", sa.JSON(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "session_token_hash", name="uq_public_widget_sessions_token_hash"
            ),
        )
    if not _index_exists("public_widget_sessions", "ix_public_widget_sessions_tenant_id"):
        op.create_index(
            "ix_public_widget_sessions_tenant_id",
            "public_widget_sessions",
            ["tenant_id"],
        )
    if not _index_exists("public_widget_sessions", "ix_public_widget_sessions_key_id"):
        op.create_index(
            "ix_public_widget_sessions_key_id",
            "public_widget_sessions",
            ["public_key_id", "created_at"],
        )
    if not _index_exists("public_widget_sessions", "ix_public_widget_sessions_agent"):
        op.create_index(
            "ix_public_widget_sessions_agent",
            "public_widget_sessions",
            ["tenant_id", "agent_id", "status"],
        )

    # 3. public_contact_sales_requests
    if not _table_exists("public_contact_sales_requests"):
        op.create_table(
            "public_contact_sales_requests",
            sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
            sa.Column("full_name", sa.String(length=200), nullable=False),
            sa.Column("work_email", sa.String(length=254), nullable=False),
            sa.Column("company_name", sa.String(length=200), nullable=False),
            sa.Column(
                "job_title",
                sa.String(length=160),
                nullable=False,
                server_default="",
            ),
            sa.Column("phone_number", sa.String(length=40), nullable=True),
            sa.Column(
                "monthly_call_volume",
                sa.String(length=64),
                nullable=False,
                server_default="10k-50k",
            ),
            sa.Column(
                "primary_use_case",
                sa.String(length=120),
                nullable=False,
                server_default="voice_agents",
            ),
            sa.Column("message", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "source_path",
                sa.String(length=255),
                nullable=False,
                server_default="/contact-sales",
            ),
            sa.Column("origin", sa.String(length=255), nullable=True),
            sa.Column("client_ip", sa.String(length=64), nullable=True),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="received",
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if not _index_exists(
        "public_contact_sales_requests", "ix_public_contact_sales_email"
    ):
        op.create_index(
            "ix_public_contact_sales_email",
            "public_contact_sales_requests",
            ["work_email"],
        )
    if not _index_exists(
        "public_contact_sales_requests", "ix_public_contact_sales_created_at"
    ):
        op.create_index(
            "ix_public_contact_sales_created_at",
            "public_contact_sales_requests",
            ["created_at"],
        )


def downgrade() -> None:
    global _OFFLINE_ASSUME_SCHEMA_EXISTS
    _OFFLINE_ASSUME_SCHEMA_EXISTS = True
    if _table_exists("public_contact_sales_requests"):
        for ix in (
            "ix_public_contact_sales_created_at",
            "ix_public_contact_sales_email",
        ):
            if _index_exists("public_contact_sales_requests", ix):
                op.drop_index(ix, table_name="public_contact_sales_requests")
        op.drop_table("public_contact_sales_requests")

    if _table_exists("public_widget_sessions"):
        for ix in (
            "ix_public_widget_sessions_agent",
            "ix_public_widget_sessions_key_id",
            "ix_public_widget_sessions_tenant_id",
        ):
            if _index_exists("public_widget_sessions", ix):
                op.drop_index(ix, table_name="public_widget_sessions")
        op.drop_table("public_widget_sessions")

    if _table_exists("public_widget_keys"):
        for ix in (
            "ix_public_widget_keys_prefix",
            "ix_public_widget_keys_tenant_agent",
            "ix_public_widget_keys_tenant_id",
        ):
            if _index_exists("public_widget_keys", ix):
                op.drop_index(ix, table_name="public_widget_keys")
        op.drop_table("public_widget_keys")
