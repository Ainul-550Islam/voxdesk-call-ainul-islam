"""Align deployed agent/MCP columns with the authoritative persisted models.

Preserves legacy columns and data for rolling deployments. No create_all or
model import is used: this revision is an explicit, reviewable schema change.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049_runtime_schema_alignment"
down_revision = "0048_boolean_defaults"
branch_labels = None
depends_on = None

LEGACY_AGENT_JSON = {
    "builder_graph": "{}", "prompt_config": "{}", "voice_config": "{}",
    "runtime_config": "{}", "tool_ids": "[]", "knowledge_base_ids": "[]",
    "guardrails_config": "{}", "escalation_policy": "{}", "workflow_ids": "[]",
    "metadata": "{}",
}
STATUS_COLUMNS = (
    ("agents", "status", "agentlifecyclestatus", "draft"),
    ("agents", "validation_status", "agentvalidationstatus", "unvalidated"),
    ("agent_versions", "status", "agentversionstatus", "published"),
)


def upgrade() -> None:
    op.add_column("agents", sa.Column("published_version_id", sa.Uuid(), nullable=True))
    op.add_column("agents", sa.Column("published_version_number", sa.Integer(), nullable=True))
    op.add_column("agents", sa.Column("archived_reason", sa.String(500), nullable=False, server_default=""))
    op.add_column("agents", sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.add_column("agent_versions", sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    # These retained historical columns aren't written by the current ORM.
    # Their defaults describe empty optional configuration, not remote success.
    for name, default in LEGACY_AGENT_JSON.items():
        op.alter_column("agents", name, server_default=sa.text(f"'{default}'"))
    op.alter_column("agent_versions", "builder_snapshot", server_default=sa.text("'{}'"))
    for table, column, enum_name, default in STATUS_COLUMNS:
        op.execute(sa.text(f'ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT'))
        op.execute(sa.text(f'ALTER TABLE {table} ALTER COLUMN {column} TYPE VARCHAR(24) USING {column}::text'))
        op.alter_column(table, column, server_default=sa.text(f"'{default}'"))
    op.execute(sa.text('UPDATE agents SET meta = metadata'))
    # Only an exact same-tenant, same-agent immutable version can be adopted.
    op.execute(sa.text("""
        UPDATE agents AS a
        SET published_version_id = v.id, published_version_number = v.version_number
        FROM agent_versions AS v
        WHERE a.active_version_id = v.id AND v.agent_id = a.id
          AND v.tenant_id = a.tenant_id AND v.status IN ('published', 'superseded')
    """))
    op.add_column("mcp_tools", sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=True))
    # The migration owner must backfill all tenants in this transactional DDL.
    # RLS is re-enabled and forced before the transaction can commit.
    op.execute(sa.text('ALTER TABLE mcp_tools DISABLE ROW LEVEL SECURITY'))
    op.execute(sa.text('UPDATE mcp_tools SET discovered_at = created_at'))
    op.alter_column("mcp_tools", "discovered_at", nullable=False, server_default=sa.func.now())
    op.execute(sa.text('ALTER TABLE mcp_tools ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text('ALTER TABLE mcp_tools FORCE ROW LEVEL SECURITY'))


def downgrade() -> None:
    op.drop_column("mcp_tools", "discovered_at")
    # A superseded immutable snapshot must not be relabelled or deleted to
    # make a downgrade pass. The old enum lacks this state, so refuse safely.
    op.execute(sa.text("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM agent_versions WHERE status = 'superseded') THEN
                RAISE EXCEPTION 'Cannot downgrade: superseded immutable agent versions exist';
            END IF;
        END $$;
    """))
    for table, column, enum_name, default in STATUS_COLUMNS:
        op.execute(sa.text(f'ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT'))
        op.execute(sa.text(f'ALTER TABLE {table} ALTER COLUMN {column} TYPE {enum_name} USING {column}::{enum_name}'))
        op.alter_column(table, column, server_default=sa.text(f"'{default}'"))
    for name in LEGACY_AGENT_JSON:
        op.alter_column("agents", name, server_default=None)
    op.alter_column("agent_versions", "builder_snapshot", server_default=None)
    op.drop_column("agent_versions", "meta")
    op.drop_column("agents", "meta")
    op.drop_column("agents", "archived_reason")
    op.drop_column("agents", "published_version_number")
    op.drop_column("agents", "published_version_id")
