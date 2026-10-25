"""Add Batch 04 contact-center persistence.

Revision ID: 0066_contact_center_v2
Revises: 0065_compliance_v2
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0066_contact_center_v2"
down_revision = "0065_compliance_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.create_table(
        'cc2_acd_policies',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('queue_id', sa.UUID(), sa.ForeignKey('cc_queues.id', ondelete='CASCADE'), nullable=False),
        sa.Column('overflow_kind', sa.String(length=16), nullable=False),
        sa.Column('overflow_after_seconds', sa.Integer(), nullable=False),
        sa.Column('overflow_waiting', sa.Integer(), nullable=False),
        sa.Column('overflow_queue_id', sa.UUID(), sa.ForeignKey('cc_queues.id', ondelete='SET NULL'), nullable=True),
        sa.Column('callback_enabled', sa.Boolean(), nullable=False),
        sa.Column('last_overflow_sweep_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('tenant_id', 'queue_id', name='uq_cc2_acd_policy_queue'),
        sa.CheckConstraint("overflow_kind IN ('none','callback','escalate','voicemail','queue')", name='ck_cc2_acd_overflow_kind'),
    )
    op.create_index('ix_cc2_acd_policies_environment_id', 'cc2_acd_policies', ['environment_id'], unique=False)
    op.create_index('ix_cc2_acd_policies_tenant_id', 'cc2_acd_policies', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_agent_reservations',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('user_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('resource_id', sa.UUID(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('active_key', sa.String(length=100), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.CheckConstraint("kind IN ('dialer','softphone','callback')", name='ck_cc2_agent_reservation_kind'),
        sa.UniqueConstraint('active_key', name='uq_cc2_agent_reservation_active'),
        sa.CheckConstraint("status IN ('active','released','expired')", name='ck_cc2_agent_reservation_status'),
    )
    op.create_index('ix_cc2_agent_reservation_agent', 'cc2_agent_reservations', ['tenant_id', 'user_id', 'status'], unique=False)
    op.create_index('ix_cc2_agent_reservations_environment_id', 'cc2_agent_reservations', ['environment_id'], unique=False)
    op.create_index('ix_cc2_agent_reservations_tenant_id', 'cc2_agent_reservations', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_agent_state_periods',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('user_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('open_key', sa.String(length=100), nullable=True),
        sa.Column('source_id', sa.String(length=80), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.CheckConstraint("state IN ('offline','available','ringing','busy','hold','wrap_up','away','paused','disabled')", name='ck_cc2_agent_period_state'),
        sa.UniqueConstraint('open_key', name='uq_cc2_agent_state_open'),
    )
    op.create_index('ix_cc2_agent_period_accounting', 'cc2_agent_state_periods', ['tenant_id', 'user_id', 'started_at', 'ended_at'], unique=False)
    op.create_index('ix_cc2_agent_state_periods_environment_id', 'cc2_agent_state_periods', ['environment_id'], unique=False)
    op.create_index('ix_cc2_agent_state_periods_tenant_id', 'cc2_agent_state_periods', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_agent_devices',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('user_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('fingerprint_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('tenant_id', 'user_id', 'fingerprint_hash', name='uq_cc2_device_fingerprint'),
        sa.CheckConstraint("kind IN ('browser','desk_phone')", name='ck_cc2_device_kind'),
        sa.CheckConstraint("status IN ('active','revoked','disabled')", name='ck_cc2_device_status'),
    )
    op.create_index('ix_cc2_device_agent', 'cc2_agent_devices', ['tenant_id', 'user_id', 'status'], unique=False)
    op.create_index('ix_cc2_agent_devices_environment_id', 'cc2_agent_devices', ['environment_id'], unique=False)
    op.create_index('ix_cc2_agent_devices_tenant_id', 'cc2_agent_devices', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_softphone_sessions',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('user_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('device_id', sa.UUID(), sa.ForeignKey('cc2_agent_devices.id', ondelete='SET NULL'), nullable=True),
        sa.Column('active_device_key', sa.String(length=100), nullable=True),
        sa.Column('registration_key', sa.String(length=128), nullable=False),
        sa.Column('request_digest', sa.String(length=64), nullable=False),
        sa.Column('client_identity', sa.String(length=80), nullable=False),
        sa.Column('device_label', sa.String(length=100), nullable=False),
        sa.Column('ws_ticket_hash', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('last_heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('client_identity', name='uq_cc2_softphone_identity'),
        sa.UniqueConstraint('active_device_key', name='uq_cc2_softphone_active_device'),
        sa.CheckConstraint("status IN ('active','expired','revoked')", name='ck_cc2_softphone_session_status'),
        sa.UniqueConstraint('tenant_id', 'registration_key', name='uq_cc2_softphone_registration'),
    )
    op.create_index('ix_cc2_softphone_agent', 'cc2_softphone_sessions', ['tenant_id', 'user_id', 'status', 'expires_at'], unique=False)
    op.create_index('ix_cc2_softphone_sessions_environment_id', 'cc2_softphone_sessions', ['environment_id'], unique=False)
    op.create_index('ix_cc2_softphone_sessions_tenant_id', 'cc2_softphone_sessions', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_dialer_policies',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('campaign_id', sa.UUID(), sa.ForeignKey('campaigns.id', ondelete='CASCADE'), nullable=False),
        sa.Column('mode', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('caller_id_number', sa.String(length=32), nullable=False),
        sa.Column('pacing_ratio', sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column('abandonment_cap', sa.Numeric(precision=6, scale=5), nullable=False),
        sa.Column('max_inflight', sa.Integer(), nullable=False),
        sa.Column('calling_windows', sa.JSON(), nullable=False),
        sa.Column('holiday_dates', sa.JSON(), nullable=False),
        sa.Column('safe_harbor_message', sa.Text(), nullable=False),
        sa.Column('consent_required', sa.Boolean(), nullable=False),
        sa.Column('jurisdiction', sa.String(length=32), nullable=False),
        sa.Column('dnc_scrubbed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('dnc_scrub_version', sa.String(length=80), nullable=False),
        sa.Column('prepared_lead_count', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('stopped_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.CheckConstraint('abandonment_cap > 0 AND abandonment_cap <= 1', name='ck_cc2_dialer_cap'),
        sa.CheckConstraint('pacing_ratio > 0', name='ck_cc2_dialer_ratio'),
        sa.UniqueConstraint('tenant_id', 'campaign_id', name='uq_cc2_dialer_policy_campaign'),
        sa.CheckConstraint("mode IN ('predictive','progressive','preview','ai_agent_blast')", name='ck_cc2_dialer_mode'),
        sa.CheckConstraint("status IN ('draft','running','paused','completed','stopped')", name='ck_cc2_dialer_status'),
        sa.CheckConstraint('max_inflight >= 1', name='ck_cc2_dialer_inflight'),
    )
    op.create_index('ix_cc2_dialer_policies_environment_id', 'cc2_dialer_policies', ['environment_id'], unique=False)
    op.create_index('ix_cc2_dialer_policies_tenant_id', 'cc2_dialer_policies', ['tenant_id'], unique=False)
    op.create_index('ix_cc2_dialer_policy_status', 'cc2_dialer_policies', ['tenant_id', 'status'], unique=False)
    op.create_table(
        'cc2_dialer_contacts',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('policy_id', sa.UUID(), sa.ForeignKey('cc2_dialer_policies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('lead_id', sa.UUID(), sa.ForeignKey('leads.id', ondelete='CASCADE'), nullable=False),
        sa.Column('state', sa.String(length=24), nullable=False),
        sa.Column('block_reason', sa.String(length=64), nullable=False),
        sa.Column('recipient_timezone', sa.String(length=64), nullable=False),
        sa.Column('dnc_scrubbed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('dnc_scrub_version', sa.String(length=80), nullable=False),
        sa.Column('scrub_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('consent_version', sa.Integer(), nullable=True),
        sa.Column('consent_recorded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('assigned_agent_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('preview_lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('active_attempt_id', sa.UUID(), nullable=True),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('policy_id', 'lead_id', name='uq_cc2_dialer_contact_lead'),
        sa.CheckConstraint("state IN ('pending','eligible','blocked','preview_assigned','dialing','retry_scheduled','completed','skipped')", name='ck_cc2_dialer_contact_state'),
    )
    op.create_index('ix_cc2_dialer_contact_agent', 'cc2_dialer_contacts', ['tenant_id', 'assigned_agent_id'], unique=False)
    op.create_index('ix_cc2_dialer_contact_due', 'cc2_dialer_contacts', ['tenant_id', 'policy_id', 'state', 'next_attempt_at'], unique=False)
    op.create_index('ix_cc2_dialer_contacts_environment_id', 'cc2_dialer_contacts', ['environment_id'], unique=False)
    op.create_index('ix_cc2_dialer_contacts_tenant_id', 'cc2_dialer_contacts', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_dialer_overrides',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('policy_id', sa.UUID(), sa.ForeignKey('cc2_dialer_policies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('jurisdiction', sa.String(length=32), nullable=False),
        sa.Column('abandonment_cap', sa.Numeric(precision=6, scale=5), nullable=False),
        sa.Column('legal_reference', sa.String(length=500), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('approved_by', sa.UUID(), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('tenant_id', 'policy_id', 'jurisdiction', 'created_at', name='uq_cc2_dialer_override_version'),
        sa.CheckConstraint('abandonment_cap > 0.03 AND abandonment_cap <= 0.10', name='ck_cc2_dialer_override_cap'),
    )
    op.create_index('ix_cc2_dialer_override_lookup', 'cc2_dialer_overrides', ['tenant_id', 'policy_id', 'jurisdiction', 'expires_at'], unique=False)
    op.create_index('ix_cc2_dialer_overrides_environment_id', 'cc2_dialer_overrides', ['environment_id'], unique=False)
    op.create_index('ix_cc2_dialer_overrides_tenant_id', 'cc2_dialer_overrides', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_assist_sessions',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('call_id', sa.UUID(), sa.ForeignKey('calls.id', ondelete='CASCADE'), nullable=False),
        sa.Column('agent_user_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('active_key', sa.String(length=80), nullable=True),
        sa.Column('last_seq', sa.Integer(), nullable=False),
        sa.Column('failure_category', sa.String(length=64), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.CheckConstraint("status IN ('active','ended','failed')", name='ck_cc2_assist_session_status'),
        sa.UniqueConstraint('active_key', name='uq_cc2_assist_active_call'),
    )
    op.create_index('ix_cc2_assist_session_agent', 'cc2_assist_sessions', ['tenant_id', 'agent_user_id', 'status'], unique=False)
    op.create_index('ix_cc2_assist_sessions_environment_id', 'cc2_assist_sessions', ['environment_id'], unique=False)
    op.create_index('ix_cc2_assist_sessions_tenant_id', 'cc2_assist_sessions', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_dialer_attempts',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('policy_id', sa.UUID(), sa.ForeignKey('cc2_dialer_policies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('contact_id', sa.UUID(), sa.ForeignKey('cc2_dialer_contacts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('lead_id', sa.UUID(), sa.ForeignKey('leads.id', ondelete='CASCADE'), nullable=False),
        sa.Column('call_id', sa.UUID(), sa.ForeignKey('calls.id', ondelete='SET NULL'), nullable=True),
        sa.Column('mode', sa.String(length=16), nullable=False),
        sa.Column('outcome', sa.String(length=24), nullable=False),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('provider_call_id', sa.String(length=80), nullable=True),
        sa.Column('agent_user_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('answered_by', sa.String(length=40), nullable=False),
        sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('agent_connected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('abandoned_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('abandonment_counted', sa.Boolean(), nullable=False),
        sa.Column('safe_harbor_requested_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('safe_harbor_played_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_seconds', sa.Integer(), nullable=False),
        sa.Column('failure_category', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('tenant_id', 'idempotency_key', name='uq_cc2_dialer_attempt_key'),
        sa.UniqueConstraint('provider_call_id', name='uq_cc2_dialer_attempt_provider'),
        sa.CheckConstraint("mode IN ('predictive','progressive','preview','ai_agent_blast')", name='ck_cc2_dialer_attempt_mode'),
        sa.CheckConstraint("outcome IN ('initiated','ringing','answered','machine','waiting_for_agent','connected','abandoned','failed','completed','cancelled')", name='ck_cc2_dialer_attempt_outcome'),
    )
    op.create_index('ix_cc2_dialer_attempt_abandon', 'cc2_dialer_attempts', ['tenant_id', 'policy_id', 'answered_at', 'abandoned_at'], unique=False)
    op.create_index('ix_cc2_dialer_attempt_contact', 'cc2_dialer_attempts', ['tenant_id', 'contact_id'], unique=False)
    op.create_index('ix_cc2_dialer_attempt_policy', 'cc2_dialer_attempts', ['tenant_id', 'policy_id', 'created_at'], unique=False)
    op.create_index('ix_cc2_dialer_attempts_environment_id', 'cc2_dialer_attempts', ['environment_id'], unique=False)
    op.create_index('ix_cc2_dialer_attempts_tenant_id', 'cc2_dialer_attempts', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_abandon_events',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('campaign_id', sa.UUID(), sa.ForeignKey('campaigns.id', ondelete='CASCADE'), nullable=False),
        sa.Column('policy_id', sa.UUID(), sa.ForeignKey('cc2_dialer_policies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('attempt_id', sa.UUID(), sa.ForeignKey('cc2_dialer_attempts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('call_id', sa.UUID(), sa.ForeignKey('calls.id', ondelete='SET NULL'), nullable=True),
        sa.Column('event_type', sa.String(length=24), nullable=False),
        sa.Column('numerator', sa.Boolean(), nullable=False),
        sa.Column('denominator', sa.Boolean(), nullable=False),
        sa.Column('connected_latency_ms', sa.Integer(), nullable=True),
        sa.Column('reason', sa.String(length=80), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('attempt_id', 'event_type', name='uq_cc2_abandon_attempt_event'),
        sa.CheckConstraint("event_type IN ('human_answered','agent_connected','abandoned','machine_answered','safe_harbor')", name='ck_cc2_abandon_event_type'),
    )
    op.create_index('ix_cc2_abandon_rollup', 'cc2_abandon_events', ['tenant_id', 'campaign_id', 'occurred_at', 'event_type'], unique=False)
    op.create_index('ix_cc2_abandon_events_environment_id', 'cc2_abandon_events', ['environment_id'], unique=False)
    op.create_index('ix_cc2_abandon_events_tenant_id', 'cc2_abandon_events', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_pacing_snapshots',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('campaign_id', sa.UUID(), sa.ForeignKey('campaigns.id', ondelete='CASCADE'), nullable=False),
        sa.Column('policy_id', sa.UUID(), sa.ForeignKey('cc2_dialer_policies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('mode', sa.String(length=16), nullable=False),
        sa.Column('answer_rate', sa.Numeric(precision=7, scale=6), nullable=False),
        sa.Column('occupancy', sa.Numeric(precision=7, scale=6), nullable=False),
        sa.Column('dial_ratio', sa.Numeric(precision=7, scale=3), nullable=False),
        sa.Column('projected_abandon_rate', sa.Numeric(precision=7, scale=6), nullable=False),
        sa.Column('available_agents', sa.Integer(), nullable=False),
        sa.Column('soon_available_agents', sa.Numeric(precision=7, scale=3), nullable=False),
        sa.Column('sample_size', sa.Integer(), nullable=False),
        sa.Column('throttled_to_progressive', sa.Boolean(), nullable=False),
        sa.Column('reason', sa.String(length=80), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.CheckConstraint("mode IN ('predictive','progressive','preview','ai_agent_blast')", name='ck_cc2_pacing_mode'),
        sa.CheckConstraint('dial_ratio >= 0', name='ck_cc2_pacing_ratio'),
        sa.CheckConstraint('projected_abandon_rate >= 0 AND projected_abandon_rate <= 1', name='ck_cc2_pacing_projected'),
    )
    op.create_index('ix_cc2_pacing_campaign_time', 'cc2_pacing_snapshots', ['tenant_id', 'campaign_id', 'created_at'], unique=False)
    op.create_index('ix_cc2_pacing_snapshots_environment_id', 'cc2_pacing_snapshots', ['environment_id'], unique=False)
    op.create_index('ix_cc2_pacing_snapshots_tenant_id', 'cc2_pacing_snapshots', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_preview_reservations',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('policy_id', sa.UUID(), sa.ForeignKey('cc2_dialer_policies.id', ondelete='CASCADE'), nullable=False),
        sa.Column('contact_id', sa.UUID(), sa.ForeignKey('cc2_dialer_contacts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('lead_id', sa.UUID(), sa.ForeignKey('leads.id', ondelete='CASCADE'), nullable=False),
        sa.Column('agent_user_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('active_key', sa.String(length=100), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('disposition', sa.String(length=80), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('active_key', name='uq_cc2_preview_active_contact'),
        sa.CheckConstraint("status IN ('active','dialed','released','expired','skipped')", name='ck_cc2_preview_status'),
    )
    op.create_index('ix_cc2_preview_agent', 'cc2_preview_reservations', ['tenant_id', 'agent_user_id', 'status', 'expires_at'], unique=False)
    op.create_index('ix_cc2_preview_reservations_environment_id', 'cc2_preview_reservations', ['environment_id'], unique=False)
    op.create_index('ix_cc2_preview_reservations_tenant_id', 'cc2_preview_reservations', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_assist_turns',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('session_id', sa.UUID(), sa.ForeignKey('cc2_assist_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('call_id', sa.UUID(), sa.ForeignKey('calls.id', ondelete='CASCADE'), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('speaker', sa.String(length=16), nullable=False),
        sa.Column('text_redacted', sa.Text(), nullable=False),
        sa.Column('processing_status', sa.String(length=24), nullable=False),
        sa.Column('sentiment_score', sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column('sentiment_label', sa.String(length=16), nullable=False),
        sa.Column('sentiment_method', sa.String(length=32), nullable=False),
        sa.Column('failure_category', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('call_id', 'speaker', 'seq', name='uq_cc2_assist_turn_sequence'),
        sa.CheckConstraint("speaker IN ('caller','agent','system','supervisor')", name='ck_cc2_assist_turn_speaker'),
        sa.CheckConstraint("processing_status IN ('queued','retrieval_only','completed','no_evidence','failed')", name='ck_cc2_assist_turn_status'),
    )
    op.create_index('ix_cc2_assist_turn_call', 'cc2_assist_turns', ['tenant_id', 'call_id', 'created_at'], unique=False)
    op.create_index('ix_cc2_assist_turns_environment_id', 'cc2_assist_turns', ['environment_id'], unique=False)
    op.create_index('ix_cc2_assist_turns_tenant_id', 'cc2_assist_turns', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_callbacks',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('queue_entry_id', sa.UUID(), sa.ForeignKey('cc_queue_entries.id', ondelete='CASCADE'), nullable=False),
        sa.Column('queue_id', sa.UUID(), sa.ForeignKey('cc_queues.id', ondelete='CASCADE'), nullable=False),
        sa.Column('call_id', sa.UUID(), sa.ForeignKey('calls.id', ondelete='CASCADE'), nullable=False),
        sa.Column('requested_by', sa.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('assigned_agent_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('job_id', sa.UUID(), sa.ForeignKey('jobs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('timezone', sa.String(length=64), nullable=False),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('note', sa.String(length=500), nullable=False),
        sa.Column('failure_category', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.CheckConstraint("status IN ('scheduled','running','completed','failed','cancelled')", name='ck_cc2_callback_status'),
        sa.UniqueConstraint('tenant_id', 'queue_entry_id', name='uq_cc2_callback_entry'),
    )
    op.create_index('ix_cc2_callback_due', 'cc2_callbacks', ['tenant_id', 'status', 'scheduled_at'], unique=False)
    op.create_index('ix_cc2_callbacks_environment_id', 'cc2_callbacks', ['environment_id'], unique=False)
    op.create_index('ix_cc2_callbacks_tenant_id', 'cc2_callbacks', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_assist_alerts',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('call_id', sa.UUID(), sa.ForeignKey('calls.id', ondelete='CASCADE'), nullable=False),
        sa.Column('turn_id', sa.UUID(), sa.ForeignKey('cc2_assist_turns.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('severity', sa.String(length=16), nullable=False),
        sa.Column('message', sa.String(length=500), nullable=False),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.CheckConstraint("kind IN ('negative_sentiment','compliance_review')", name='ck_cc2_assist_alert_kind'),
        sa.CheckConstraint("status IN ('open','acknowledged','resolved')", name='ck_cc2_assist_alert_status'),
        sa.UniqueConstraint('tenant_id', 'turn_id', 'kind', name='uq_cc2_assist_alert_turn_kind'),
    )
    op.create_index('ix_cc2_assist_alert_call', 'cc2_assist_alerts', ['tenant_id', 'call_id', 'status', 'created_at'], unique=False)
    op.create_index('ix_cc2_assist_alerts_environment_id', 'cc2_assist_alerts', ['environment_id'], unique=False)
    op.create_index('ix_cc2_assist_alerts_tenant_id', 'cc2_assist_alerts', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_assist_suggestions',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('session_id', sa.UUID(), sa.ForeignKey('cc2_assist_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('call_id', sa.UUID(), sa.ForeignKey('calls.id', ondelete='CASCADE'), nullable=False),
        sa.Column('turn_id', sa.UUID(), sa.ForeignKey('cc2_assist_turns.id', ondelete='CASCADE'), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('citations', sa.JSON(), nullable=False),
        sa.Column('generation_method', sa.String(length=32), nullable=False),
        sa.Column('model', sa.String(length=160), nullable=False),
        sa.Column('feedback', sa.String(length=16), nullable=False),
        sa.Column('feedback_note', sa.String(length=500), nullable=False),
        sa.Column('feedback_by', sa.UUID(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('feedback_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.CheckConstraint("kind IN ('knowledge','next_best_action')", name='ck_cc2_assist_suggestion_kind'),
        sa.UniqueConstraint('tenant_id', 'turn_id', 'kind', name='uq_cc2_assist_suggestion_kind'),
        sa.CheckConstraint("feedback IN ('','accepted','dismissed','helpful','not_helpful')", name='ck_cc2_assist_suggestion_feedback'),
    )
    op.create_index('ix_cc2_assist_suggestion_call', 'cc2_assist_suggestions', ['tenant_id', 'call_id', 'created_at'], unique=False)
    op.create_index('ix_cc2_assist_suggestions_environment_id', 'cc2_assist_suggestions', ['environment_id'], unique=False)
    op.create_index('ix_cc2_assist_suggestions_tenant_id', 'cc2_assist_suggestions', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_assist_feedback',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('suggestion_id', sa.UUID(), sa.ForeignKey('cc2_assist_suggestions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('call_id', sa.UUID(), sa.ForeignKey('calls.id', ondelete='CASCADE'), nullable=False),
        sa.Column('agent_user_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('action', sa.String(length=16), nullable=False),
        sa.Column('note_redacted', sa.String(length=500), nullable=False),
        sa.Column('shown_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('response_latency_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('suggestion_id', 'agent_user_id', name='uq_cc2_feedback_suggestion_agent'),
        sa.CheckConstraint("action IN ('shown','accepted','ignored','dismissed','helpful','not_helpful')", name='ck_cc2_feedback_action'),
    )
    op.create_index('ix_cc2_feedback_call', 'cc2_assist_feedback', ['tenant_id', 'call_id', 'created_at'], unique=False)
    op.create_index('ix_cc2_assist_feedback_environment_id', 'cc2_assist_feedback', ['environment_id'], unique=False)
    op.create_index('ix_cc2_assist_feedback_tenant_id', 'cc2_assist_feedback', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_softphone_calls',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('call_id', sa.UUID(), sa.ForeignKey('calls.id', ondelete='SET NULL'), nullable=True),
        sa.Column('session_id', sa.UUID(), sa.ForeignKey('cc2_softphone_sessions.id', ondelete='SET NULL'), nullable=True),
        sa.Column('agent_user_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('origin', sa.String(length=24), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('conference_name', sa.String(length=100), nullable=False),
        sa.Column('provider_conference_id', sa.String(length=80), nullable=True),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('dialer_attempt_id', sa.UUID(), sa.ForeignKey('cc2_dialer_attempts.id', ondelete='SET NULL'), nullable=True),
        sa.Column('callback_id', sa.UUID(), sa.ForeignKey('cc2_callbacks.id', ondelete='SET NULL'), nullable=True),
        sa.Column('acd_entry_id', sa.UUID(), sa.ForeignKey('cc_queue_entries.id', ondelete='SET NULL'), nullable=True),
        sa.Column('muted', sa.Boolean(), nullable=False),
        sa.Column('on_hold', sa.Boolean(), nullable=False),
        sa.Column('agent_join_deadline', sa.DateTime(timezone=True), nullable=True),
        sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failure_category', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('conference_name', name='uq_cc2_softphone_conference'),
        sa.CheckConstraint("status IN ('pending','dialing','waiting_for_agent','in_progress','held','completed','failed','cancelled','transferred')", name='ck_cc2_softphone_call_status'),
        sa.UniqueConstraint('tenant_id', 'idempotency_key', name='uq_cc2_softphone_call_key'),
        sa.CheckConstraint("origin IN ('agent_outbound','acd','dialer','callback')", name='ck_cc2_softphone_origin'),
    )
    op.create_index('ix_cc2_softphone_agent_calls', 'cc2_softphone_calls', ['tenant_id', 'agent_user_id', 'status', 'created_at'], unique=False)
    op.create_index('ix_cc2_softphone_call_record', 'cc2_softphone_calls', ['tenant_id', 'call_id'], unique=False)
    op.create_index('ix_cc2_softphone_calls_environment_id', 'cc2_softphone_calls', ['environment_id'], unique=False)
    op.create_index('ix_cc2_softphone_calls_tenant_id', 'cc2_softphone_calls', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_softphone_legs',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('softphone_call_id', sa.UUID(), sa.ForeignKey('cc2_softphone_calls.id', ondelete='CASCADE'), nullable=False),
        sa.Column('call_id', sa.UUID(), sa.ForeignKey('calls.id', ondelete='SET NULL'), nullable=True),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('provider_call_id', sa.String(length=80), nullable=True),
        sa.Column('idempotency_key', sa.String(length=128), nullable=False),
        sa.Column('destination_redacted', sa.String(length=40), nullable=False),
        sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('tenant_id', 'idempotency_key', name='uq_cc2_softphone_leg_key'),
        sa.CheckConstraint("kind IN ('customer','agent','consult')", name='ck_cc2_softphone_leg_kind'),
        sa.CheckConstraint("status IN ('pending','ringing','in_progress','completed','failed','cancelled')", name='ck_cc2_softphone_leg_status'),
        sa.UniqueConstraint('provider_call_id', name='uq_cc2_softphone_leg_provider'),
    )
    op.create_index('ix_cc2_softphone_leg_call', 'cc2_softphone_legs', ['tenant_id', 'softphone_call_id', 'kind'], unique=False)
    op.create_index('ix_cc2_softphone_legs_environment_id', 'cc2_softphone_legs', ['environment_id'], unique=False)
    op.create_index('ix_cc2_softphone_legs_tenant_id', 'cc2_softphone_legs', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_compliance_scripts',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('jurisdiction', sa.String(length=32), nullable=False),
        sa.Column('channel', sa.String(length=16), nullable=False),
        sa.Column('script_text', sa.Text(), nullable=False),
        sa.Column('required_phrases', sa.JSON(), nullable=False),
        sa.Column('forbidden_phrases', sa.JSON(), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.UUID(), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('tenant_id', 'environment_id', 'name', 'version', name='uq_cc2_script_version'),
        sa.CheckConstraint('version >= 1', name='ck_cc2_script_version'),
    )
    op.create_index('ix_cc2_compliance_scripts_environment_id', 'cc2_compliance_scripts', ['environment_id'], unique=False)
    op.create_index('ix_cc2_compliance_scripts_tenant_id', 'cc2_compliance_scripts', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_compliance_adherence',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('call_id', sa.UUID(), sa.ForeignKey('calls.id', ondelete='CASCADE'), nullable=False),
        sa.Column('script_id', sa.UUID(), sa.ForeignKey('cc2_compliance_scripts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('agent_user_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('matched_phrases', sa.JSON(), nullable=False),
        sa.Column('missed_phrases', sa.JSON(), nullable=False),
        sa.Column('violated_phrases', sa.JSON(), nullable=False),
        sa.Column('evidence_redacted', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('call_id', 'script_id', name='uq_cc2_adherence_call_script'),
        sa.CheckConstraint("status IN ('pending','met','missed','violated')", name='ck_cc2_adherence_status'),
    )
    op.create_index('ix_cc2_adherence_agent', 'cc2_compliance_adherence', ['tenant_id', 'agent_user_id', 'status', 'updated_at'], unique=False)
    op.create_index('ix_cc2_compliance_adherence_environment_id', 'cc2_compliance_adherence', ['environment_id'], unique=False)
    op.create_index('ix_cc2_compliance_adherence_tenant_id', 'cc2_compliance_adherence', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_wrapup_drafts',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('call_id', sa.UUID(), sa.ForeignKey('calls.id', ondelete='CASCADE'), nullable=False),
        sa.Column('agent_user_id', sa.UUID(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('summary_redacted', sa.Text(), nullable=False),
        sa.Column('disposition', sa.String(length=80), nullable=False),
        sa.Column('tags', sa.JSON(), nullable=False),
        sa.Column('crm_status', sa.String(length=16), nullable=False),
        sa.Column('crm_reference', sa.String(length=120), nullable=False),
        sa.Column('failure_category', sa.String(length=80), nullable=False),
        sa.Column('generation_method', sa.String(length=32), nullable=False),
        sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('call_id', 'agent_user_id', name='uq_cc2_wrapup_call_agent'),
        sa.CheckConstraint("status IN ('draft','submitted','failed')", name='ck_cc2_wrapup_status'),
        sa.CheckConstraint("crm_status IN ('not_requested','pending','written','failed')", name='ck_cc2_wrapup_crm_status'),
    )
    op.create_index('ix_cc2_wrapup_drafts_environment_id', 'cc2_wrapup_drafts', ['environment_id'], unique=False)
    op.create_index('ix_cc2_wrapup_drafts_tenant_id', 'cc2_wrapup_drafts', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_queue_skill_requirements',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('queue_id', sa.UUID(), sa.ForeignKey('cc_queues.id', ondelete='CASCADE'), nullable=False),
        sa.Column('skill_id', sa.UUID(), sa.ForeignKey('cc_skills.id', ondelete='CASCADE'), nullable=False),
        sa.Column('min_proficiency', sa.Integer(), nullable=False),
        sa.Column('required', sa.Boolean(), nullable=False),
        sa.Column('weight', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('queue_id', 'skill_id', name='uq_cc2_queue_skill'),
        sa.CheckConstraint('min_proficiency >= 1 AND min_proficiency <= 5', name='ck_cc2_queue_skill_proficiency'),
    )
    op.create_index('ix_cc2_queue_skill_requirements_environment_id', 'cc2_queue_skill_requirements', ['environment_id'], unique=False)
    op.create_index('ix_cc2_queue_skill_requirements_tenant_id', 'cc2_queue_skill_requirements', ['tenant_id'], unique=False)
    op.create_table(
        'cc2_queue_priority_configs',
        sa.Column('id', sa.UUID(), nullable=False, primary_key=True),
        sa.Column('queue_id', sa.UUID(), sa.ForeignKey('cc_queues.id', ondelete='CASCADE'), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('strategy', sa.String(length=32), nullable=False),
        sa.Column('overflow_chain', sa.JSON(), nullable=False),
        sa.Column('max_wait_seconds', sa.Integer(), nullable=False),
        sa.Column('fallback_kind', sa.String(length=16), nullable=False),
        sa.Column('ewt_config', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('tenant_id', sa.UUID(), sa.ForeignKey('tenants.id', ondelete='CASCADE'), nullable=False),
        sa.Column('environment_id', sa.UUID(), sa.ForeignKey('environments.id', ondelete='RESTRICT'), nullable=False),
        sa.UniqueConstraint('tenant_id', 'queue_id', name='uq_cc2_queue_priority'),
        sa.CheckConstraint('priority >= 0', name='ck_cc2_queue_priority_value'),
        sa.CheckConstraint('max_wait_seconds >= 1', name='ck_cc2_queue_max_wait'),
        sa.CheckConstraint("fallback_kind IN ('ai_agent','voicemail','callback','queue')", name='ck_cc2_queue_fallback'),
    )
    op.create_index('ix_cc2_queue_priority_configs_environment_id', 'cc2_queue_priority_configs', ['environment_id'], unique=False)
    op.create_index('ix_cc2_queue_priority_configs_tenant_id', 'cc2_queue_priority_configs', ['tenant_id'], unique=False)


def downgrade() -> None:
    # Reverse dependency order; dropping each table also drops its indexes.

    op.drop_table('cc2_queue_priority_configs')
    op.drop_table('cc2_queue_skill_requirements')
    op.drop_table('cc2_wrapup_drafts')
    op.drop_table('cc2_compliance_adherence')
    op.drop_table('cc2_compliance_scripts')
    op.drop_table('cc2_assist_feedback')
    op.drop_table('cc2_preview_reservations')
    op.drop_table('cc2_pacing_snapshots')
    op.drop_table('cc2_abandon_events')
    op.drop_table('cc2_softphone_legs')
    op.drop_table('cc2_softphone_calls')
    op.drop_table('cc2_assist_suggestions')
    op.drop_table('cc2_assist_alerts')
    op.drop_table('cc2_callbacks')
    op.drop_table('cc2_assist_turns')
    op.drop_table('cc2_dialer_attempts')
    op.drop_table('cc2_assist_sessions')
    op.drop_table('cc2_dialer_overrides')
    op.drop_table('cc2_dialer_contacts')
    op.drop_table('cc2_dialer_policies')
    op.drop_table('cc2_softphone_sessions')
    op.drop_table('cc2_agent_devices')
    op.drop_table('cc2_agent_state_periods')
    op.drop_table('cc2_agent_reservations')
    op.drop_table('cc2_acd_policies')
