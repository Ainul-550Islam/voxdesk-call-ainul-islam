"""
alembic/versions/0041_telephony_voice_runtime.py
Prompt 6 — Telephony / Voice Runtime tables:
- telephony_sip_connections
- telephony_phone_numbers
- telephony_call_sessions
- telephony_provider_events
- telephony_transfer_records
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0041_telephony_voice_runtime"
down_revision = "0040_public_widget_keys"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telephony_sip_connections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False, server_default="Primary SIP Trunk"),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="SIP"),
        sa.Column("phone_number_e164", sa.String(length=32), nullable=True),
        sa.Column("termination_uri", sa.String(length=255), nullable=False),
        sa.Column("origination_uri", sa.String(length=255), nullable=True),
        sa.Column("username", sa.String(length=128), nullable=True),
        sa.Column("credential_reference", sa.String(length=128), nullable=True),
        sa.Column("transport", sa.String(length=16), nullable=False, server_default="TLS"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="NOT_CONFIGURED"),
        sa.Column("last_test_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=400), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_telephony_sip_connections_tenant_id",
        "telephony_sip_connections",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_sip_connections_environment_id",
        "telephony_sip_connections",
        ["environment_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_sip_connections_status",
        "telephony_sip_connections",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_sip_connections_tenant_status",
        "telephony_sip_connections",
        ["tenant_id", "status"],
        unique=False,
    )

    op.create_table(
        "telephony_phone_numbers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("number", sa.String(length=32), nullable=False),
        sa.Column("e164_number", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="TWILIO"),
        sa.Column("provider_number_id", sa.String(length=96), nullable=True),
        sa.Column("sip_connection_id", sa.Uuid(), nullable=True),
        sa.Column("sip_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("inbound_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("outbound_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("inbound_agent_id", sa.String(length=64), nullable=True),
        sa.Column("outbound_agent_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="NOT_CONFIGURED"),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("last_health_check_at", sa.DateTime(), nullable=True),
        sa.Column("last_health_error", sa.String(length=400), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["sip_connection_id"],
            ["telephony_sip_connections.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "e164_number",
            name="uq_telephony_phone_numbers_tenant_e164",
        ),
    )
    op.create_index(
        "ix_telephony_phone_numbers_tenant_id",
        "telephony_phone_numbers",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_phone_numbers_environment_id",
        "telephony_phone_numbers",
        ["environment_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_phone_numbers_e164_number",
        "telephony_phone_numbers",
        ["e164_number"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_phone_numbers_inbound_agent_id",
        "telephony_phone_numbers",
        ["inbound_agent_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_phone_numbers_outbound_agent_id",
        "telephony_phone_numbers",
        ["outbound_agent_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_phone_numbers_status",
        "telephony_phone_numbers",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_phone_numbers_tenant_status",
        "telephony_phone_numbers",
        ["tenant_id", "status"],
        unique=False,
    )

    op.create_table(
        "telephony_call_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("legacy_call_id", sa.Uuid(), nullable=True),
        sa.Column("phone_number_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.String(length=64), nullable=True),
        sa.Column("agent_version_number", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="TWILIO"),
        sa.Column("provider_call_id", sa.String(length=96), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False, server_default="INBOUND"),
        sa.Column("from_number", sa.String(length=32), nullable=False),
        sa.Column("to_number", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="CREATED"),
        sa.Column("media_state", sa.String(length=32), nullable=False, server_default="IDLE"),
        sa.Column("transfer_state", sa.String(length=32), nullable=False, server_default="NONE"),
        sa.Column("transfer_mode", sa.String(length=32), nullable=True),
        sa.Column("transfer_target", sa.String(length=128), nullable=True),
        sa.Column("transferred_to_agent_id", sa.String(length=64), nullable=True),
        sa.Column("parent_call_id", sa.Uuid(), nullable=True),
        sa.Column("is_simulation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("execution_kind", sa.String(length=24), nullable=False, server_default="PRODUCTION"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("answered_at", sa.DateTime(), nullable=True),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("billable_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("usage_finalized", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("hangup_reason", sa.String(length=120), nullable=True),
        sa.Column("recording_reference", sa.String(length=255), nullable=True),
        sa.Column("transcript_reference", sa.String(length=255), nullable=True),
        sa.Column("transcript_turns", sa.JSON(), nullable=False),
        sa.Column("dtmf_buffer", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("dtmf_events", sa.JSON(), nullable=False),
        sa.Column("runtime_events", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["legacy_call_id"], ["calls.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["phone_number_id"],
            ["telephony_phone_numbers.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "provider_call_id",
            name="uq_telephony_call_sessions_tenant_provider_call",
        ),
    )
    op.create_index(
        "ix_telephony_call_sessions_tenant_id",
        "telephony_call_sessions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_call_sessions_environment_id",
        "telephony_call_sessions",
        ["environment_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_call_sessions_legacy_call_id",
        "telephony_call_sessions",
        ["legacy_call_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_call_sessions_phone_number_id",
        "telephony_call_sessions",
        ["phone_number_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_call_sessions_agent_id",
        "telephony_call_sessions",
        ["agent_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_call_sessions_provider_call_id",
        "telephony_call_sessions",
        ["provider_call_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_call_sessions_status",
        "telephony_call_sessions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_call_sessions_parent_call_id",
        "telephony_call_sessions",
        ["parent_call_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_call_sessions_created_at",
        "telephony_call_sessions",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_call_sessions_tenant_status",
        "telephony_call_sessions",
        ["tenant_id", "status"],
        unique=False,
    )

    op.create_table(
        "telephony_provider_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column("provider_call_id", sa.String(length=96), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("processing_status", sa.String(length=32), nullable=False, server_default="received"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_reason", sa.String(length=400), nullable=True),
        sa.Column("normalized_payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["call_id"],
            ["telephony_call_sessions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "provider_event_id",
            name="uq_telephony_provider_events_tenant_provider_event",
        ),
    )
    op.create_index(
        "ix_telephony_provider_events_tenant_id",
        "telephony_provider_events",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_provider_events_environment_id",
        "telephony_provider_events",
        ["environment_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_provider_events_provider",
        "telephony_provider_events",
        ["provider"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_provider_events_provider_event_id",
        "telephony_provider_events",
        ["provider_event_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_provider_events_call_id",
        "telephony_provider_events",
        ["call_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_provider_events_provider_call_id",
        "telephony_provider_events",
        ["provider_call_id"],
        unique=False,
    )

    op.create_table(
        "telephony_transfer_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("call_session_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=96), nullable=False),
        sa.Column("transfer_mode", sa.String(length=32), nullable=False, server_default="COLD"),
        sa.Column("source_agent_id", sa.String(length=64), nullable=True),
        sa.Column("target_agent_id", sa.String(length=64), nullable=True),
        sa.Column("target_destination", sa.String(length=128), nullable=False),
        sa.Column("whisper_message", sa.String(length=500), nullable=True),
        sa.Column(
            "fallback_action",
            sa.String(length=32),
            nullable=False,
            server_default="RETURN_TO_AGENT",
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="REQUESTED"),
        sa.Column("reason", sa.String(length=400), nullable=True),
        sa.Column("failure_reason", sa.String(length=400), nullable=True),
        sa.Column("context_snapshot", sa.JSON(), nullable=False),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["call_session_id"],
            ["telephony_call_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "call_session_id",
            "idempotency_key",
            name="uq_telephony_transfer_records_idempotency",
        ),
    )
    op.create_index(
        "ix_telephony_transfer_records_tenant_id",
        "telephony_transfer_records",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_transfer_records_environment_id",
        "telephony_transfer_records",
        ["environment_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_transfer_records_call_session_id",
        "telephony_transfer_records",
        ["call_session_id"],
        unique=False,
    )

    op.create_table(
        "telephony_usage_ledger",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("call_session_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("event_stage", sa.String(length=32), nullable=False, server_default="CALL_FINALIZED"),
        sa.Column("direction", sa.String(length=16), nullable=False, server_default="INBOUND"),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="TWILIO"),
        sa.Column("is_simulation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("execution_kind", sa.String(length=24), nullable=False, server_default="PRODUCTION"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("billable_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("billable_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["environment_id"], ["environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["call_session_id"],
            ["telephony_call_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_telephony_usage_ledger_idempotency",
        ),
    )
    op.create_index(
        "ix_telephony_usage_ledger_tenant_id",
        "telephony_usage_ledger",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_usage_ledger_environment_id",
        "telephony_usage_ledger",
        ["environment_id"],
        unique=False,
    )
    op.create_index(
        "ix_telephony_usage_ledger_call_session_id",
        "telephony_usage_ledger",
        ["call_session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_telephony_usage_ledger_call_session_id", table_name="telephony_usage_ledger")
    op.drop_index("ix_telephony_usage_ledger_environment_id", table_name="telephony_usage_ledger")
    op.drop_index("ix_telephony_usage_ledger_tenant_id", table_name="telephony_usage_ledger")
    op.drop_table("telephony_usage_ledger")

    op.drop_index("ix_telephony_transfer_records_call_session_id", table_name="telephony_transfer_records")
    op.drop_index("ix_telephony_transfer_records_environment_id", table_name="telephony_transfer_records")
    op.drop_index("ix_telephony_transfer_records_tenant_id", table_name="telephony_transfer_records")
    op.drop_table("telephony_transfer_records")

    op.drop_index("ix_telephony_provider_events_provider_call_id", table_name="telephony_provider_events")
    op.drop_index("ix_telephony_provider_events_call_id", table_name="telephony_provider_events")
    op.drop_index("ix_telephony_provider_events_provider_event_id", table_name="telephony_provider_events")
    op.drop_index("ix_telephony_provider_events_provider", table_name="telephony_provider_events")
    op.drop_index("ix_telephony_provider_events_environment_id", table_name="telephony_provider_events")
    op.drop_index("ix_telephony_provider_events_tenant_id", table_name="telephony_provider_events")
    op.drop_table("telephony_provider_events")

    op.drop_index("ix_telephony_call_sessions_tenant_status", table_name="telephony_call_sessions")
    op.drop_index("ix_telephony_call_sessions_created_at", table_name="telephony_call_sessions")
    op.drop_index("ix_telephony_call_sessions_parent_call_id", table_name="telephony_call_sessions")
    op.drop_index("ix_telephony_call_sessions_status", table_name="telephony_call_sessions")
    op.drop_index("ix_telephony_call_sessions_provider_call_id", table_name="telephony_call_sessions")
    op.drop_index("ix_telephony_call_sessions_agent_id", table_name="telephony_call_sessions")
    op.drop_index("ix_telephony_call_sessions_phone_number_id", table_name="telephony_call_sessions")
    op.drop_index("ix_telephony_call_sessions_legacy_call_id", table_name="telephony_call_sessions")
    op.drop_index("ix_telephony_call_sessions_environment_id", table_name="telephony_call_sessions")
    op.drop_index("ix_telephony_call_sessions_tenant_id", table_name="telephony_call_sessions")
    op.drop_table("telephony_call_sessions")

    op.drop_index("ix_telephony_phone_numbers_tenant_status", table_name="telephony_phone_numbers")
    op.drop_index("ix_telephony_phone_numbers_status", table_name="telephony_phone_numbers")
    op.drop_index("ix_telephony_phone_numbers_outbound_agent_id", table_name="telephony_phone_numbers")
    op.drop_index("ix_telephony_phone_numbers_inbound_agent_id", table_name="telephony_phone_numbers")
    op.drop_index("ix_telephony_phone_numbers_e164_number", table_name="telephony_phone_numbers")
    op.drop_index("ix_telephony_phone_numbers_environment_id", table_name="telephony_phone_numbers")
    op.drop_index("ix_telephony_phone_numbers_tenant_id", table_name="telephony_phone_numbers")
    op.drop_table("telephony_phone_numbers")

    op.drop_index("ix_telephony_sip_connections_tenant_status", table_name="telephony_sip_connections")
    op.drop_index("ix_telephony_sip_connections_status", table_name="telephony_sip_connections")
    op.drop_index("ix_telephony_sip_connections_environment_id", table_name="telephony_sip_connections")
    op.drop_index("ix_telephony_sip_connections_tenant_id", table_name="telephony_sip_connections")
    op.drop_table("telephony_sip_connections")
