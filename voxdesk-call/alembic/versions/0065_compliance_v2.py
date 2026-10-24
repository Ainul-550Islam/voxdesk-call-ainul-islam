"""Compliance control plane: SIEM, audit bundles, KMS rekey, residency, SOC 2, HIPAA.

Revision ID: 0065_compliance_v2
Revises: 0064_carrier_s2s
Create Date: 2026-10-24

Eighteen new tables, no changes to any existing table. Everything here is
additive: a downgrade drops exactly these tables and nothing else, so a
partially-applied deployment can be rolled back without touching customer data.

The ``_create`` / ``_drop`` helpers make the migration re-runnable. Alembic
should only ever run each revision once, but a restored dump or a branch merge
can leave a table behind, and a migration that fails on "already exists" is
worse than one that notices and moves on.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0065_compliance_v2"
down_revision = "0064_carrier_s2s"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    return table_name in sa.inspect(bind).get_table_names()


def _has_index(bind, table_name: str, index_name: str) -> bool:
    if not _has_table(bind, table_name):
        return False
    return any(ix["name"] == index_name for ix in sa.inspect(bind).get_indexes(table_name))


def _create(
    bind,
    table_name: str,
    columns: list[sa.Column],
    *,
    constraints: list[sa.schema.CheckConstraint] | None = None,
    unique: list[tuple[list[str], str]] | None = None,
    indexes: list[tuple[list[str], str]] | None = None,
) -> None:
    """Create one table with its checks, unique keys and indexes, if missing."""
    if not _has_table(bind, table_name):
        op.create_table(
            table_name,
            *columns,
            *(constraints or []),
            *(sa.UniqueConstraint(*cols, name=name) for cols, name in (unique or [])),
        )
    for cols, name in indexes or []:
        if not _has_index(bind, table_name, name):
            op.create_index(name, table_name, cols)


def _drop(bind, table_name: str, indexes: list[tuple[list[str], str]] | None = None) -> None:
    """Drop one table (and its indexes) if present."""
    if not _has_table(bind, table_name):
        return
    for _cols, name in indexes or []:
        if _has_index(bind, table_name, name):
            op.drop_index(name, table_name=table_name)
    op.drop_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    _create(
        bind,
        "compliance_access_review_campaigns",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("scope_key", sa.String(length=120), nullable=False, server_default="all"),
            sa.Column("reviewer_ids", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("auto_revoke", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        constraints=[
            sa.CheckConstraint("status IN ('draft', 'active', 'completed', 'cancelled')", name="ck_compliance_campaign_status"),
        ],
        unique=[
            (["tenant_id", "scope_key", "status"], "uq_compliance_campaign_scope"),
        ],
        indexes=[
            (["tenant_id"], "ix_compliance_access_review_campaigns_tenant_id"),
            (["status", "due_at"], "ix_compliance_campaign_due"),
        ],
    )

    _create(
        bind,
        "compliance_audit_exports",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
            sa.Column("range_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("range_end", sa.DateTime(timezone=True), nullable=False),
            sa.Column("event_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("chain_head", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("bundle_path", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("bundle_sha256", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("signing_key_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("manifest", sa.JSON(), nullable=False),
            sa.Column("error_message", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("requested_by", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        constraints=[
            sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_compliance_audit_export_status"),
        ],
        indexes=[
            (["tenant_id", "status"], "ix_compliance_audit_export_tenant_status"),
            (["tenant_id"], "ix_compliance_audit_exports_tenant_id"),
        ],
    )

    _create(
        bind,
        "compliance_dsar_requests",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("subject_type", sa.String(length=32), nullable=False, server_default="contact"),
            sa.Column("subject_key", sa.String(length=320), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="received"),
            sa.Column("identity_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("bundle_id", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("bundle_path", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("bundle_sha256", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("summary", sa.JSON(), nullable=False),
            sa.Column("error_message", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("requested_by", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        constraints=[
            sa.CheckConstraint("kind IN ('export', 'erase')", name="ck_compliance_dsar_kind"),
            sa.CheckConstraint("status IN ('received', 'verifying', 'in_progress', 'completed', 'rejected', 'failed')", name="ck_compliance_dsar_status"),
        ],
        indexes=[
            (["tenant_id"], "ix_compliance_dsar_requests_tenant_id"),
            (["tenant_id", "status"], "ix_compliance_dsar_tenant_status"),
        ],
    )

    _create(
        bind,
        "compliance_evidence_snapshots",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
            sa.Column("run_key", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("summary", sa.JSON(), nullable=False),
            sa.Column("file_path", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("sha256", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("pass_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("fail_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("not_configured_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("error_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        indexes=[
            (["tenant_id", "created_at"], "ix_compliance_evidence_snapshot_tenant"),
            (["tenant_id"], "ix_compliance_evidence_snapshots_tenant_id"),
        ],
    )

    _create(
        bind,
        "compliance_incidents",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
            sa.Column("reference", sa.String(length=32), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("severity", sa.String(length=8), nullable=False, server_default="sev3"),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="detected"),
            sa.Column("category", sa.String(length=64), nullable=False, server_default="security"),
            sa.Column("affected_tenants", sa.JSON(), nullable=False),
            sa.Column("personal_data_involved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("owner_user_id", sa.Uuid(), nullable=True),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("triaged_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("contained_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        constraints=[
            sa.CheckConstraint("status IN ('detected', 'triaged', 'contained', 'resolved', 'closed')", name="ck_compliance_incident_status"),
            sa.CheckConstraint("severity IN ('sev1', 'sev2', 'sev3', 'sev4')", name="ck_compliance_incident_severity"),
        ],
        unique=[
            (["reference"], "uq_compliance_incident_reference"),
        ],
        indexes=[
            (["status", "detected_at"], "ix_compliance_incident_status"),
            (["tenant_id", "detected_at"], "ix_compliance_incident_tenant"),
            (["tenant_id"], "ix_compliance_incidents_tenant_id"),
        ],
    )

    _create(
        bind,
        "compliance_kms_rekey_jobs",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
            sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("target_tables", sa.JSON(), nullable=False),
            sa.Column("cursor_table", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("cursor_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("rows_scanned", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("rows_rekeyed", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("rows_skipped", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("rows_failed", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("source_key_ids", sa.JSON(), nullable=False),
            sa.Column("target_key_id", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("error_message", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("requested_by", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        constraints=[
            sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')", name="ck_compliance_kms_rekey_status"),
        ],
        indexes=[
            (["tenant_id"], "ix_compliance_kms_rekey_jobs_tenant_id"),
            (["status", "created_at"], "ix_compliance_kms_rekey_status"),
        ],
    )

    _create(
        bind,
        "compliance_phi_policies",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("hipaa_mode", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("baa_on_file", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("baa_reference", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("redact_webhooks", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("minimum_necessary", sa.JSON(), nullable=False),
            sa.Column("updated_by", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        unique=[
            (["tenant_id"], "uq_compliance_phi_policy_tenant"),
        ],
        indexes=[
            (["tenant_id"], "ix_compliance_phi_policies_tenant_id"),
        ],
    )

    _create(
        bind,
        "compliance_residency_violations",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("pinned_region", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("requested_region", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("operation", sa.String(length=64), nullable=False),
            sa.Column("resource_type", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("resource_id", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("actor_user_id", sa.Uuid(), nullable=True),
            sa.Column("ip_address", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("blocked", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("reason", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        indexes=[
            (["tenant_id", "created_at"], "ix_compliance_residency_violation_tenant"),
            (["tenant_id"], "ix_compliance_residency_violations_tenant_id"),
        ],
    )

    _create(
        bind,
        "compliance_siem_sinks",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("endpoint", sa.String(length=500), nullable=False),
            sa.Column("secret_ref", sa.Text(), nullable=False, server_default=""),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("batch_size", sa.Integer(), nullable=False, server_default=sa.text("200")),
            sa.Column("created_by", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        constraints=[
            sa.CheckConstraint("batch_size > 0 AND batch_size <= 5000", name="ck_compliance_siem_sink_batch"),
            sa.CheckConstraint("kind IN ('splunk_hec', 'datadog_logs', 'elastic_bulk', 'syslog_cef', 's3_jsonl')", name="ck_compliance_siem_sink_kind"),
        ],
        unique=[
            (["tenant_id", "name"], "uq_compliance_siem_sink_name"),
        ],
        indexes=[
            (["tenant_id", "enabled"], "ix_compliance_siem_sink_tenant_enabled"),
            (["tenant_id"], "ix_compliance_siem_sinks_tenant_id"),
        ],
    )

    _create(
        bind,
        "compliance_tenant_residency",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("region", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="pinned"),
            sa.Column("previous_region", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("data_locations", sa.JSON(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("changed_by", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        constraints=[
            sa.CheckConstraint("status IN ('pinned', 'pending', 'unpinned')", name="ck_compliance_residency_status"),
        ],
        unique=[
            (["tenant_id"], "uq_compliance_residency_tenant"),
        ],
        indexes=[
            (["tenant_id"], "ix_compliance_tenant_residency_tenant_id"),
        ],
    )

    _create(
        bind,
        "compliance_access_review_decisions",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("campaign_id", sa.Uuid(), sa.ForeignKey("compliance_access_review_campaigns.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("role_at_review", sa.String(length=24), nullable=False, server_default="member"),
            sa.Column("decision", sa.String(length=24), nullable=False, server_default="pending"),
            sa.Column("decided_by", sa.Uuid(), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("auto_revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("note", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        constraints=[
            sa.CheckConstraint("decision IN ('pending', 'attest', 'revoke', 'not_applicable')", name="ck_compliance_review_decision"),
        ],
        unique=[
            (["campaign_id", "user_id"], "uq_compliance_review_decision"),
        ],
        indexes=[
            (["campaign_id"], "ix_compliance_access_review_decisions_campaign_id"),
            (["tenant_id"], "ix_compliance_access_review_decisions_tenant_id"),
            (["tenant_id", "decision"], "ix_compliance_review_decision_tenant"),
        ],
    )

    _create(
        bind,
        "compliance_breach_clocks",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("compliance_incidents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("jurisdiction", sa.String(length=32), nullable=False),
            sa.Column("obligation", sa.String(length=120), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
            sa.Column("reminder_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("last_reminder_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("notification_proof", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        constraints=[
            sa.CheckConstraint("status IN ('open', 'notified', 'overdue', 'closed')", name="ck_compliance_breach_clock_status"),
        ],
        unique=[
            (["incident_id", "tenant_id", "jurisdiction"], "uq_compliance_breach_clock"),
        ],
        indexes=[
            (["status", "deadline_at"], "ix_compliance_breach_clock_due"),
            (["incident_id"], "ix_compliance_breach_clocks_incident_id"),
            (["tenant_id"], "ix_compliance_breach_clocks_tenant_id"),
        ],
    )

    _create(
        bind,
        "compliance_break_glass_grants",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("requested_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("scope", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False, server_default="pending_approval"),
            sa.Column("dual_approval_required", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("approved_by", sa.Uuid(), nullable=True),
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_by", sa.Uuid(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        constraints=[
            sa.CheckConstraint("status IN ('pending_approval', 'active', 'expired', 'revoked', 'denied')", name="ck_compliance_break_glass_status"),
            sa.CheckConstraint("length(reason) >= 10", name="ck_compliance_break_glass_reason"),
        ],
        indexes=[
            (["requested_by"], "ix_compliance_break_glass_grants_requested_by"),
            (["tenant_id"], "ix_compliance_break_glass_grants_tenant_id"),
            (["tenant_id", "status"], "ix_compliance_break_glass_tenant_status"),
        ],
    )

    _create(
        bind,
        "compliance_control_status",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True),
            sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("compliance_evidence_snapshots.id", ondelete="CASCADE"), nullable=False),
            sa.Column("control_id", sa.String(length=24), nullable=False),
            sa.Column("collector", sa.String(length=80), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("detail", sa.JSON(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        constraints=[
            sa.CheckConstraint("status IN ('PASS', 'FAIL', 'NOT_CONFIGURED', 'ERROR')", name="ck_compliance_control_status"),
        ],
        unique=[
            (["snapshot_id", "control_id"], "uq_compliance_control_status"),
        ],
        indexes=[
            (["control_id", "status"], "ix_compliance_control_status_control"),
            (["snapshot_id"], "ix_compliance_control_status_snapshot_id"),
            (["tenant_id"], "ix_compliance_control_status_tenant_id"),
        ],
    )

    _create(
        bind,
        "compliance_dsar_ledger",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("request_id", sa.Uuid(), sa.ForeignKey("compliance_dsar_requests.id", ondelete="CASCADE"), nullable=False),
            sa.Column("table_name", sa.String(length=120), nullable=False),
            sa.Column("action", sa.String(length=24), nullable=False),
            sa.Column("rows_affected", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("detail", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        unique=[
            (["request_id", "table_name"], "uq_compliance_dsar_ledger"),
        ],
        indexes=[
            (["request_id"], "ix_compliance_dsar_ledger_request_id"),
            (["tenant_id", "created_at"], "ix_compliance_dsar_ledger_tenant"),
            (["tenant_id"], "ix_compliance_dsar_ledger_tenant_id"),
        ],
    )

    _create(
        bind,
        "compliance_incident_timeline",
        [
            sa.Column("tenant_id", sa.Uuid(), nullable=True),
            sa.Column("incident_id", sa.Uuid(), sa.ForeignKey("compliance_incidents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("entry_type", sa.String(length=32), nullable=False),
            sa.Column("message", sa.Text(), nullable=False, server_default=""),
            sa.Column("actor_user_id", sa.Uuid(), nullable=True),
            sa.Column("detail", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        indexes=[
            (["incident_id", "created_at"], "ix_compliance_incident_timeline_incident"),
            (["incident_id"], "ix_compliance_incident_timeline_incident_id"),
            (["tenant_id"], "ix_compliance_incident_timeline_tenant_id"),
        ],
    )

    _create(
        bind,
        "compliance_siem_cursors",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("sink_id", sa.Uuid(), sa.ForeignKey("compliance_siem_sinks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("watermark_created_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("watermark_id", sa.Uuid(), nullable=True),
            sa.Column("events_delivered", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("state", sa.String(length=24), nullable=False, server_default="idle"),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("circuit_open_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        constraints=[
            sa.CheckConstraint("state IN ('idle', 'retrying', 'circuit_open', 'dead_lettered')", name="ck_compliance_siem_cursor_state"),
        ],
        unique=[
            (["sink_id"], "uq_compliance_siem_cursor_sink"),
        ],
        indexes=[
            (["state", "next_attempt_at"], "ix_compliance_siem_cursor_due"),
            (["sink_id"], "ix_compliance_siem_cursors_sink_id"),
            (["tenant_id"], "ix_compliance_siem_cursors_tenant_id"),
        ],
    )

    _create(
        bind,
        "compliance_siem_dead_letters",
        [
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.Column("sink_id", sa.Uuid(), sa.ForeignKey("compliance_siem_sinks.id", ondelete="CASCADE"), nullable=False),
            sa.Column("audit_id", sa.Uuid(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("error_category", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("error_message", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("redrive_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("redriven_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        ],
        unique=[
            (["sink_id", "audit_id"], "uq_compliance_siem_dlq_event"),
        ],
        indexes=[
            (["sink_id"], "ix_compliance_siem_dead_letters_sink_id"),
            (["tenant_id"], "ix_compliance_siem_dead_letters_tenant_id"),
            (["tenant_id", "created_at"], "ix_compliance_siem_dlq_tenant"),
        ],
    )


TABLES = [
    "compliance_siem_dead_letters",
    "compliance_siem_cursors",
    "compliance_incident_timeline",
    "compliance_dsar_ledger",
    "compliance_control_status",
    "compliance_break_glass_grants",
    "compliance_breach_clocks",
    "compliance_access_review_decisions",
    "compliance_tenant_residency",
    "compliance_siem_sinks",
    "compliance_residency_violations",
    "compliance_phi_policies",
    "compliance_kms_rekey_jobs",
    "compliance_incidents",
    "compliance_evidence_snapshots",
    "compliance_dsar_requests",
    "compliance_audit_exports",
    "compliance_access_review_campaigns",
]


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in TABLES:
        _drop(bind, table_name)
