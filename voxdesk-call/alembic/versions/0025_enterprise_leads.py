"""enterprise lead lifecycle

Identity, status history, activities, tasks, segments, score snapshots,
consent, merges and enrichment requests. The ``leads`` table stays the
canonical person record. This revision does not add a Contact table and does
not rename ``LeadStatus``.

Revision ID: 0025_enterprise_leads
Revises: 0024_qa_conversation_intel
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_enterprise_leads"
down_revision = "0024_qa_conversation_intel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_identities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "environment_id",
            sa.Uuid(),
            sa.ForeignKey("environments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("phone_normalized", sa.String(20), nullable=True),
        sa.Column("email_normalized", sa.String(254), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("merged_into_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("lead_id", name="uq_lead_identities_lead"),
        sa.UniqueConstraint(
            "tenant_id", "environment_id", "phone_normalized", name="uq_lead_identity_phone"
        ),
        sa.UniqueConstraint(
            "tenant_id", "environment_id", "email_normalized", name="uq_lead_identity_email"
        ),
    )
    op.create_index("ix_lead_identities_scope", "lead_identities", ["tenant_id", "environment_id"])
    op.create_table(
        "lead_status_history",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "environment_id",
            sa.Uuid(),
            sa.ForeignKey("environments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("lead_id", "sequence", name="uq_lead_status_history_sequence"),
    )
    op.create_index(
        "ix_lead_status_history_lead",
        "lead_status_history",
        ["tenant_id", "environment_id", "lead_id"],
    )
    op.create_table(
        "lead_activities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "environment_id",
            sa.Uuid(),
            sa.ForeignKey("environments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("calls.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "appointment_id",
            sa.Uuid(),
            sa.ForeignKey("appointments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_lead_activities_lead",
        "lead_activities",
        ["tenant_id", "environment_id", "lead_id"],
    )
    op.create_table(
        "lead_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "environment_id",
            sa.Uuid(),
            sa.ForeignKey("environments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lead_tasks_scope", "lead_tasks", ["tenant_id", "environment_id", "status"])
    op.create_table(
        "lead_segments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "environment_id",
            sa.Uuid(),
            sa.ForeignKey("environments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "environment_id", "name", name="uq_lead_segments_name"),
    )
    op.create_index("ix_lead_segments_scope", "lead_segments", ["tenant_id", "environment_id"])
    op.create_table(
        "lead_score_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "environment_id",
            sa.Uuid(),
            sa.ForeignKey("environments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("factors", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_lead_score_snapshots_lead",
        "lead_score_snapshots",
        ["tenant_id", "lead_id", "created_at"],
    )
    op.create_table(
        "lead_consents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "environment_id",
            sa.Uuid(),
            sa.ForeignKey("environments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("lead_id", "channel", "version", name="uq_lead_consents_version"),
    )
    op.create_index(
        "ix_lead_consents_lead",
        "lead_consents",
        ["tenant_id", "environment_id", "lead_id"],
    )
    op.create_table(
        "lead_merges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "environment_id",
            sa.Uuid(),
            sa.ForeignKey("environments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "survivor_lead_id",
            sa.Uuid(),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "duplicate_lead_id",
            sa.Uuid(),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "duplicate_lead_id", name="uq_lead_merges_duplicate"),
    )
    op.create_index("ix_lead_merges_scope", "lead_merges", ["tenant_id", "environment_id"])
    op.create_table(
        "lead_enrichment_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("lead_id", sa.Uuid(), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "environment_id",
            sa.Uuid(),
            sa.ForeignKey("environments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("detail", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_lead_enrichment_lead", "lead_enrichment_records", ["tenant_id", "lead_id"])


def downgrade() -> None:
    op.drop_table("lead_enrichment_records")
    op.drop_table("lead_merges")
    op.drop_table("lead_consents")
    op.drop_table("lead_score_snapshots")
    op.drop_table("lead_segments")
    op.drop_table("lead_tasks")
    op.drop_table("lead_activities")
    op.drop_table("lead_status_history")
    op.drop_table("lead_identities")
