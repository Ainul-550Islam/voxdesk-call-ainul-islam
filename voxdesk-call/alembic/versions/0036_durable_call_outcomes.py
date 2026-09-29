"""Durable, reviewable call-outcome events.

Revision: 0036_durable_call_outcomes
Revises: 0035_enterprise_compliance_roi_deployment
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036_durable_call_outcomes"
down_revision = "0035_enterprise_compliance_roi_deployment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "call_outcome_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", sa.Uuid(), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("payload_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_call_outcome_idempotency"),
        sa.UniqueConstraint("tenant_id", "environment_id", "call_id", "event_version", name="uq_call_outcome_call_version"),
        sa.CheckConstraint("event_version >= 1", name="ck_call_outcome_event_version"),
        sa.CheckConstraint(
            "outcome IN ('resolved','unresolved','follow_up_required','handed_off')",
            name="ck_call_outcome_value",
        ),
        sa.CheckConstraint(
            "reason_code IN ('agent_completed_task','human_completed_task','unable_to_resolve',"
            "'caller_disconnected','provider_failure','follow_up_needed','external_dependency',"
            "'transfer_connected')",
            name="ck_call_outcome_reason",
        ),
    )
    op.create_index(
        "ix_call_outcome_call_created",
        "call_outcome_events",
        ["tenant_id", "environment_id", "call_id", "event_version"],
    )
    op.create_index(
        "ix_call_outcome_scope_created",
        "call_outcome_events",
        ["tenant_id", "organization_id", "environment_id", "created_at"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("ALTER TABLE call_outcome_events ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text("ALTER TABLE call_outcome_events FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text("""
            CREATE POLICY call_outcome_events_tenant_isolation
            ON call_outcome_events
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
        """))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("DROP POLICY IF EXISTS call_outcome_events_tenant_isolation ON call_outcome_events"))
        op.execute(sa.text("ALTER TABLE call_outcome_events DISABLE ROW LEVEL SECURITY"))
    op.drop_index("ix_call_outcome_scope_created", table_name="call_outcome_events")
    op.drop_index("ix_call_outcome_call_created", table_name="call_outcome_events")
    op.drop_table("call_outcome_events")
