"""durable enterprise operations

Adds the PostgreSQL queue and delivery tables for jobs, action receipts,
notification attempts, preferences, webhook subscriptions and deliveries, and
email deliveries. Extends inbox thread state with a version and SLA clock.
Existing automations, automation runs, notifications and inbox rows are reused.

No row is copied onto another tenant. New environment columns stay nullable
where a job may be tenant-wide. Replay does not add an audit-enum value.

Revision ID: 0020_durable_enterprise_operations
Revises: 0019_environment_scope_business_resources
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_durable_enterprise_operations"
down_revision = "0019_environment_scope_business_resources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("replay_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("last_error_category", sa.String(64), nullable=False, server_default=""),
        sa.Column("last_error", sa.String(500), nullable=False, server_default=""),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_jobs_tenant_idempotency"),
    )
    op.create_index("ix_jobs_tenant_id", "jobs", ["tenant_id"])
    op.create_index("ix_jobs_claim", "jobs", ["status", "available_at"])
    op.create_index("ix_jobs_tenant_environment", "jobs", ["tenant_id", "environment_id"])

    op.create_table(
        "job_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_category", sa.String(64), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_job_attempts_number"),
    )
    op.create_index("ix_job_attempts_job", "job_attempts", ["job_id"])

    op.create_table(
        "job_idempotency",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="in_progress"),
        sa.Column("result_ref", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_job_idempotency_key"),
    )
    op.create_index("ix_job_idempotency_tenant_id", "job_idempotency", ["tenant_id"])

    op.create_table(
        "automation_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("automation_id", sa.String(64), nullable=False),
        sa.Column("business_event_id", sa.String(200), nullable=False),
        sa.Column("action_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="started"),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "tenant_id",
            "automation_id",
            "business_event_id",
            "action_id",
            name="uq_automation_action_once",
        ),
    )
    op.create_index("ix_automation_actions_tenant_id", "automation_actions", ["tenant_id"])
    op.create_index(
        "ix_automation_actions_tenant_environment",
        "automation_actions",
        ["tenant_id", "environment_id"],
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("notification_id", sa.String(64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("response_class", sa.String(32), nullable=False, server_default=""),
        sa.Column("error_category", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "notification_id", "attempt_number", name="uq_notification_delivery_attempt"
        ),
    )
    op.create_index(
        "ix_notification_deliveries_tenant_id", "notification_deliveries", ["tenant_id"]
    )
    op.create_index(
        "ix_notification_deliveries_notification_id", "notification_deliveries", ["notification_id"]
    )
    op.create_index(
        "ix_notification_deliveries_tenant_environment",
        "notification_deliveries",
        ["tenant_id", "environment_id"],
    )

    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sms_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("in_app_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("webhook_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("operational_alerts", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("security_alerts", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_notification_preferences_user"),
    )
    op.create_index(
        "ix_notification_preferences_tenant_id", "notification_preferences", ["tenant_id"]
    )

    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("endpoint", sa.String(500), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("event_types", sa.JSON(), nullable=False),
        sa.Column("secret_envelope", sa.Text(), nullable=False, server_default=""),
        sa.Column("secret_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_webhook_subscriptions_tenant_id", "webhook_subscriptions", ["tenant_id"])
    op.create_index(
        "ix_webhook_subscriptions_tenant_environment",
        "webhook_subscriptions",
        ["tenant_id", "environment_id"],
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_category", sa.String(64), nullable=False, server_default=""),
        sa.Column("replay_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["webhook_subscriptions.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("subscription_id", "event_id", name="uq_webhook_delivery_event"),
    )
    op.create_index("ix_webhook_deliveries_tenant_id", "webhook_deliveries", ["tenant_id"])
    op.create_index(
        "ix_webhook_deliveries_status", "webhook_deliveries", ["status", "next_attempt_at"]
    )

    op.create_table(
        "email_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("recipient_hash", sa.String(64), nullable=False),
        sa.Column("template_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_category", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_email_deliveries_tenant_id", "email_deliveries", ["tenant_id"])
    op.create_index("ix_email_deliveries_tenant", "email_deliveries", ["tenant_id", "status"])

    op.add_column(
        "inbox_thread_states",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "inbox_thread_states",
        sa.Column("first_response_deadline", sa.String(40), nullable=False, server_default=""),
    )
    op.add_column(
        "inbox_thread_states",
        sa.Column("resolution_deadline", sa.String(40), nullable=False, server_default=""),
    )
    op.add_column(
        "inbox_thread_states",
        sa.Column("sla_state", sa.String(16), nullable=False, server_default="running"),
    )
    op.add_column(
        "inbox_thread_states",
        sa.Column("sla_breached_at", sa.String(40), nullable=False, server_default=""),
    )
    op.add_column(
        "inbox_thread_states",
        sa.Column("sla_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("inbox_thread_states", "sla_paused")
    op.drop_column("inbox_thread_states", "sla_breached_at")
    op.drop_column("inbox_thread_states", "sla_state")
    op.drop_column("inbox_thread_states", "resolution_deadline")
    op.drop_column("inbox_thread_states", "first_response_deadline")
    op.drop_column("inbox_thread_states", "version")
    op.drop_index("ix_email_deliveries_tenant", table_name="email_deliveries")
    op.drop_index("ix_email_deliveries_tenant_id", table_name="email_deliveries")
    op.drop_table("email_deliveries")
    op.drop_index("ix_webhook_deliveries_status", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_tenant_id", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_webhook_subscriptions_tenant_environment", table_name="webhook_subscriptions")
    op.drop_index("ix_webhook_subscriptions_tenant_id", table_name="webhook_subscriptions")
    op.drop_table("webhook_subscriptions")
    op.drop_index("ix_notification_preferences_tenant_id", table_name="notification_preferences")
    op.drop_table("notification_preferences")
    op.drop_index(
        "ix_notification_deliveries_tenant_environment", table_name="notification_deliveries"
    )
    op.drop_index(
        "ix_notification_deliveries_notification_id", table_name="notification_deliveries"
    )
    op.drop_index("ix_notification_deliveries_tenant_id", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")
    op.drop_index("ix_automation_actions_tenant_environment", table_name="automation_actions")
    op.drop_index("ix_automation_actions_tenant_id", table_name="automation_actions")
    op.drop_table("automation_actions")
    op.drop_index("ix_job_idempotency_tenant_id", table_name="job_idempotency")
    op.drop_table("job_idempotency")
    op.drop_index("ix_job_attempts_job", table_name="job_attempts")
    op.drop_table("job_attempts")
    op.drop_index("ix_jobs_tenant_environment", table_name="jobs")
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.drop_index("ix_jobs_tenant_id", table_name="jobs")
    op.drop_table("jobs")
