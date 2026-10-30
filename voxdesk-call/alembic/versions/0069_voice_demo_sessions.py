"""Durable public voice-demo sessions.

Revision ID: 0069_voice_demo_sessions
Revises: 0068_platform_v2
Create Date: 2026-10-29

Adds ``voice_demo_sessions`` so public marketing voice-demo interactions persist
across API worker restarts, enforce HMAC session tokens and per-IP rate limits,
and expire cleanly via background scheduler reaping.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0069_voice_demo_sessions"
down_revision = "0068_platform_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_demo_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("agent_version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("agent_name", sa.String(160), nullable=False, server_default=""),
        sa.Column("language", sa.String(32), nullable=False, server_default="en-US"),
        sa.Column("voice_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column("client_ip", sa.String(64), nullable=False, server_default="unknown"),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_turns", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("transcript", sa.JSON(), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("meta", sa.JSON(), nullable=False),
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_voice_demo_sessions_expires_at",
        "voice_demo_sessions",
        ["expires_at"],
    )
    op.create_index(
        "ix_voice_demo_sessions_client_ip",
        "voice_demo_sessions",
        ["client_ip", "created_at"],
    )
    op.create_index(
        "ix_voice_demo_sessions_status",
        "voice_demo_sessions",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_voice_demo_sessions_status", table_name="voice_demo_sessions")
    op.drop_index("ix_voice_demo_sessions_client_ip", table_name="voice_demo_sessions")
    op.drop_index("ix_voice_demo_sessions_expires_at", table_name="voice_demo_sessions")
    op.drop_table("voice_demo_sessions")
