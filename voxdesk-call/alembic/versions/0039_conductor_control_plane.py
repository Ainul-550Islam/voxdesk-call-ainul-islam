"""Prompt 4: Durable Conductor AI Control Plane tables, constraints, and indexes.

Revision ID: 0039_conductor_control_plane
Revises: 0038_agent_chat_conductor
Create Date: 2026-10-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect

# Offline SQL generation cannot introspect a database. Upgrade renders assume
# the objects are absent; downgrade renders assume they are present.
_OFFLINE_ASSUME_SCHEMA_EXISTS = False


revision: str = "0039_conductor_control_plane"
down_revision: Union[str, None] = "0038_agent_chat_conductor"
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
    # 1. conductor_sessions
    if not _table_exists("conductor_sessions"):
        op.create_table(
            "conductor_sessions",
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
            sa.Column("agent_id", sa.String(length=80), nullable=False),
            sa.Column(
                "agent_kind",
                sa.String(length=24),
                nullable=False,
                server_default="voice",
            ),
            sa.Column("starting_agent_version_id", sa.UUID(), nullable=True),
            sa.Column(
                "starting_version_number",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column(
                "caller_user_id",
                sa.UUID(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "origin_surface",
                sa.String(length=64),
                nullable=False,
                server_default="agent_builder",
            ),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="ACTIVE",
            ),
            sa.Column("request_text", sa.Text(), nullable=False, server_default=""),
            sa.Column("context_policy", sa.JSON(), nullable=False),
            sa.Column("context_snapshot", sa.JSON(), nullable=False),
            sa.Column(
                "correlation_id",
                sa.String(length=96),
                nullable=False,
                server_default="",
            ),
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
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _index_exists("conductor_sessions", "ix_conductor_sessions_tenant_id"):
        op.create_index(
            "ix_conductor_sessions_tenant_id",
            "conductor_sessions",
            ["tenant_id"],
        )
    if not _index_exists("conductor_sessions", "ix_conductor_sessions_tenant_agent"):
        op.create_index(
            "ix_conductor_sessions_tenant_agent",
            "conductor_sessions",
            ["tenant_id", "agent_id"],
        )

    # 2. conductor_proposals
    if not _table_exists("conductor_proposals"):
        op.create_table(
            "conductor_proposals",
            sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
            sa.Column(
                "session_id",
                sa.UUID(),
                sa.ForeignKey("conductor_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
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
            sa.Column("agent_id", sa.String(length=80), nullable=False),
            sa.Column(
                "agent_kind",
                sa.String(length=24),
                nullable=False,
                server_default="voice",
            ),
            sa.Column("base_agent_version_id", sa.UUID(), nullable=True),
            sa.Column(
                "base_version_number",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column(
                "base_config_hash",
                sa.String(length=96),
                nullable=False,
                server_default="",
            ),
            sa.Column(
                "base_draft_etag",
                sa.String(length=96),
                nullable=False,
                server_default="",
            ),
            sa.Column("base_config_snapshot", sa.JSON(), nullable=False),
            sa.Column("request_text", sa.Text(), nullable=False, server_default=""),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "status",
                sa.String(length=32),
                nullable=False,
                server_default="PROPOSED",
            ),
            sa.Column(
                "validation_status",
                sa.String(length=32),
                nullable=False,
                server_default="not_run",
            ),
            sa.Column("validation_report", sa.JSON(), nullable=False),
            sa.Column(
                "simulation_status",
                sa.String(length=32),
                nullable=False,
                server_default="not_run",
            ),
            sa.Column("simulation_summary", sa.JSON(), nullable=False),
            sa.Column("risk_summary", sa.JSON(), nullable=False),
            sa.Column("candidate_config_snapshot", sa.JSON(), nullable=False),
            sa.Column(
                "final_candidate_hash",
                sa.String(length=96),
                nullable=False,
                server_default="",
            ),
            sa.Column("resulting_agent_version_id", sa.UUID(), nullable=True),
            sa.Column("resulting_version_number", sa.Integer(), nullable=True),
            sa.Column(
                "apply_idempotency_key", sa.String(length=128), nullable=True
            ),
            sa.Column(
                "approved_change_hash", sa.String(length=96), nullable=True
            ),
            sa.Column(
                "is_mock_provider",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
            sa.Column(
                "provider",
                sa.String(length=48),
                nullable=False,
                server_default="openai",
            ),
            sa.Column(
                "model",
                sa.String(length=96),
                nullable=False,
                server_default="gpt-4o-mini",
            ),
            sa.Column(
                "correlation_id",
                sa.String(length=96),
                nullable=False,
                server_default="",
            ),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column(
                "created_by",
                sa.UUID(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
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
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _index_exists("conductor_proposals", "ix_conductor_proposals_tenant_id"):
        op.create_index(
            "ix_conductor_proposals_tenant_id",
            "conductor_proposals",
            ["tenant_id"],
        )
    if not _index_exists("conductor_proposals", "ix_conductor_proposals_session_id"):
        op.create_index(
            "ix_conductor_proposals_session_id",
            "conductor_proposals",
            ["session_id"],
        )
    if not _index_exists("conductor_proposals", "ix_conductor_proposals_tenant_agent"):
        op.create_index(
            "ix_conductor_proposals_tenant_agent",
            "conductor_proposals",
            ["tenant_id", "agent_id"],
        )
    if not _index_exists("conductor_proposals", "ix_conductor_proposals_status"):
        op.create_index(
            "ix_conductor_proposals_status",
            "conductor_proposals",
            ["tenant_id", "status"],
        )

    # 3. conductor_changes
    if not _table_exists("conductor_changes"):
        op.create_table(
            "conductor_changes",
            sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
            sa.Column(
                "proposal_id",
                sa.UUID(),
                sa.ForeignKey("conductor_proposals.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "tenant_id",
                sa.UUID(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "section",
                sa.String(length=64),
                nullable=False,
                server_default="general",
            ),
            sa.Column("path", sa.String(length=200), nullable=False),
            sa.Column(
                "operation",
                sa.String(length=24),
                nullable=False,
                server_default="set",
            ),
            sa.Column("old_value", sa.JSON(), nullable=True),
            sa.Column("new_value", sa.JSON(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("evidence_ids", sa.JSON(), nullable=False),
            sa.Column(
                "risk_level",
                sa.String(length=24),
                nullable=False,
                server_default="low",
            ),
            sa.Column(
                "validation_state",
                sa.String(length=32),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("validation_messages", sa.JSON(), nullable=False),
            sa.Column(
                "simulation_state",
                sa.String(length=32),
                nullable=False,
                server_default="not_run",
            ),
            sa.Column(
                "approval_state",
                sa.String(length=24),
                nullable=False,
                server_default="pending",
            ),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
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
        )
    if not _index_exists("conductor_changes", "ix_conductor_changes_tenant_id"):
        op.create_index(
            "ix_conductor_changes_tenant_id",
            "conductor_changes",
            ["tenant_id"],
        )
    if not _index_exists("conductor_changes", "ix_conductor_changes_proposal_seq"):
        op.create_index(
            "ix_conductor_changes_proposal_seq",
            "conductor_changes",
            ["proposal_id", "sequence"],
        )

    # 4. conductor_approvals
    if not _table_exists("conductor_approvals"):
        op.create_table(
            "conductor_approvals",
            sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
            sa.Column(
                "proposal_id",
                sa.UUID(),
                sa.ForeignKey("conductor_proposals.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "change_id",
                sa.UUID(),
                sa.ForeignKey("conductor_changes.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "tenant_id",
                sa.UUID(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "actor_user_id",
                sa.UUID(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("action", sa.String(length=48), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "previous_state",
                sa.String(length=32),
                nullable=False,
                server_default="",
            ),
            sa.Column(
                "new_state",
                sa.String(length=32),
                nullable=False,
                server_default="",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    if not _index_exists("conductor_approvals", "ix_conductor_approvals_tenant_id"):
        op.create_index(
            "ix_conductor_approvals_tenant_id",
            "conductor_approvals",
            ["tenant_id"],
        )
    if not _index_exists("conductor_approvals", "ix_conductor_approvals_proposal_id"):
        op.create_index(
            "ix_conductor_approvals_proposal_id",
            "conductor_approvals",
            ["proposal_id", "created_at"],
        )

    # 5. conductor_evidence
    if not _table_exists("conductor_evidence"):
        op.create_table(
            "conductor_evidence",
            sa.Column("id", sa.UUID(), primary_key=True, nullable=False),
            sa.Column(
                "proposal_id",
                sa.UUID(),
                sa.ForeignKey("conductor_proposals.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "tenant_id",
                sa.UUID(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "source_type",
                sa.String(length=48),
                nullable=False,
                server_default="agent_version",
            ),
            sa.Column("source_id", sa.String(length=120), nullable=False),
            sa.Column(
                "evidence_summary",
                sa.Text(),
                nullable=False,
                server_default="",
            ),
            sa.Column("evidence_payload", sa.JSON(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
    if not _index_exists("conductor_evidence", "ix_conductor_evidence_tenant_id"):
        op.create_index(
            "ix_conductor_evidence_tenant_id",
            "conductor_evidence",
            ["tenant_id"],
        )
    if not _index_exists("conductor_evidence", "ix_conductor_evidence_proposal_id"):
        op.create_index(
            "ix_conductor_evidence_proposal_id",
            "conductor_evidence",
            ["proposal_id"],
        )
    if not _index_exists("conductor_evidence", "ix_conductor_evidence_source"):
        op.create_index(
            "ix_conductor_evidence_source",
            "conductor_evidence",
            ["tenant_id", "source_type", "source_id"],
        )


def downgrade() -> None:
    global _OFFLINE_ASSUME_SCHEMA_EXISTS
    _OFFLINE_ASSUME_SCHEMA_EXISTS = True
    if _table_exists("conductor_evidence"):
        for ix in (
            "ix_conductor_evidence_source",
            "ix_conductor_evidence_proposal_id",
            "ix_conductor_evidence_tenant_id",
        ):
            if _index_exists("conductor_evidence", ix):
                op.drop_index(ix, table_name="conductor_evidence")
        op.drop_table("conductor_evidence")

    if _table_exists("conductor_approvals"):
        for ix in (
            "ix_conductor_approvals_proposal_id",
            "ix_conductor_approvals_tenant_id",
        ):
            if _index_exists("conductor_approvals", ix):
                op.drop_index(ix, table_name="conductor_approvals")
        op.drop_table("conductor_approvals")

    if _table_exists("conductor_changes"):
        for ix in (
            "ix_conductor_changes_proposal_seq",
            "ix_conductor_changes_tenant_id",
        ):
            if _index_exists("conductor_changes", ix):
                op.drop_index(ix, table_name="conductor_changes")
        op.drop_table("conductor_changes")

    if _table_exists("conductor_proposals"):
        for ix in (
            "ix_conductor_proposals_status",
            "ix_conductor_proposals_tenant_agent",
            "ix_conductor_proposals_session_id",
            "ix_conductor_proposals_tenant_id",
        ):
            if _index_exists("conductor_proposals", ix):
                op.drop_index(ix, table_name="conductor_proposals")
        op.drop_table("conductor_proposals")

    if _table_exists("conductor_sessions"):
        for ix in (
            "ix_conductor_sessions_tenant_agent",
            "ix_conductor_sessions_tenant_id",
        ):
            if _index_exists("conductor_sessions", ix):
                op.drop_index(ix, table_name="conductor_sessions")
        op.drop_table("conductor_sessions")
