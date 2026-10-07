"""Durable Agent, AgentVersion, ChatSession, ChatMessage, TestSuite, TestCase, TestRun, EvaluationRule, and EvaluationResult tables with tenant/environment isolation.

Revision ID: 0038_agent_chat_conductor
Revises: 0038_retell_parity_foundation
Create Date: 2026-10-03 00:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect

# Offline SQL generation cannot introspect a database. Upgrade renders assume
# the objects are absent; downgrade renders assume they are present.
_OFFLINE_ASSUME_SCHEMA_EXISTS = False


revision = "0038_agent_chat_conductor"
down_revision = "0038_retell_parity_foundation"
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    if context.is_offline_mode():
        return _OFFLINE_ASSUME_SCHEMA_EXISTS
    return name in inspect(bind).get_table_names()


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    if context.is_offline_mode():
        return _OFFLINE_ASSUME_SCHEMA_EXISTS
    if not _table_exists(bind, table_name):
        return False
    return any(ix["name"] == index_name for ix in inspect(bind).get_indexes(table_name))


def upgrade() -> None:
    global _OFFLINE_ASSUME_SCHEMA_EXISTS
    _OFFLINE_ASSUME_SCHEMA_EXISTS = False
    bind = op.get_bind()

    agent_lifecycle_status = sa.Enum(
        "draft",
        "published",
        "archived",
        "retired",
        name="agentlifecyclestatus",
    )
    agent_validation_status = sa.Enum(
        "unvalidated",
        "valid",
        "invalid",
        name="agentvalidationstatus",
    )
    agent_version_status = sa.Enum(
        "draft",
        "published",
        "rolled_back",
        "retired",
        "archived",
        name="agentversionstatus",
    )

    if not _table_exists(bind, "agents"):
        op.create_table(
            "agents",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "environment_id",
                sa.Uuid(),
                sa.ForeignKey("environments.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("external_key", sa.String(length=128), nullable=False, server_default="default"),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("slug", sa.String(length=255), nullable=False, server_default="default-agent"),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("agent_type", sa.String(length=64), nullable=False, server_default="voice"),
            sa.Column("status", agent_lifecycle_status, nullable=False, server_default="draft"),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("active_version_id", sa.Uuid(), nullable=True),
            sa.Column("active_version_number", sa.Integer(), nullable=True),
            sa.Column("latest_version_number", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("published_environment", sa.String(length=64), nullable=False, server_default="development"),
            sa.Column("current_draft_config", sa.JSON(), nullable=False),
            sa.Column("builder_graph", sa.JSON(), nullable=False),
            sa.Column("prompt_config", sa.JSON(), nullable=False),
            sa.Column("voice_config", sa.JSON(), nullable=False),
            sa.Column("runtime_config", sa.JSON(), nullable=False),
            sa.Column("tool_ids", sa.JSON(), nullable=False),
            sa.Column("knowledge_base_ids", sa.JSON(), nullable=False),
            sa.Column("guardrails_config", sa.JSON(), nullable=False),
            sa.Column("escalation_policy", sa.JSON(), nullable=False),
            sa.Column("workflow_ids", sa.JSON(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("validation_status", agent_validation_status, nullable=False, server_default="unvalidated"),
            sa.Column("validation_errors", sa.JSON(), nullable=False),
            sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("draft_etag", sa.String(length=64), nullable=False, server_default=""),
            sa.Column(
                "created_by_user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "updated_by_user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_by", sa.String(length=255), nullable=False, server_default="system"),
            sa.Column("updated_by", sa.String(length=255), nullable=False, server_default="system"),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "external_key", name="uq_agents_tenant_external_key"),
        )

    if not _index_exists(bind, "agents", "ix_agents_tenant_id"):
        op.create_index("ix_agents_tenant_id", "agents", ["tenant_id"])
    if not _index_exists(bind, "agents", "ix_agents_environment_id"):
        op.create_index("ix_agents_environment_id", "agents", ["environment_id"])
    if not _index_exists(bind, "agents", "ix_agents_tenant_status"):
        op.create_index("ix_agents_tenant_status", "agents", ["tenant_id", "status"])
    if not _index_exists(bind, "agents", "ix_agents_tenant_env"):
        op.create_index("ix_agents_tenant_env", "agents", ["tenant_id", "environment_id"])
    if not _index_exists(bind, "agents", "ix_agents_tenant_updated"):
        op.create_index("ix_agents_tenant_updated", "agents", ["tenant_id", "updated_at"])

    if not _table_exists(bind, "agent_versions"):
        op.create_table(
            "agent_versions",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "agent_id",
                sa.Uuid(),
                sa.ForeignKey("agents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("version_label", sa.String(length=64), nullable=False, server_default="v1"),
            sa.Column("status", agent_version_status, nullable=False, server_default="published"),
            sa.Column("published_environment", sa.String(length=64), nullable=False, server_default="production"),
            sa.Column(
                "published_environment_id",
                sa.Uuid(),
                sa.ForeignKey("environments.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("config_snapshot", sa.JSON(), nullable=False),
            sa.Column("builder_snapshot", sa.JSON(), nullable=False),
            sa.Column("config_hash", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("changelog", sa.Text(), nullable=False, server_default=""),
            sa.Column("release_notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("source_version_id", sa.Uuid(), nullable=True),
            sa.Column("source_version_number", sa.Integer(), nullable=True),
            sa.Column("is_rollback", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column(
                "published_by_user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("published_by", sa.String(length=255), nullable=False, server_default="system"),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("agent_id", "version_number", name="uq_agent_versions_agent_version"),
        )

    if not _index_exists(bind, "agent_versions", "ix_agent_versions_tenant_id"):
        op.create_index("ix_agent_versions_tenant_id", "agent_versions", ["tenant_id"])
    if not _index_exists(bind, "agent_versions", "ix_agent_versions_agent_id"):
        op.create_index("ix_agent_versions_agent_id", "agent_versions", ["agent_id"])
    if not _index_exists(bind, "agent_versions", "ix_agent_versions_tenant_agent"):
        op.create_index(
            "ix_agent_versions_tenant_agent",
            "agent_versions",
            ["tenant_id", "agent_id", "version_number"],
        )
    if not _index_exists(bind, "agent_versions", "ix_agent_versions_agent_active"):
        op.create_index(
            "ix_agent_versions_agent_active",
            "agent_versions",
            ["agent_id", "is_active"],
        )

    # ----------------------------------------------------------- chat_sessions
    if not _table_exists(bind, "chat_sessions"):
        op.create_table(
            "chat_sessions",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "environment_id",
                sa.Uuid(),
                sa.ForeignKey("environments.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "chat_agent_id",
                sa.Uuid(),
                sa.ForeignKey("chat_agents.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("chat_agent_version", sa.Integer(), nullable=True),
            sa.Column(
                "contact_id",
                sa.Uuid(),
                sa.ForeignKey("contacts.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("channel", sa.String(length=24), nullable=False, server_default="web"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("dynamic_variables", sa.JSON(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    if not _index_exists(bind, "chat_sessions", "ix_chat_sessions_tenant_id"):
        op.create_index("ix_chat_sessions_tenant_id", "chat_sessions", ["tenant_id"])
    if not _index_exists(bind, "chat_sessions", "ix_chat_sessions_agent_id"):
        op.create_index("ix_chat_sessions_agent_id", "chat_sessions", ["chat_agent_id"])
    if not _index_exists(bind, "chat_sessions", "ix_chat_sessions_contact_id"):
        op.create_index("ix_chat_sessions_contact_id", "chat_sessions", ["contact_id"])
    if not _index_exists(bind, "chat_sessions", "ix_chat_sessions_tenant_status"):
        op.create_index("ix_chat_sessions_tenant_status", "chat_sessions", ["tenant_id", "status"])

    # ----------------------------------------------------------- chat_messages
    if not _table_exists(bind, "chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "session_id",
                sa.Uuid(),
                sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("sequence", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=16), nullable=False, server_default="user"),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column("tool_calls", sa.JSON(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("session_id", "sequence", name="uq_chat_messages_session_seq"),
        )

    if not _index_exists(bind, "chat_messages", "ix_chat_messages_tenant_id"):
        op.create_index("ix_chat_messages_tenant_id", "chat_messages", ["tenant_id"])
    if not _index_exists(bind, "chat_messages", "ix_chat_messages_session_id"):
        op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])
    if not _index_exists(bind, "chat_messages", "ix_chat_messages_session_seq"):
        op.create_index(
            "ix_chat_messages_session_seq",
            "chat_messages",
            ["tenant_id", "session_id", "sequence"],
        )

    # ------------------------------------------------------------- test_suites
    if not _table_exists(bind, "test_suites"):
        op.create_table(
            "test_suites",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "environment_id",
                sa.Uuid(),
                sa.ForeignKey("environments.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
            sa.Column("pass_policy", sa.String(length=32), nullable=False, server_default="all_passed"),
            sa.Column("min_pass_score", sa.Float(), nullable=False, server_default="100.0"),
            sa.Column(
                "created_by",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("tenant_id", "name", name="uq_test_suites_tenant_name"),
        )

    if not _index_exists(bind, "test_suites", "ix_test_suites_tenant_id"):
        op.create_index("ix_test_suites_tenant_id", "test_suites", ["tenant_id"])
    if not _index_exists(bind, "test_suites", "ix_test_suites_tenant_status"):
        op.create_index("ix_test_suites_tenant_status", "test_suites", ["tenant_id", "status"])

    # -------------------------------------------------------------- test_cases
    if not _table_exists(bind, "test_cases"):
        op.create_table(
            "test_cases",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "suite_id",
                sa.Uuid(),
                sa.ForeignKey("test_suites.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("agent_id", sa.String(length=128), nullable=False),
            sa.Column("agent_kind", sa.String(length=16), nullable=False, server_default="voice"),
            sa.Column("agent_version_id", sa.Uuid(), nullable=True),
            sa.Column("agent_version_number", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("mode", sa.String(length=24), nullable=False, server_default="simulation"),
            sa.Column("input_messages", sa.JSON(), nullable=False),
            sa.Column("dynamic_variables", sa.JSON(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("expected_rules", sa.JSON(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_by",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    if not _index_exists(bind, "test_cases", "ix_test_cases_tenant_id"):
        op.create_index("ix_test_cases_tenant_id", "test_cases", ["tenant_id"])
    if not _index_exists(bind, "test_cases", "ix_test_cases_suite_id"):
        op.create_index("ix_test_cases_suite_id", "test_cases", ["suite_id"])
    if not _index_exists(bind, "test_cases", "ix_test_cases_agent_version"):
        op.create_index(
            "ix_test_cases_agent_version",
            "test_cases",
            ["tenant_id", "agent_id", "agent_version_number"],
        )

    # --------------------------------------------------------------- test_runs
    if not _table_exists(bind, "test_runs"):
        op.create_table(
            "test_runs",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "environment_id",
                sa.Uuid(),
                sa.ForeignKey("environments.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "suite_id",
                sa.Uuid(),
                sa.ForeignKey("test_suites.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("batch_id", sa.String(length=64), nullable=True),
            sa.Column(
                "test_case_id",
                sa.Uuid(),
                sa.ForeignKey("test_cases.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("agent_id", sa.String(length=128), nullable=False),
            sa.Column("agent_kind", sa.String(length=16), nullable=False, server_default="voice"),
            sa.Column("agent_version_id", sa.Uuid(), nullable=True),
            sa.Column("agent_version_number", sa.Integer(), nullable=False),
            sa.Column("agent_config_hash", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("pinned_config_snapshot", sa.JSON(), nullable=False),
            sa.Column("mode", sa.String(length=24), nullable=False, server_default="simulation"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
            sa.Column("is_mock_provider", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("provider", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("model", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("correlation_id", sa.String(length=128), nullable=False, server_default=""),
            sa.Column(
                "call_id",
                sa.Uuid(),
                sa.ForeignKey("calls.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "chat_session_id",
                sa.Uuid(),
                sa.ForeignKey("chat_sessions.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("transcript_snapshot", sa.JSON(), nullable=False),
            sa.Column("events_snapshot", sa.JSON(), nullable=False),
            sa.Column("usage_metadata", sa.JSON(), nullable=False),
            sa.Column("latency_metadata", sa.JSON(), nullable=False),
            sa.Column("final_output", sa.JSON(), nullable=False),
            sa.Column("scorecard_summary", sa.JSON(), nullable=False),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column(
                "created_by",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    if not _index_exists(bind, "test_runs", "ix_test_runs_tenant_id"):
        op.create_index("ix_test_runs_tenant_id", "test_runs", ["tenant_id"])
    if not _index_exists(bind, "test_runs", "ix_test_runs_suite_id"):
        op.create_index("ix_test_runs_suite_id", "test_runs", ["suite_id"])
    if not _index_exists(bind, "test_runs", "ix_test_runs_batch_id"):
        op.create_index("ix_test_runs_batch_id", "test_runs", ["batch_id"])
    if not _index_exists(bind, "test_runs", "ix_test_runs_tenant_created"):
        op.create_index("ix_test_runs_tenant_created", "test_runs", ["tenant_id", "created_at"])
    if not _index_exists(bind, "test_runs", "ix_test_runs_agent_version"):
        op.create_index(
            "ix_test_runs_agent_version",
            "test_runs",
            ["tenant_id", "agent_id", "agent_version_number"],
        )

    # -------------------------------------------------------- evaluation_rules
    if not _table_exists(bind, "evaluation_rules"):
        op.create_table(
            "evaluation_rules",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "suite_id",
                sa.Uuid(),
                sa.ForeignKey("test_suites.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "test_case_id",
                sa.Uuid(),
                sa.ForeignKey("test_cases.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("rule_type", sa.String(length=48), nullable=False, server_default="contains"),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("evaluator_version", sa.String(length=32), nullable=False, server_default="v1.0"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column(
                "created_by",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    if not _index_exists(bind, "evaluation_rules", "ix_evaluation_rules_tenant_id"):
        op.create_index("ix_evaluation_rules_tenant_id", "evaluation_rules", ["tenant_id"])
    if not _index_exists(bind, "evaluation_rules", "ix_evaluation_rules_suite_id"):
        op.create_index("ix_evaluation_rules_suite_id", "evaluation_rules", ["suite_id"])
    if not _index_exists(bind, "evaluation_rules", "ix_evaluation_rules_case_id"):
        op.create_index("ix_evaluation_rules_case_id", "evaluation_rules", ["test_case_id"])

    # ------------------------------------------------------ evaluation_results
    if not _table_exists(bind, "evaluation_results"):
        op.create_table(
            "evaluation_results",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "test_run_id",
                sa.Uuid(),
                sa.ForeignKey("test_runs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "evaluation_rule_id",
                sa.Uuid(),
                sa.ForeignKey("evaluation_rules.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("rule_name", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("rule_type", sa.String(length=48), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="passed"),
            sa.Column("score", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
            sa.Column("evaluator_version", sa.String(length=32), nullable=False, server_default="v1.0"),
            sa.Column("formula_version", sa.String(length=64), nullable=False, server_default="weighted_v1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )

    if not _index_exists(bind, "evaluation_results", "ix_evaluation_results_tenant_id"):
        op.create_index("ix_evaluation_results_tenant_id", "evaluation_results", ["tenant_id"])
    if not _index_exists(bind, "evaluation_results", "ix_evaluation_results_run_id"):
        op.create_index(
            "ix_evaluation_results_run_id",
            "evaluation_results",
            ["tenant_id", "test_run_id"],
        )


def downgrade() -> None:
    global _OFFLINE_ASSUME_SCHEMA_EXISTS
    _OFFLINE_ASSUME_SCHEMA_EXISTS = True
    bind = op.get_bind()

    if _table_exists(bind, "evaluation_results"):
        for ix in (
            "ix_evaluation_results_run_id",
            "ix_evaluation_results_tenant_id",
        ):
            if _index_exists(bind, "evaluation_results", ix):
                op.drop_index(ix, table_name="evaluation_results")
        op.drop_table("evaluation_results")

    if _table_exists(bind, "evaluation_rules"):
        for ix in (
            "ix_evaluation_rules_case_id",
            "ix_evaluation_rules_suite_id",
            "ix_evaluation_rules_tenant_id",
        ):
            if _index_exists(bind, "evaluation_rules", ix):
                op.drop_index(ix, table_name="evaluation_rules")
        op.drop_table("evaluation_rules")

    if _table_exists(bind, "test_runs"):
        for ix in (
            "ix_test_runs_agent_version",
            "ix_test_runs_tenant_created",
            "ix_test_runs_batch_id",
            "ix_test_runs_suite_id",
            "ix_test_runs_tenant_id",
        ):
            if _index_exists(bind, "test_runs", ix):
                op.drop_index(ix, table_name="test_runs")
        op.drop_table("test_runs")

    if _table_exists(bind, "test_cases"):
        for ix in (
            "ix_test_cases_agent_version",
            "ix_test_cases_suite_id",
            "ix_test_cases_tenant_id",
        ):
            if _index_exists(bind, "test_cases", ix):
                op.drop_index(ix, table_name="test_cases")
        op.drop_table("test_cases")

    if _table_exists(bind, "test_suites"):
        for ix in (
            "ix_test_suites_tenant_status",
            "ix_test_suites_tenant_id",
        ):
            if _index_exists(bind, "test_suites", ix):
                op.drop_index(ix, table_name="test_suites")
        op.drop_table("test_suites")

    if _table_exists(bind, "chat_messages"):
        for ix in (
            "ix_chat_messages_session_seq",
            "ix_chat_messages_session_id",
            "ix_chat_messages_tenant_id",
        ):
            if _index_exists(bind, "chat_messages", ix):
                op.drop_index(ix, table_name="chat_messages")
        op.drop_table("chat_messages")

    if _table_exists(bind, "chat_sessions"):
        for ix in (
            "ix_chat_sessions_tenant_status",
            "ix_chat_sessions_contact_id",
            "ix_chat_sessions_agent_id",
            "ix_chat_sessions_tenant_id",
        ):
            if _index_exists(bind, "chat_sessions", ix):
                op.drop_index(ix, table_name="chat_sessions")
        op.drop_table("chat_sessions")

    if _table_exists(bind, "agent_versions"):
        for ix in (
            "ix_agent_versions_agent_active",
            "ix_agent_versions_tenant_agent",
            "ix_agent_versions_agent_id",
            "ix_agent_versions_tenant_id",
        ):
            if _index_exists(bind, "agent_versions", ix):
                op.drop_index(ix, table_name="agent_versions")
        op.drop_table("agent_versions")

    if _table_exists(bind, "agents"):
        for ix in (
            "ix_agents_tenant_updated",
            "ix_agents_tenant_env",
            "ix_agents_tenant_status",
            "ix_agents_environment_id",
            "ix_agents_tenant_id",
        ):
            if _index_exists(bind, "agents", ix):
                op.drop_index(ix, table_name="agents")
        op.drop_table("agents")

    # These enums are introduced only by this revision. PostgreSQL retains a
    # native enum after the last table using it is dropped, which makes a
    # subsequent upgrade after ``downgrade base`` fail with DuplicateObject.
    # Drop them after every dependent table has been removed; SQLite stores
    # these values as text and has no standalone enum type to drop.
    if bind.dialect.name == "postgresql":
        op.execute("DROP TYPE IF EXISTS agentversionstatus")
        op.execute("DROP TYPE IF EXISTS agentvalidationstatus")
        op.execute("DROP TYPE IF EXISTS agentlifecyclestatus")
