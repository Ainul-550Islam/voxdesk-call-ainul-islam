"""Enterprise missing APIs closure — batch calls, A/B testing, PCAP, retention, webhooks, Salesforce, KB collections, simulation, tool registry, workflow triggers, multichannel, call policies, analysis, backfill, live sessions, DNC

Revision ID: 0037_enterprise_missing_apis
Revises: 0036_runtime_deployment_observ
Create Date: 2026-09-29
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0037_enterprise_missing_apis"
down_revision = "0036_runtime_deployment_observ"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # batch_calls
    op.create_table(
        "batch_calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("agent_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("concurrency", sa.Integer, nullable=False, server_default="5"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("retry_delay_seconds", sa.Integer, nullable=False, server_default="3600"),
        sa.Column("voicemail_action", sa.String(32), nullable=False, server_default="hangup"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calling_window_start", sa.String(8), nullable=False, server_default="09:00"),
        sa.Column("calling_window_end", sa.String(8), nullable=False, server_default="20:00"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("total_recipients", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completed_recipients", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_recipients", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meta", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.create_index("ix_batch_calls_tenant", "batch_calls", ["tenant_id"])
    op.create_index("ix_batch_calls_status", "batch_calls", ["tenant_id", "status"])

    op.create_table(
        "batch_recipients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("batch_calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False, server_default=""),
        sa.Column("custom_fields", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(500), nullable=False, server_default=""),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_batch_recipients_batch", "batch_recipients", ["batch_id"])
    op.create_index("ix_batch_recipients_tenant", "batch_recipients", ["tenant_id"])
    op.create_unique_constraint("uq_batch_recipients_phone", "batch_recipients", ["batch_id", "phone"])

    # experiments
    op.create_table(
        "experiments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("traffic_split", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("winner_variant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_experiments_tenant", "experiments", ["tenant_id"])

    op.create_table(
        "experiment_variants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("experiment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("weight", sa.Integer, nullable=False, server_default="50"),
        sa.Column("config", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("prompt", sa.Text, nullable=False, server_default=""),
        sa.Column("is_control", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("metrics", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_exp_variants_exp", "experiment_variants", ["experiment_id"])

    # pcap_artifacts
    op.create_table(
        "pcap_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False, server_default="twilio"),
        sa.Column("capture_type", sa.String(32), nullable=False, server_default="sip"),
        sa.Column("size_bytes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("storage_key", sa.String(240), nullable=False, server_default=""),
        sa.Column("checksum", sa.String(64), nullable=False, server_default=""),
        sa.Column("retention_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pcap_tenant", "pcap_artifacts", ["tenant_id"])
    op.create_index("ix_pcap_call", "pcap_artifacts", ["call_id"])

    # retention_policies
    op.create_table(
        "retention_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("resource_type", sa.String(32), nullable=False, server_default="call"),
        sa.Column("retention_days", sa.Integer, nullable=False, server_default="90"),
        sa.Column("purge_after_days", sa.Integer, nullable=False, server_default="365"),
        sa.Column("legal_hold", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("last_purge_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_purge_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meta", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.create_index("ix_retention_tenant", "retention_policies", ["tenant_id"])
    op.create_unique_constraint("uq_retention_agent_resource", "retention_policies", ["tenant_id", "agent_id", "resource_type"])

    # webhook_endpoints
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("secret", sa.String(128), nullable=False, server_default=""),
        sa.Column("events", sa.JSON, nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("retry_policy", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_delivery_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_count", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_webhook_endpoints_tenant", "webhook_endpoints", ["tenant_id"])

    op.create_table(
        "webhook_delivery_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("endpoint_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("http_status", sa.Integer, nullable=False, server_default="0"),
        sa.Column("response_body", sa.Text, nullable=False, server_default=""),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_webhook_attempts_endpoint", "webhook_delivery_attempts", ["endpoint_id"])
    op.create_index("ix_webhook_attempts_tenant", "webhook_delivery_attempts", ["tenant_id"])

    # salesforce_connections
    op.create_table(
        "salesforce_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("instance_url", sa.String(500), nullable=False, server_default=""),
        sa.Column("access_token_encrypted", sa.Text, nullable=False, server_default=""),
        sa.Column("refresh_token_encrypted", sa.Text, nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meta", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.create_unique_constraint("uq_salesforce_tenant", "salesforce_connections", ["tenant_id"])

    # crm_writeback_logs
    op.create_table(
        "crm_writeback_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("payload", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("error", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_crm_writeback_tenant", "crm_writeback_logs", ["tenant_id"])
    op.create_index("ix_crm_writeback_call", "crm_writeback_logs", ["call_id"])

    # knowledge_collections
    op.create_table(
        "knowledge_collections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("agent_ids", sa.JSON, nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("sync_status", sa.String(24), nullable=False, server_default="idle"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("meta", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.create_index("ix_kb_collections_tenant", "knowledge_collections", ["tenant_id"])

    op.create_table(
        "knowledge_collection_sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("collection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("knowledge_collections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("uri", sa.String(500), nullable=False, server_default=""),
        sa.Column("sync_status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_kb_coll_src_coll", "knowledge_collection_sources", ["collection_id"])

    # call_simulations
    op.create_table(
        "call_simulations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("scenario", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("result", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("evidence", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_call_sim_tenant", "call_simulations", ["tenant_id"])

    # agent_tools
    op.create_table(
        "agent_tools",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("schema", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("auth_binding", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_tools_tenant", "agent_tools", ["tenant_id"])
    op.create_unique_constraint("uq_agent_tools_name", "agent_tools", ["tenant_id", "agent_id", "name"])

    # workflow_triggers
    op.create_table(
        "workflow_triggers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workflow_id", sa.String(80), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("config", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_wf_triggers_tenant", "workflow_triggers", ["tenant_id"])

    # message_channels
    op.create_table(
        "message_channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel_type", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("config", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("health_status", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_msg_channels_tenant", "message_channels", ["tenant_id"])

    # call_policies
    op.create_table(
        "call_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("policy_type", sa.String(32), nullable=False),
        sa.Column("config", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_call_policies_tenant", "call_policies", ["tenant_id"])
    op.create_unique_constraint("uq_call_policies_agent_type", "call_policies", ["tenant_id", "agent_id", "policy_type"])

    # analysis_schemas
    op.create_table(
        "analysis_schemas",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("fields", sa.JSON, nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_analysis_schemas_tenant", "analysis_schemas", ["tenant_id"])

    op.create_table(
        "analysis_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("schema_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("analysis_schemas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("result", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("status", sa.String(24), nullable=False, server_default="completed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_analysis_results_tenant", "analysis_results", ["tenant_id"])
    op.create_index("ix_analysis_results_call", "analysis_results", ["call_id"])

    op.create_table(
        "backfill_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("schema_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("analysis_schemas.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("total_calls", sa.Integer, nullable=False, server_default="0"),
        sa.Column("processed_calls", sa.Integer, nullable=False, server_default="0"),
        sa.Column("failed_calls", sa.Integer, nullable=False, server_default="0"),
        sa.Column("idempotency_key", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_backfill_tenant", "backfill_jobs", ["tenant_id"])

    # live_call_sessions
    op.create_table(
        "live_call_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("supervisor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(24), nullable=False, server_default="listen"),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta", sa.JSON, nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.create_index("ix_live_sessions_tenant", "live_call_sessions", ["tenant_id"])
    op.create_index("ix_live_sessions_call", "live_call_sessions", ["call_id"])

    # dnc_entries
    op.create_table(
        "dnc_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("phone", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False, server_default=""),
        sa.Column("source", sa.String(64), nullable=False, server_default="manual"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dnc_tenant", "dnc_entries", ["tenant_id"])
    op.create_unique_constraint("uq_dnc_phone", "dnc_entries", ["tenant_id", "phone"])

def downgrade() -> None:
    op.drop_table("dnc_entries")
    op.drop_table("live_call_sessions")
    op.drop_table("backfill_jobs")
    op.drop_table("analysis_results")
    op.drop_table("analysis_schemas")
    op.drop_table("call_policies")
    op.drop_table("message_channels")
    op.drop_table("workflow_triggers")
    op.drop_table("agent_tools")
    op.drop_table("call_simulations")
    op.drop_table("knowledge_collection_sources")
    op.drop_table("knowledge_collections")
    op.drop_table("crm_writeback_logs")
    op.drop_table("salesforce_connections")
    op.drop_table("webhook_delivery_attempts")
    op.drop_table("webhook_endpoints")
    op.drop_table("retention_policies")
    op.drop_table("pcap_artifacts")
    op.drop_table("experiment_variants")
    op.drop_table("experiments")
    op.drop_table("batch_recipients")
    op.drop_table("batch_calls")
