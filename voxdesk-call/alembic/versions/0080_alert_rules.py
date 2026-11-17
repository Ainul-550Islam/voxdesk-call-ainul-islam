"""User-defined alert rules and their events (PART 15, P15-04).

Revision ID: 0080_alert_rules
Revises: 0079_share_links_abuse
Create Date: 2026-11-11

Two tables. `alert_rules` is what the tenant's rule builder produced; `alert_events`
records every evaluation that breached a threshold -- including the ones a cooldown
suppressed, because a suppressed alert with no row is indistinguishable from a
monitoring gap. `(tenant_id, dedupe_key)` is unique, which is what makes delivery
exactly-once per window bucket.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0080_alert_rules"
down_revision = "0079_share_links_abuse"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_rules",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("window_minutes", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("comparator", sa.String(4), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("scope_kind", sa.String(16), nullable=False, server_default="tenant"),
        sa.Column("scope_id", sa.String(120), nullable=True),
        sa.Column("channels", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_alert_rule_name"),
        sa.CheckConstraint("comparator IN ('gt','lt','gte','lte')", name="ck_alert_comparator"),
        sa.CheckConstraint("scope_kind IN ('tenant','agent','number')", name="ck_alert_scope"),
        sa.CheckConstraint("window_minutes BETWEEN 1 AND 1440", name="ck_alert_window"),
        sa.CheckConstraint("cooldown_minutes BETWEEN 0 AND 1440", name="ck_alert_cooldown"),
    )
    op.create_index("ix_alert_rules_tenant_enabled", "alert_rules", ["tenant_id", "enabled"])

    op.create_table(
        "alert_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "rule_id",
            UUID(as_uuid=True),
            sa.ForeignKey("alert_rules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metric", sa.String(40), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("state", sa.String(28), nullable=False, server_default="fired"),
        sa.Column("dedupe_key", sa.String(120), nullable=False),
        sa.Column("deliveries", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_alert_event_dedupe"),
    )
    op.create_index("ix_alert_events_rule_created", "alert_events", ["rule_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_alert_events_rule_created", table_name="alert_events")
    op.drop_table("alert_events")
    op.drop_index("ix_alert_rules_tenant_enabled", table_name="alert_rules")
    op.drop_table("alert_rules")
