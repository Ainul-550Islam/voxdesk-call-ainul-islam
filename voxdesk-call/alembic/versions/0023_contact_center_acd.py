"""contact center acd

Queues, skills, presence and routing decisions. Call, User, Tenant and the
inbox assignee stay where they are. This revision does not copy them.

Revision ID: 0023_contact_center_acd
Revises: 0022_telephony_media_platform
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023_contact_center_acd"
down_revision = "0022_telephony_media_platform"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cc_queues",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("required_skills", sa.JSON(), nullable=False),
        sa.Column("overflow_policy", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "environment_id", "name", name="uq_cc_queues_name"),
        sa.CheckConstraint("priority >= 0", name="ck_cc_queues_priority"),
        sa.CheckConstraint("max_concurrency >= 1", name="ck_cc_queues_concurrency"),
    )
    op.create_index("ix_cc_queues_tenant", "cc_queues", ["tenant_id", "environment_id"])
    op.create_table(
        "cc_queue_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("queue_id", sa.Uuid(), sa.ForeignKey("cc_queues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("queue_id", "user_id", name="uq_cc_queue_members"),
    )
    op.create_index("ix_cc_queue_members_tenant", "cc_queue_members", ["tenant_id", "queue_id"])
    op.create_table(
        "cc_skills",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_cc_skills_name"),
    )
    op.create_index("ix_cc_skills_tenant_id", "cc_skills", ["tenant_id"])
    op.create_table(
        "cc_agent_skills",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("skill_id", sa.Uuid(), sa.ForeignKey("cc_skills.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("proficiency", sa.Integer(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("skill_id", "user_id", name="uq_cc_agent_skills"),
        sa.CheckConstraint(
            "proficiency >= 1 AND proficiency <= 5", name="ck_cc_agent_skills_proficiency"
        ),
    )
    op.create_index("ix_cc_agent_skills_user", "cc_agent_skills", ["tenant_id", "user_id"])
    op.create_table(
        "cc_agent_presence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("active_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_cc_agent_presence"),
        sa.CheckConstraint(
            "state IN ('offline', 'available', 'ringing', 'busy', 'wrap_up', 'away', 'paused', 'disabled')",
            name="ck_cc_agent_presence_state",
        ),
        sa.CheckConstraint("capacity >= 1", name="ck_cc_agent_presence_capacity"),
        sa.CheckConstraint("active_count >= 0", name="ck_cc_agent_presence_active"),
    )
    op.create_index("ix_cc_agent_presence_state", "cc_agent_presence", ["tenant_id", "state"])
    op.create_table(
        "cc_queue_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("queue_id", sa.Uuid(), sa.ForeignKey("cc_queues.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active_lock", sa.String(80), nullable=True),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.UniqueConstraint("active_lock", name="uq_cc_queue_entries_active"),
        sa.CheckConstraint(
            "status IN ('waiting', 'assigned', 'overflowed', 'abandoned', 'completed', 'failed')",
            name="ck_cc_queue_entries_status",
        ),
    )
    op.create_index("ix_cc_queue_entries_waiting", "cc_queue_entries", ["tenant_id", "queue_id", "status"])
    op.create_table(
        "cc_routing_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "entry_id",
            sa.Uuid(),
            sa.ForeignKey("cc_queue_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("queue_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("entry_lock", sa.String(40), nullable=True),
        sa.Column("agent_lock", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("entry_lock", name="uq_cc_routing_entry_lock"),
        sa.UniqueConstraint("agent_lock", name="uq_cc_routing_agent_lock"),
    )
    op.create_index("ix_cc_routing_assignments_tenant", "cc_routing_assignments", ["tenant_id", "status"])
    op.create_table(
        "cc_routing_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("entry_id", sa.Uuid(), nullable=True),
        sa.Column("queue_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("matched_skills", sa.JSON(), nullable=False),
        sa.Column("rejected", sa.JSON(), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_cc_routing_decisions_tenant", "cc_routing_decisions", ["tenant_id", "created_at"])
    op.create_table(
        "cc_routing_cursors",
        sa.Column("queue_id", sa.Uuid(), sa.ForeignKey("cc_queues.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("last_user_id", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("cc_routing_cursors")
    op.drop_index("ix_cc_routing_decisions_tenant", table_name="cc_routing_decisions")
    op.drop_table("cc_routing_decisions")
    op.drop_index("ix_cc_routing_assignments_tenant", table_name="cc_routing_assignments")
    op.drop_table("cc_routing_assignments")
    op.drop_index("ix_cc_queue_entries_waiting", table_name="cc_queue_entries")
    op.drop_table("cc_queue_entries")
    op.drop_index("ix_cc_agent_presence_state", table_name="cc_agent_presence")
    op.drop_table("cc_agent_presence")
    op.drop_index("ix_cc_agent_skills_user", table_name="cc_agent_skills")
    op.drop_table("cc_agent_skills")
    op.drop_index("ix_cc_skills_tenant_id", table_name="cc_skills")
    op.drop_table("cc_skills")
    op.drop_index("ix_cc_queue_members_tenant", table_name="cc_queue_members")
    op.drop_table("cc_queue_members")
    op.drop_index("ix_cc_queues_tenant", table_name="cc_queues")
    op.drop_table("cc_queues")
