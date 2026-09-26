"""environment scope for selected business resources

Adds ``environment_id`` beside the existing ``tenant_id`` on calls, leads,
appointments, knowledge documents and chunks, automations and runs,
notifications, inbox thread state, and usage events. Billing, SSO, API keys
and service accounts are not touched.

Backfill binds each existing row to that tenant's production environment. It
never copies a row onto another tenant. A missing production environment leaves
the row unbound and the column stays nullable so the migration does not invent
an environment.

Revision ID: 0019_environment_scope_business_resources
Revises: 0018_organization_memberships_quotas
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_environment_scope_business_resources"
down_revision = "0018_organization_memberships_quotas"
branch_labels = None
depends_on = None

NEW_AUDIT_ACTIONS = (
    "RESOURCE_BOUND",
    "RESOURCE_SCOPE_DENIED",
    "RESOURCE_EXPORTED",
)

SCOPED_TABLES = (
    "calls",
    "leads",
    "appointments",
    "knowledge_documents",
    "knowledge_chunks",
    "automations",
    "automation_runs",
    "notifications",
    "inbox_thread_states",
    "usage_events",
)


def backfill_environment_scope(connection) -> dict[str, int]:
    """Bind unbound rows to their own tenant's production environment.

    Returns the number of rows updated per table. A second call updates nothing.
    """
    updated: dict[str, int] = {}
    for table in SCOPED_TABLES:
        result = connection.execute(sa.text(
            f"UPDATE {table} SET environment_id = ("
            "SELECT e.id FROM environments e "
            f"WHERE e.tenant_id = {table}.tenant_id AND e.kind = 'production' "
            "ORDER BY e.is_default DESC LIMIT 1) "
            "WHERE environment_id IS NULL "
            "AND EXISTS ("
            "SELECT 1 FROM environments e "
            f"WHERE e.tenant_id = {table}.tenant_id AND e.kind = 'production')"
        ))
        updated[table] = int(result.rowcount or 0)
    return updated


def unbound_counts(connection) -> dict[str, int]:
    counts = {}
    for table in SCOPED_TABLES:
        counts[table] = int(connection.execute(sa.text(
            f"SELECT COUNT(*) FROM {table} WHERE environment_id IS NULL"
        )).scalar_one())
    return counts


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for value in NEW_AUDIT_ACTIONS:
            op.execute(f"ALTER TYPE auditaction ADD VALUE IF NOT EXISTS '{value}'")
        op.execute(
            "ALTER TABLE environments ADD CONSTRAINT uq_environments_tenant_identity "
            "UNIQUE (tenant_id, id)"
        )
    else:
        try:
            op.create_unique_constraint(
                "uq_environments_tenant_identity", "environments", ["tenant_id", "id"],
            )
        except Exception:
            pass

    for table in SCOPED_TABLES:
        op.add_column(table, sa.Column("environment_id", sa.Uuid(), nullable=True))

    backfill_environment_scope(bind)
    remaining = unbound_counts(bind)

    for table in SCOPED_TABLES:
        op.create_index(f"ix_{table}_environment_id", table, ["environment_id"])
        op.create_index(f"ix_{table}_tenant_environment", table, ["tenant_id", "environment_id"])
        op.create_foreign_key(
            f"fk_{table}_environment",
            table, "environments",
            ["environment_id"], ["id"],
            ondelete="RESTRICT",
        )
        if bind.dialect.name == "postgresql":
            op.create_foreign_key(
                f"fk_{table}_tenant_environment",
                table, "environments",
                ["tenant_id", "environment_id"], ["tenant_id", "id"],
            )
        if remaining[table] == 0:
            op.alter_column(table, "environment_id", nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(SCOPED_TABLES):
        if bind.dialect.name == "postgresql":
            op.drop_constraint(f"fk_{table}_tenant_environment", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_environment", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_tenant_environment", table_name=table)
        op.drop_index(f"ix_{table}_environment_id", table_name=table)
        op.drop_column(table, "environment_id")
    op.drop_constraint("uq_environments_tenant_identity", "environments", type_="unique")
