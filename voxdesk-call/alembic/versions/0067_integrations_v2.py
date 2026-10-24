"""Add integrations_v2 schema for helpdesk, CRM, collaboration, sync, and agent tools (Batch 05).

Revision ID: 0067_integrations_v2
Revises: 0066_contact_center_v2
Create Date: 2026-10-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0067_integrations_v2"
down_revision = "0066_contact_center_v2"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "int2_connections"):
        op.create_table(
            "int2_connections",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("environment_id", sa.Uuid(), nullable=True),
            sa.Column("provider", sa.String(length=48), nullable=False),
            sa.Column("category", sa.String(length=24), nullable=False),
            sa.Column("display_name", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("auth_kind", sa.String(length=24), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
            sa.Column(
                "external_account_id",
                sa.String(length=160),
                nullable=False,
                server_default="default",
            ),
            sa.Column("instance_url", sa.String(length=512), nullable=False, server_default=""),
            sa.Column("region", sa.String(length=32), nullable=False, server_default="us"),
            sa.Column("scopes", sa.JSON(), nullable=False),
            sa.Column("token_secret_ref", sa.Text(), nullable=False, server_default=""),
            sa.Column("refresh_secret_ref", sa.Text(), nullable=False, server_default=""),
            sa.Column("webhook_secret_ref", sa.Text(), nullable=False, server_default=""),
            sa.Column("encryption_key_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("config_json", sa.JSON(), nullable=False),
            sa.Column("last_health_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_health_ok", sa.Boolean(), nullable=True),
            sa.Column("last_error", sa.String(length=512), nullable=False, server_default=""),
            sa.Column(
                "created_by_user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "provider",
                "external_account_id",
                name="uq_int2_conn_tenant_provider_ext",
            ),
            sa.CheckConstraint(
                "status IN ('pending','active','expired','revoked','needs_reauth','disabled','error')",
                name="ck_int2_conn_status",
            ),
            sa.CheckConstraint(
                "category IN ('helpdesk','crm','collab')",
                name="ck_int2_conn_category",
            ),
        )
        op.create_index("ix_int2_connections_tenant_id", "int2_connections", ["tenant_id"])
        op.create_index(
            "ix_int2_conn_tenant_env",
            "int2_connections",
            ["tenant_id", "environment_id"],
        )
        op.create_index(
            "ix_int2_conn_tenant_category_status",
            "int2_connections",
            ["tenant_id", "category", "status"],
        )
        op.create_index(
            "ix_int2_conn_token_expires",
            "int2_connections",
            ["status", "token_expires_at"],
        )

    if not _has_table(bind, "int2_oauth_states"):
        op.create_table(
            "int2_oauth_states",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                sa.Uuid(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("provider", sa.String(length=48), nullable=False),
            sa.Column("state_hash", sa.String(length=64), nullable=False),
            sa.Column("pkce_verifier_secret_ref", sa.Text(), nullable=False),
            sa.Column("redirect_uri", sa.String(length=512), nullable=False),
            sa.Column("extra_params", sa.JSON(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("state_hash", name="uq_int2_oauth_state_hash"),
        )
        op.create_index("ix_int2_oauth_states_tenant_id", "int2_oauth_states", ["tenant_id"])
        op.create_index(
            "ix_int2_oauth_state_tenant_exp",
            "int2_oauth_states",
            ["tenant_id", "expires_at"],
        )

    if not _has_table(bind, "int2_field_mapping_profiles"):
        op.create_table(
            "int2_field_mapping_profiles",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("environment_id", sa.Uuid(), nullable=True),
            sa.Column(
                "connection_id",
                sa.Uuid(),
                sa.ForeignKey("int2_connections.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("entity_type", sa.String(length=48), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False, server_default="default"),
            sa.Column("rules_json", sa.JSON(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "connection_id", "entity_type", "name", name="uq_int2_mapping_profile_name"
            ),
        )
        op.create_index(
            "ix_int2_field_mapping_profiles_tenant_id",
            "int2_field_mapping_profiles",
            ["tenant_id"],
        )
        op.create_index(
            "ix_int2_mapping_profile_tenant_env",
            "int2_field_mapping_profiles",
            ["tenant_id", "environment_id"],
        )

    if not _has_table(bind, "int2_field_mappings"):
        op.create_table(
            "int2_field_mappings",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "connection_id",
                sa.Uuid(),
                sa.ForeignKey("int2_connections.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("entity_type", sa.String(length=48), nullable=False),
            sa.Column(
                "direction",
                sa.String(length=16),
                nullable=False,
                server_default="bidirectional",
            ),
            sa.Column("voxdesk_field", sa.String(length=120), nullable=False),
            sa.Column("external_field", sa.String(length=160), nullable=False),
            sa.Column(
                "transform",
                sa.String(length=32),
                nullable=False,
                server_default="identity",
            ),
            sa.Column("transform_config", sa.JSON(), nullable=False),
            sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("default_value", sa.String(length=256), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "connection_id",
                "entity_type",
                "voxdesk_field",
                "external_field",
                name="uq_int2_field_map_rule",
            ),
            sa.CheckConstraint(
                "direction IN ('inbound','outbound','bidirectional')",
                name="ck_int2_field_map_direction",
            ),
        )
        op.create_index(
            "ix_int2_field_mappings_tenant_id", "int2_field_mappings", ["tenant_id"]
        )
        op.create_index(
            "ix_int2_field_map_tenant_conn",
            "int2_field_mappings",
            ["tenant_id", "connection_id"],
        )

    if not _has_table(bind, "int2_sync_cursors"):
        op.create_table(
            "int2_sync_cursors",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "connection_id",
                sa.Uuid(),
                sa.ForeignKey("int2_connections.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("entity_type", sa.String(length=48), nullable=False),
            sa.Column("cursor_value", sa.String(length=1024), nullable=False, server_default=""),
            sa.Column(
                "cursor_kind",
                sa.String(length=32),
                nullable=False,
                server_default="updated_since",
            ),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "connection_id", "entity_type", name="uq_int2_sync_cursor_conn_entity"
            ),
        )
        op.create_index("ix_int2_sync_cursors_tenant_id", "int2_sync_cursors", ["tenant_id"])
        op.create_index(
            "ix_int2_sync_cursor_tenant",
            "int2_sync_cursors",
            ["tenant_id", "connection_id"],
        )

    if not _has_table(bind, "int2_sync_runs"):
        op.create_table(
            "int2_sync_runs",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("environment_id", sa.Uuid(), nullable=True),
            sa.Column(
                "connection_id",
                sa.Uuid(),
                sa.ForeignKey("int2_connections.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("entity_type", sa.String(length=48), nullable=False),
            sa.Column("direction", sa.String(length=16), nullable=False, server_default="inbound"),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="running"),
            sa.Column("cursor_before", sa.String(length=1024), nullable=False, server_default=""),
            sa.Column("cursor_after", sa.String(length=1024), nullable=False, server_default=""),
            sa.Column("records_seen", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("records_upserted", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("records_failed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("dead_letters", sa.JSON(), nullable=False),
            sa.Column("error_summary", sa.String(length=512), nullable=False, server_default=""),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.CheckConstraint(
                "status IN ('running','succeeded','partial','failed')",
                name="ck_int2_sync_runs_status",
            ),
        )
        op.create_index("ix_int2_sync_runs_tenant_id", "int2_sync_runs", ["tenant_id"])
        op.create_index(
            "ix_int2_sync_runs_tenant_env",
            "int2_sync_runs",
            ["tenant_id", "environment_id"],
        )
        op.create_index(
            "ix_int2_sync_runs_tenant_conn_started",
            "int2_sync_runs",
            ["tenant_id", "connection_id", "started_at"],
        )

    if not _has_table(bind, "int2_external_links"):
        op.create_table(
            "int2_external_links",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "connection_id",
                sa.Uuid(),
                sa.ForeignKey("int2_connections.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("entity_type", sa.String(length=48), nullable=False),
            sa.Column("voxdesk_id", sa.String(length=120), nullable=False),
            sa.Column("external_id", sa.String(length=160), nullable=False),
            sa.Column("external_etag", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("external_updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "connection_id",
                "entity_type",
                "external_id",
                name="uq_int2_ext_link_external",
            ),
        )
        op.create_index(
            "ix_int2_external_links_tenant_id", "int2_external_links", ["tenant_id"]
        )
        op.create_index(
            "ix_int2_ext_link_voxdesk",
            "int2_external_links",
            ["tenant_id", "connection_id", "entity_type", "voxdesk_id"],
        )

    if not _has_table(bind, "int2_writeback_ledger"):
        op.create_table(
            "int2_writeback_ledger",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "connection_id",
                sa.Uuid(),
                sa.ForeignKey("int2_connections.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("call_id", sa.String(length=120), nullable=False),
            sa.Column("provider", sa.String(length=48), nullable=False, server_default="unknown"),
            sa.Column("entity", sa.String(length=64), nullable=False, server_default="call_log"),
            sa.Column("action", sa.String(length=48), nullable=False),
            sa.Column("idempotency_key", sa.String(length=160), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
            sa.Column("external_id", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("payload_hash", sa.String(length=64), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.String(length=512), nullable=False, server_default=""),
            sa.Column("outbox_event_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "tenant_id", "idempotency_key", name="uq_int2_writeback_tenant_idem"
            ),
            sa.UniqueConstraint(
                "call_id", "provider", "entity", name="uq_int2_ledger_call_provider_entity"
            ),
            sa.CheckConstraint(
                "status IN ('pending','succeeded','failed','dead_letter')",
                name="ck_int2_writeback_status",
            ),
        )
        op.create_index(
            "ix_int2_writeback_ledger_tenant_id", "int2_writeback_ledger", ["tenant_id"]
        )
        op.create_index(
            "ix_int2_writeback_call",
            "int2_writeback_ledger",
            ["tenant_id", "call_id"],
        )

    if not _has_table(bind, "int2_webhook_events"):
        op.create_table(
            "int2_webhook_events",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "connection_id",
                sa.Uuid(),
                sa.ForeignKey("int2_connections.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("provider", sa.String(length=48), nullable=False),
            sa.Column("external_event_id", sa.String(length=180), nullable=False),
            sa.Column("nonce", sa.String(length=192), nullable=False),
            sa.Column("event_type", sa.String(length=120), nullable=False, server_default=""),
            sa.Column(
                "signature_verified",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
            sa.Column("payload_sha256", sa.String(length=64), nullable=False),
            sa.Column("payload_excerpt", sa.JSON(), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.String(length=512), nullable=False, server_default=""),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint(
                "connection_id", "external_event_id", name="uq_int2_wh_conn_event"
            ),
            sa.UniqueConstraint("nonce", name="uq_int2_wh_nonce"),
            sa.CheckConstraint(
                "status IN ('queued','processed','failed','dead_letter','duplicate')",
                name="ck_int2_wh_status",
            ),
        )
        op.create_index(
            "ix_int2_webhook_events_tenant_id", "int2_webhook_events", ["tenant_id"]
        )
        op.create_index(
            "ix_int2_wh_tenant_status",
            "int2_webhook_events",
            ["tenant_id", "status", "received_at"],
        )

    if not _has_table(bind, "int2_collab_routes"):
        op.create_table(
            "int2_collab_routes",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("environment_id", sa.Uuid(), nullable=True),
            sa.Column(
                "connection_id",
                sa.Uuid(),
                sa.ForeignKey("int2_connections.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("team_id", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("channel_id", sa.String(length=160), nullable=False),
            sa.Column("channel_name", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("template", sa.String(length=2000), nullable=False, server_default=""),
            sa.Column("rate_limit_per_min", sa.Integer(), nullable=False, server_default="30"),
            sa.Column(
                "dedupe_window_seconds", sa.Integer(), nullable=False, server_default="120"
            ),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("filter_config", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "connection_id",
                "event_type",
                "channel_id",
                name="uq_int2_collab_route_target",
            ),
        )
        op.create_index(
            "ix_int2_collab_routes_tenant_id", "int2_collab_routes", ["tenant_id"]
        )
        op.create_index(
            "ix_int2_collab_route_tenant_env",
            "int2_collab_routes",
            ["tenant_id", "environment_id"],
        )
        op.create_index(
            "ix_int2_collab_route_tenant_event",
            "int2_collab_routes",
            ["tenant_id", "event_type", "enabled"],
        )

    if not _has_table(bind, "int2_meetings"):
        op.create_table(
            "int2_meetings",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("environment_id", sa.Uuid(), nullable=True),
            sa.Column(
                "connection_id",
                sa.Uuid(),
                sa.ForeignKey("int2_connections.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("provider", sa.String(length=48), nullable=False),
            sa.Column("lead_id", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("call_id", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("external_meeting_id", sa.String(length=160), nullable=False),
            sa.Column("topic", sa.String(length=240), nullable=False),
            sa.Column("join_url", sa.String(length=1024), nullable=False),
            sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
            sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="30"),
            sa.Column("idempotency_key", sa.String(length=160), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="scheduled"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "tenant_id", "idempotency_key", name="uq_int2_meeting_tenant_idem"
            ),
        )
        op.create_index("ix_int2_meetings_tenant_id", "int2_meetings", ["tenant_id"])
        op.create_index(
            "ix_int2_meetings_tenant_env",
            "int2_meetings",
            ["tenant_id", "environment_id"],
        )

    if not _has_table(bind, "int2_tool_enablements"):
        op.create_table(
            "int2_tool_enablements",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("agent_id", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("tool_name", sa.String(length=80), nullable=False),
            sa.Column(
                "connection_id",
                sa.Uuid(),
                sa.ForeignKey("int2_connections.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(
                "max_calls_per_conversation",
                sa.Integer(),
                nullable=False,
                server_default="3",
            ),
            sa.Column("timeout_ms", sa.Integer(), nullable=False, server_default="3000"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "agent_id",
                "tool_name",
                name="uq_int2_tool_enablement_agent_tool",
            ),
        )
        op.create_index(
            "ix_int2_tool_enablements_tenant_id", "int2_tool_enablements", ["tenant_id"]
        )
        op.create_index(
            "ix_int2_tool_enablement_tenant",
            "int2_tool_enablements",
            ["tenant_id", "enabled"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in (
        "int2_tool_enablements",
        "int2_meetings",
        "int2_collab_routes",
        "int2_webhook_events",
        "int2_writeback_ledger",
        "int2_external_links",
        "int2_sync_runs",
        "int2_sync_cursors",
        "int2_field_mappings",
        "int2_field_mapping_profiles",
        "int2_oauth_states",
        "int2_connections",
    ):
        if _has_table(bind, table_name):
            op.drop_table(table_name)
