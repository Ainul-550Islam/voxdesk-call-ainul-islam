"""telephony media platform

Phone-number inventory, recording metadata, consent, transcript jobs,
callback idempotency and QoS samples. Call, Tenant and the existing webhook
tables are not copied. No provider credential is stored.

Revision ID: 0022_telephony_media_platform
Revises: 0021_ai_governance
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022_telephony_media_platform"
down_revision = "0021_ai_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telephony_provider_bindings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_telephony_provider_bindings"),
    )
    op.create_index("ix_telephony_provider_bindings_tenant_id", "telephony_provider_bindings", ["tenant_id"])
    op.create_table(
        "phone_numbers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("e164", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(80), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("active_key", sa.String(48), nullable=True),
        sa.Column("country", sa.String(8), nullable=False),
        sa.Column("region", sa.String(32), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("capability_source", sa.String(32), nullable=False),
        sa.Column("assigned_use", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("active_key", name="uq_phone_numbers_active"),
        sa.CheckConstraint(
            "status IN ('available', 'reserved', 'provisioned', 'assigned', 'released')",
            name="ck_phone_numbers_status",
        ),
        sa.CheckConstraint(
            "provider IN ('twilio', 'telnyx', 'vonage')",
            name="ck_phone_numbers_provider",
        ),
    )
    op.create_index("ix_phone_numbers_tenant_id", "phone_numbers", ["tenant_id"])
    op.create_table(
        "call_recordings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("external_recording_id", sa.String(80), nullable=False),
        sa.Column("external_guard", sa.String(96), nullable=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("content_type", sa.String(80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(240), nullable=False),
        sa.Column("error_class", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("provider", "external_guard", name="uq_call_recordings_external"),
        sa.CheckConstraint(
            "state IN ('requested', 'recording', 'processing', 'ready', 'failed', 'deletion_pending', 'deleted')",
            name="ck_call_recordings_state",
        ),
    )
    op.create_index("ix_call_recordings_tenant_id", "call_recordings", ["tenant_id"])
    op.create_index("ix_call_recordings_call_id", "call_recordings", ["call_id"])
    op.create_table(
        "recording_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_scope", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=True),
        sa.Column("legal_hold", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "environment_scope", name="uq_recording_policies_scope"),
    )
    op.create_index("ix_recording_policies_tenant_id", "recording_policies", ["tenant_id"])
    op.create_table(
        "recording_consents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("jurisdiction", sa.String(32), nullable=False),
        sa.Column("requirement", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recording_consents_tenant_id", "recording_consents", ["tenant_id"])
    op.create_index("ix_recording_consents_call_id", "recording_consents", ["call_id"])
    op.create_table(
        "transcript_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("recording_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_class", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_transcript_jobs_status",
        ),
    )
    op.create_index("ix_transcript_jobs_tenant_id", "transcript_jobs", ["tenant_id"])
    op.create_index("ix_transcript_jobs_call_id", "transcript_jobs", ["call_id"])
    op.create_table(
        "telephony_callback_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("external_id", sa.String(80), nullable=False),
        sa.Column("event_id", sa.String(80), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("replay_count", sa.Integer(), nullable=False),
        sa.Column("error_class", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("safe_metadata", sa.JSON(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.UniqueConstraint("idempotency_key", name="uq_telephony_callback_events_key"),
        sa.CheckConstraint(
            "status IN ('received', 'processed', 'dead_letter', 'replayed', 'unmatched', 'retry_scheduled')",
            name="ck_telephony_callback_events_status",
        ),
    )
    op.create_index("ix_telephony_callback_events_tenant_id", "telephony_callback_events", ["tenant_id"])
    op.create_table(
        "call_qos_samples",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("packet_loss", sa.Float(), nullable=True),
        sa.Column("jitter_ms", sa.Float(), nullable=True),
        sa.Column("rtt_ms", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("disconnect_reason", sa.String(80), nullable=False),
        sa.Column("provider_error", sa.String(80), nullable=False),
        sa.Column("media_error", sa.String(80), nullable=False),
        sa.Column("quality_index", sa.Float(), nullable=True),
        sa.Column("quality_formula", sa.String(160), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_call_qos_samples_tenant_id", "call_qos_samples", ["tenant_id"])
    op.create_index("ix_call_qos_samples_call_id", "call_qos_samples", ["call_id"])


def downgrade() -> None:
    op.drop_table("call_qos_samples")
    op.drop_table("telephony_callback_events")
    op.drop_table("transcript_jobs")
    op.drop_table("recording_consents")
    op.drop_table("recording_policies")
    op.drop_table("call_recordings")
    op.drop_table("phone_numbers")
    op.drop_table("telephony_provider_bindings")
