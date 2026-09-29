"""Prompt 3 connector/tool/MCP/security surfaces and PostgreSQL RLS.

Revision: 0029
Revises: 0028_durable_workflow

All newly introduced tenant records are protected by transaction-scoped RLS.
The application must install ``app.tenant_id`` with ``set_config(..., true)``
inside the transaction; an absent setting matches no row and therefore fails
closed.
"""

from __future__ import annotations
import sqlalchemy as sa
from alembic import op

revision = "0029_prompt3_surfaces"
down_revision = "0028_durable_workflow"
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "connectors",
    "connector_oauth",
    "api_tools",
    "tool_executions",
    "mcp_servers",
    "mcp_tools",
    "knowledge_sources",
    "privacy_policies",
    "human_approvals",
    "public_webhook_endpoints",
    "public_webhook_receipts",
)


def _uuid():
    return sa.Uuid()


def _created():
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "connectors" not in tables:
        op.create_table(
            "connectors",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tenant_id", _uuid(), nullable=False),
            sa.Column("environment_id", _uuid(), nullable=True),
            sa.Column("provider", sa.String(80), nullable=False),
            sa.Column("kind", sa.String(40), nullable=False, server_default="generic"),
            sa.Column("status", sa.String(32), nullable=False, server_default="configured"),
            sa.Column("capabilities", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("credential_ref", sa.String(255), nullable=True),
            sa.Column("health_message", sa.String(255), nullable=True),
            sa.Column("last_health_at", sa.DateTime(timezone=True), nullable=True),
            _created(),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.UniqueConstraint(
                "tenant_id", "environment_id", "provider", name="uq_connector_scope_provider"
            ),
        )
        op.create_index("ix_connectors_tenant", "connectors", ["tenant_id"])
        op.create_index("ix_connectors_tenant_status", "connectors", ["tenant_id", "status"])
    if "connector_oauth" not in tables:
        op.create_table(
            "connector_oauth",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tenant_id", _uuid(), nullable=False),
            sa.Column("connector_id", _uuid(), nullable=False),
            sa.Column("state", sa.String(24), nullable=False, server_default="disconnected"),
            sa.Column("oauth_state_nonce", sa.String(96), nullable=True),
            sa.Column("oauth_state_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("scopes", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("access_token_ref", sa.String(255), nullable=True),
            sa.Column("refresh_token_ref", sa.String(255), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("provider_account_ref", sa.String(255), nullable=True),
            _created(),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["connector_id"], ["connectors.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("connector_id", name="uq_connector_oauth_connector"),
        )
        op.create_index("ix_connector_oauth_tenant", "connector_oauth", ["tenant_id"])
    if "connector_oauth" in tables:
        oauth_columns = {
            column["name"] for column in sa.inspect(op.get_bind()).get_columns("connector_oauth")
        }
        if "oauth_state_nonce" not in oauth_columns:
            op.add_column(
                "connector_oauth", sa.Column("oauth_state_nonce", sa.String(96), nullable=True)
            )
        if "oauth_state_expires_at" not in oauth_columns:
            op.add_column(
                "connector_oauth",
                sa.Column("oauth_state_expires_at", sa.DateTime(timezone=True), nullable=True),
            )
    if "api_tools" not in tables:
        op.create_table(
            "api_tools",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tenant_id", _uuid(), nullable=False),
            sa.Column("environment_id", _uuid(), nullable=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("description", sa.String(500), nullable=False, server_default=""),
            sa.Column("method", sa.String(8), nullable=False),
            sa.Column("url_template", sa.String(1000), nullable=False),
            sa.Column("parameter_schema", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("body_schema", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("result_schema", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("auth_ref", sa.String(255), nullable=True),
            sa.Column("timeout_seconds", sa.Float(), nullable=False, server_default="10"),
            sa.Column("retry_max_attempts", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            _created(),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.UniqueConstraint(
                "tenant_id", "environment_id", "name", name="uq_api_tools_scope_name"
            ),
        )
        op.create_index("ix_api_tools_tenant", "api_tools", ["tenant_id"])
        op.create_index("ix_api_tools_tenant_enabled", "api_tools", ["tenant_id", "enabled"])
    if "tool_executions" not in tables:
        op.create_table(
            "tool_executions",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tenant_id", _uuid(), nullable=False),
            sa.Column("environment_id", _uuid(), nullable=True),
            sa.Column("tool_type", sa.String(32), nullable=False),
            sa.Column("tool_id", _uuid(), nullable=False),
            sa.Column("idempotency_key", sa.String(180), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="started"),
            sa.Column("result_metadata", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("error_category", sa.String(64), nullable=True),
            _created(),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.UniqueConstraint(
                "tenant_id", "idempotency_key", name="uq_tool_execution_idempotency"
            ),
        )
        op.create_index("ix_tool_executions_tenant", "tool_executions", ["tenant_id"])
        op.create_index(
            "ix_tool_executions_tenant_created", "tool_executions", ["tenant_id", "created_at"]
        )
    if "mcp_servers" not in tables:
        op.create_table(
            "mcp_servers",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tenant_id", _uuid(), nullable=False),
            sa.Column("environment_id", _uuid(), nullable=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("endpoint", sa.String(1000), nullable=False),
            sa.Column("transport", sa.String(32), nullable=False, server_default="streamable_http"),
            sa.Column("auth_ref", sa.String(255), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="configured"),
            sa.Column("server_info", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("last_health_at", sa.DateTime(timezone=True), nullable=True),
            _created(),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.UniqueConstraint(
                "tenant_id", "environment_id", "name", name="uq_mcp_server_scope_name"
            ),
        )
        op.create_index("ix_mcp_servers_tenant", "mcp_servers", ["tenant_id"])
    if "mcp_tools" not in tables:
        op.create_table(
            "mcp_tools",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tenant_id", _uuid(), nullable=False),
            sa.Column("server_id", _uuid(), nullable=False),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("description", sa.String(1000), nullable=False, server_default=""),
            sa.Column("input_schema", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("annotations", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            _created(),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["server_id"], ["mcp_servers.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("server_id", "name", name="uq_mcp_tool_server_name"),
        )
        op.create_index("ix_mcp_tools_tenant", "mcp_tools", ["tenant_id"])
    if "knowledge_sources" not in tables:
        op.create_table(
            "knowledge_sources",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tenant_id", _uuid(), nullable=False),
            sa.Column("environment_id", _uuid(), nullable=True),
            sa.Column("canonical_url", sa.String(2000), nullable=False),
            sa.Column("max_depth", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("last_error", sa.String(500), nullable=True),
            _created(),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.UniqueConstraint(
                "tenant_id", "environment_id", "canonical_url", name="uq_knowledge_source_url"
            ),
        )
        op.create_index("ix_knowledge_sources_tenant", "knowledge_sources", ["tenant_id"])
    if "privacy_policies" not in tables:
        op.create_table(
            "privacy_policies",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tenant_id", _uuid(), nullable=False),
            sa.Column("mode", sa.String(16), nullable=False, server_default="mask"),
            sa.Column("rules", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("tenant_id", name="uq_privacy_policy_tenant"),
        )
        op.create_index("ix_privacy_policies_tenant", "privacy_policies", ["tenant_id"])
    if "human_approvals" not in tables:
        op.create_table(
            "human_approvals",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tenant_id", _uuid(), nullable=False),
            sa.Column("environment_id", _uuid(), nullable=True),
            sa.Column("action", sa.String(120), nullable=False),
            sa.Column("subject_ref", sa.String(255), nullable=False),
            sa.Column("payload_digest", sa.String(64), nullable=False),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("requested_by", _uuid(), nullable=True),
            sa.Column("decided_by", _uuid(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            _created(),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        )
        op.create_index("ix_human_approvals_tenant", "human_approvals", ["tenant_id"])
        op.create_index(
            "ix_human_approvals_tenant_status", "human_approvals", ["tenant_id", "status"]
        )
    if "public_webhook_endpoints" not in tables:
        op.create_table(
            "public_webhook_endpoints",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tenant_id", _uuid(), nullable=False),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("secret_ref", sa.String(255), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            _created(),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.UniqueConstraint("tenant_id", "name", name="uq_public_webhook_tenant_name"),
        )
        op.create_index(
            "ix_public_webhook_endpoints_tenant", "public_webhook_endpoints", ["tenant_id"]
        )
    if "public_webhook_receipts" not in tables:
        op.create_table(
            "public_webhook_receipts",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tenant_id", _uuid(), nullable=False),
            sa.Column("endpoint_id", _uuid(), nullable=False),
            sa.Column("event_id", sa.String(180), nullable=False),
            sa.Column("payload_digest", sa.String(64), nullable=False),
            sa.Column(
                "received_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["endpoint_id"], ["public_webhook_endpoints.id"], ondelete="CASCADE"
            ),
            sa.UniqueConstraint("endpoint_id", "event_id", name="uq_public_webhook_receipt_event"),
        )
        op.create_index(
            "ix_public_webhook_receipts_tenant", "public_webhook_receipts", ["tenant_id"]
        )
        op.create_index(
            "ix_public_webhook_receipts_received", "public_webhook_receipts", ["received_at"]
        )

    columns = {c["name"] for c in inspector.get_columns("email_deliveries")}
    additions = (
        ("subject", sa.String(255), ""),
        ("provider", sa.String(64), ""),
        ("idempotency_key", sa.String(180), ""),
    )
    for name, typ, default in additions:
        if name not in columns:
            op.add_column(
                "email_deliveries", sa.Column(name, typ, nullable=False, server_default=default)
            )
    if "uq_email_delivery_idempotency" not in {
        i["name"] for i in inspector.get_indexes("email_deliveries")
    }:
        op.create_index(
            "uq_email_delivery_idempotency",
            "email_deliveries",
            ["tenant_id", "idempotency_key"],
            unique=True,
            postgresql_where=sa.text("idempotency_key <> ''"),
        )

    # Real PostgreSQL RLS. PostgreSQL is the production database; SQLite test
    # runs skip these statements because SQLite has no RLS implementation.
    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
            op.execute(
                sa.text(
                    f"CREATE POLICY {table}_tenant_isolation ON {table} USING (tenant_id::text = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"
                )
            )
        # Public delivery has no authenticated tenant yet. This second policy
        # permits only the endpoint UUID supplied by the request; the handler
        # installs the endpoint's tenant context before any other row access.
        op.execute(
            sa.text(
                "CREATE POLICY public_webhook_lookup ON public_webhook_endpoints USING (id::text = current_setting('app.webhook_endpoint_id', true))"
            )
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text("DROP POLICY IF EXISTS public_webhook_lookup ON public_webhook_endpoints")
        )
        for table in TENANT_TABLES:
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
            op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    inspector = sa.inspect(op.get_bind())
    indexes = {item["name"] for item in inspector.get_indexes("email_deliveries")}
    if "uq_email_delivery_idempotency" in indexes:
        op.drop_index("uq_email_delivery_idempotency", table_name="email_deliveries")
    columns = {c["name"] for c in inspector.get_columns("email_deliveries")}
    for name in ("idempotency_key", "provider", "subject"):
        if name in columns:
            op.drop_column("email_deliveries", name)
    oauth_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("connector_oauth")
    }
    for name in ("oauth_state_expires_at", "oauth_state_nonce"):
        if name in oauth_columns:
            op.drop_column("connector_oauth", name)
    for table in reversed(TENANT_TABLES):
        if table in set(inspector.get_table_names()):
            op.drop_table(table)
