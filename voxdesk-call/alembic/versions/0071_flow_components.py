"""Flow components and agent bindings (PART 12, P12-20).

Revision ID: 0071_flow_components
Revises: 0070_agent_voice_behavior
Create Date: 2026-11-10

The specification asked for `0064_flow_components.py`; that number is taken, so this is the next
free revision. Three tables, all tenant-scoped, all with the cascades that make deleting a tenant
or a component behave the way an operator expects.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0071_flow_components"
down_revision = "0070_agent_voice_behavior"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flow_components",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String(120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_flow_components_tenant_name"),
    )
    op.create_index("ix_flow_components_tenant", "flow_components", ["tenant_id"])

    op.create_table(
        "flow_component_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "component_id",
            UUID(as_uuid=True),
            sa.ForeignKey("flow_components.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("graph", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("changelog", sa.Text(), nullable=False, server_default=""),
        sa.Column("published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "component_id", "version", name="uq_flow_component_versions_component_version"
        ),
    )
    op.create_index(
        "ix_flow_component_versions_component", "flow_component_versions", ["component_id", "version"]
    )

    op.create_table(
        "agent_component_bindings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "environment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("environments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "component_id",
            UUID(as_uuid=True),
            sa.ForeignKey("flow_components.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sync_mode", sa.String(16), nullable=False, server_default="snapshot"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "agent_id", "component_id", name="uq_agent_component_bindings_agent_component"
        ),
    )
    op.create_index(
        "ix_agent_component_bindings_component", "agent_component_bindings", ["component_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_component_bindings_component", table_name="agent_component_bindings")
    op.drop_table("agent_component_bindings")
    op.drop_index("ix_flow_component_versions_component", table_name="flow_component_versions")
    op.drop_table("flow_component_versions")
    op.drop_index("ix_flow_components_tenant", table_name="flow_components")
    op.drop_table("flow_components")
