"""Add the platform-v2 schema: workforce management, white-label and mobile push.

Revision ID: 0068_platform_v2
Revises: 0067_integrations_v2
Create Date: 2026-10-26

Twenty-six tables in three groups:

* **Workforce** (13) — interval history, forecasts and their accuracy, shift
  templates, labour rules, schedules and immutable versions, shifts, time-off
  requests and balances, swaps, open-shift bids, adherence exceptions.
* **White-label** (8) — custom domains with a global uniqueness constraint and a
  hashed ownership token, per-check DNS evidence, TLS certificate state, brand
  themes and their versions, brand assets, sender domains, sender DNS evidence.
* **Mobile** (5) — devices, push tokens, push deliveries, PKCE authorization codes
  and rotating refresh tokens with a session family for reuse detection.

Three constraints carry security meaning and are therefore in the schema rather
than only in a service:

* ``uq_wl_custom_domains_domain`` is unique on ``domain`` **alone**. A hostname
  belongs to exactly one tenant, so an unknown or duplicated Host can never fall
  back to a default tenant.
* ``uq_wl_brand_themes_active`` (``tenant_id``, ``active_key``) makes "one active
  theme per tenant" a database fact; ``active_key`` is ``'active'`` or NULL so the
  uniqueness holds on every backend.
* ``queue_key`` is non-nullable with ``''`` meaning "the whole environment",
  because NULL never compares equal inside a unique constraint.

``wfm_forecast_accuracy`` deliberately stores no "target" column: accuracy is
computed from stored actuals at request time and is never configured.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0068_platform_v2"
down_revision = "0067_integrations_v2"
branch_labels = None
depends_on = None


def _has_table(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "wfm_interval_history"):
        op.create_table(
            "wfm_interval_history",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("queue_key", sa.String(length=36), nullable=False),
            sa.Column("interval_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("interval_minutes", sa.Integer(), nullable=False),
            sa.Column("timezone", sa.String(length=64), nullable=False),
            sa.Column("offered", sa.Integer(), nullable=False),
            sa.Column("handled", sa.Integer(), nullable=False),
            sa.Column("abandoned", sa.Integer(), nullable=False),
            sa.Column("total_handle_seconds", sa.Numeric(precision=14, scale=2), nullable=False),
            sa.Column("total_speed_answer_seconds", sa.Numeric(precision=14, scale=2), nullable=False),
            sa.Column("agents_scheduled", sa.Integer(), nullable=False),
            sa.Column("agents_available", sa.Integer(), nullable=False),
            sa.Column("is_gap", sa.Boolean(), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("environment_id", sa.Uuid(), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("interval_minutes IN (15,30)", name="ck_wfm_history_interval_minutes"),
            sa.CheckConstraint("offered >= 0 AND handled >= 0 AND abandoned >= 0", name="ck_wfm_history_nonnegative"),
            sa.UniqueConstraint("tenant_id", "environment_id", "queue_key", "interval_start", name="uq_wfm_history_interval"),
        )
        op.create_index("ix_wfm_history_lookup", "wfm_interval_history", ["tenant_id", "queue_key", "interval_start"])
        op.create_index("ix_wfm_interval_history_environment_id", "wfm_interval_history", ["environment_id"])
        op.create_index("ix_wfm_interval_history_tenant_id", "wfm_interval_history", ["tenant_id"])

    if not _has_table(bind, "wfm_forecasts"):
        op.create_table(
            "wfm_forecasts",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("queue_key", sa.String(length=36), nullable=False),
            sa.Column("timezone", sa.String(length=64), nullable=False),
            sa.Column("interval_minutes", sa.Integer(), nullable=False),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("horizon_days", sa.Integer(), nullable=False),
            sa.Column("history_days", sa.Integer(), nullable=False),
            sa.Column("method", sa.String(length=48), nullable=False),
            sa.Column("model_version", sa.String(length=32), nullable=False),
            sa.Column("points", sa.JSON(), nullable=False),
            sa.Column("overrides", sa.JSON(), nullable=False),
            sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("environment_id", sa.Uuid(), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("horizon_days > 0", name="ck_wfm_forecast_horizon"),
        )
        op.create_index("ix_wfm_forecast_lookup", "wfm_forecasts", ["tenant_id", "queue_key", "generated_at"])
        op.create_index("ix_wfm_forecasts_environment_id", "wfm_forecasts", ["environment_id"])
        op.create_index("ix_wfm_forecasts_tenant_id", "wfm_forecasts", ["tenant_id"])

    if not _has_table(bind, "wfm_forecast_accuracy"):
        op.create_table(
            "wfm_forecast_accuracy",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("queue_key", sa.String(length=36), nullable=False),
            sa.Column("forecast_id", sa.Uuid(), sa.ForeignKey("wfm_forecasts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("evaluated_from", sa.DateTime(timezone=True), nullable=False),
            sa.Column("evaluated_to", sa.DateTime(timezone=True), nullable=False),
            sa.Column("wape", sa.Numeric(precision=8, scale=4), nullable=False),
            sa.Column("mape", sa.Numeric(precision=8, scale=4), nullable=False),
            sa.Column("bias", sa.Numeric(precision=10, scale=4), nullable=False),
            sa.Column("mae", sa.Numeric(precision=12, scale=4), nullable=False),
            sa.Column("sample_count", sa.Integer(), nullable=False),
            sa.Column("per_interval", sa.JSON(), nullable=False),
            sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("environment_id", sa.Uuid(), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.UniqueConstraint("forecast_id", "evaluated_from", name="uq_wfm_accuracy_window"),
        )
        op.create_index("ix_wfm_accuracy_lookup", "wfm_forecast_accuracy", ["tenant_id", "queue_key", "computed_at"])
        op.create_index("ix_wfm_forecast_accuracy_environment_id", "wfm_forecast_accuracy", ["environment_id"])
        op.create_index("ix_wfm_forecast_accuracy_forecast_id", "wfm_forecast_accuracy", ["forecast_id"])
        op.create_index("ix_wfm_forecast_accuracy_tenant_id", "wfm_forecast_accuracy", ["tenant_id"])

    if not _has_table(bind, "wfm_shift_templates"):
        op.create_table(
            "wfm_shift_templates",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column("start_minute", sa.Integer(), nullable=False),
            sa.Column("length_minutes", sa.Integer(), nullable=False),
            sa.Column("paid_break_minutes", sa.Integer(), nullable=False),
            sa.Column("unpaid_break_minutes", sa.Integer(), nullable=False),
            sa.Column("break_rules", sa.JSON(), nullable=False),
            sa.Column("required_skill_key", sa.String(length=80), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("paid_break_minutes >= 0 AND unpaid_break_minutes >= 0", name="ck_wfm_template_breaks"),
            sa.CheckConstraint("length_minutes BETWEEN 60 AND 960", name="ck_wfm_template_length"),
            sa.CheckConstraint("start_minute >= 0 AND start_minute < 1440", name="ck_wfm_template_start"),
            sa.UniqueConstraint("tenant_id", "name", name="uq_wfm_shift_template_name"),
        )
        op.create_index("ix_wfm_shift_templates_tenant_id", "wfm_shift_templates", ["tenant_id"])

    if not _has_table(bind, "wfm_labor_rules"):
        op.create_table(
            "wfm_labor_rules",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column("max_hours_per_week", sa.Numeric(precision=5, scale=2), nullable=False),
            sa.Column("min_rest_hours", sa.Numeric(precision=5, scale=2), nullable=False),
            sa.Column("max_consecutive_days", sa.Integer(), nullable=False),
            sa.Column("max_shift_hours", sa.Numeric(precision=5, scale=2), nullable=False),
            sa.Column("min_split_gap_minutes", sa.Integer(), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("max_consecutive_days BETWEEN 1 AND 14", name="ck_wfm_rule_consecutive"),
            sa.CheckConstraint("min_rest_hours >= 0 AND min_rest_hours <= 48", name="ck_wfm_rule_rest"),
            sa.CheckConstraint("max_shift_hours > 0 AND max_shift_hours <= 16", name="ck_wfm_rule_shift"),
            sa.CheckConstraint("max_hours_per_week > 0 AND max_hours_per_week <= 168", name="ck_wfm_rule_week"),
            sa.UniqueConstraint("tenant_id", "name", name="uq_wfm_labor_rule_name"),
        )
        op.create_index("ix_wfm_labor_rules_tenant_id", "wfm_labor_rules", ["tenant_id"])

    if not _has_table(bind, "wfm_schedules"):
        op.create_table(
            "wfm_schedules",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("week_start", sa.Date(), nullable=False),
            sa.Column("timezone", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("current_version_id", sa.Uuid()),
            sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("environment_id", sa.Uuid(), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("status IN ('draft','published','archived')", name="ck_wfm_schedule_status"),
            sa.UniqueConstraint("tenant_id", "environment_id", "week_start", "name", name="uq_wfm_schedule_week"),
        )
        op.create_index("ix_wfm_schedules_environment_id", "wfm_schedules", ["environment_id"])
        op.create_index("ix_wfm_schedules_tenant_id", "wfm_schedules", ["tenant_id"])

    if not _has_table(bind, "wfm_schedule_versions"):
        op.create_table(
            "wfm_schedule_versions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("schedule_id", sa.Uuid(), sa.ForeignKey("wfm_schedules.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("assignments", sa.JSON(), nullable=False),
            sa.Column("coverage", sa.JSON(), nullable=False),
            sa.Column("solver_report", sa.JSON(), nullable=False),
            sa.Column("note", sa.String(length=400), nullable=False),
            sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True)),
            sa.Column("published_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("environment_id", sa.Uuid(), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("version_number > 0", name="ck_wfm_version_number"),
            sa.CheckConstraint("status IN ('draft','published','superseded')", name="ck_wfm_version_status"),
            sa.UniqueConstraint("schedule_id", "version_number", name="uq_wfm_schedule_version"),
        )
        op.create_index("ix_wfm_schedule_versions_environment_id", "wfm_schedule_versions", ["environment_id"])
        op.create_index("ix_wfm_schedule_versions_schedule_id", "wfm_schedule_versions", ["schedule_id"])
        op.create_index("ix_wfm_schedule_versions_tenant_id", "wfm_schedule_versions", ["tenant_id"])

    if not _has_table(bind, "wfm_shifts"):
        op.create_table(
            "wfm_shifts",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("schedule_version_id", sa.Uuid(), sa.ForeignKey("wfm_schedule_versions.id", ondelete="CASCADE")),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE")),
            sa.Column("template_id", sa.Uuid(), sa.ForeignKey("wfm_shift_templates.id", ondelete="SET NULL")),
            sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("break_minutes", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("skill_key", sa.String(length=80), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("environment_id", sa.Uuid(), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("status IN ('scheduled','published','swapped','dropped','open')", name="ck_wfm_shift_status"),
        )
        op.create_index("ix_wfm_shift_person", "wfm_shifts", ["tenant_id", "user_id", "starts_at"])
        op.create_index("ix_wfm_shift_window", "wfm_shifts", ["tenant_id", "environment_id", "starts_at"])
        op.create_index("ix_wfm_shifts_environment_id", "wfm_shifts", ["environment_id"])
        op.create_index("ix_wfm_shifts_schedule_version_id", "wfm_shifts", ["schedule_version_id"])
        op.create_index("ix_wfm_shifts_tenant_id", "wfm_shifts", ["tenant_id"])
        op.create_index("ix_wfm_shifts_user_id", "wfm_shifts", ["user_id"])

    if not _has_table(bind, "wfm_time_off_requests"):
        op.create_table(
            "wfm_time_off_requests",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("hours", sa.Numeric(precision=7, scale=2), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("reason", sa.String(length=400), nullable=False),
            sa.Column("coverage", sa.JSON(), nullable=False),
            sa.Column("decided_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("decided_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("kind IN ('vacation','sick','unpaid','training','bereavement')", name="ck_wfm_timeoff_kind"),
            sa.CheckConstraint("status IN ('requested','approved','denied','cancelled')", name="ck_wfm_timeoff_status"),
            sa.CheckConstraint("ends_at > starts_at", name="ck_wfm_timeoff_window"),
        )
        op.create_index("ix_wfm_time_off_requests_tenant_id", "wfm_time_off_requests", ["tenant_id"])
        op.create_index("ix_wfm_timeoff_person", "wfm_time_off_requests", ["tenant_id", "user_id", "starts_at"])

    if not _has_table(bind, "wfm_time_off_balances"):
        op.create_table(
            "wfm_time_off_balances",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("balance_hours", sa.Numeric(precision=8, scale=2), nullable=False),
            sa.Column("accrued_hours", sa.Numeric(precision=8, scale=2), nullable=False),
            sa.Column("used_hours", sa.Numeric(precision=8, scale=2), nullable=False),
            sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint(
                "used_hours >= 0 AND balance_hours >= -1000", name="ck_wfm_balance_nonnegative"
            ),
            sa.UniqueConstraint("tenant_id", "user_id", "kind", name="uq_wfm_balance_person_kind"),
        )
        op.create_index("ix_wfm_time_off_balances_tenant_id", "wfm_time_off_balances", ["tenant_id"])

    if not _has_table(bind, "wfm_shift_swaps"):
        op.create_table(
            "wfm_shift_swaps",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("shift_id", sa.Uuid(), sa.ForeignKey("wfm_shifts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("offering_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("target_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("rule_check", sa.JSON(), nullable=False),
            sa.Column("note", sa.String(length=400), nullable=False),
            sa.Column("decided_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("decided_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("environment_id", sa.Uuid(), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("status IN ('offered','accepted','approved','rejected','cancelled')", name="ck_wfm_swap_status"),
        )
        op.create_index("ix_wfm_shift_swaps_environment_id", "wfm_shift_swaps", ["environment_id"])
        op.create_index("ix_wfm_shift_swaps_shift_id", "wfm_shift_swaps", ["shift_id"])
        op.create_index("ix_wfm_shift_swaps_tenant_id", "wfm_shift_swaps", ["tenant_id"])
        op.create_index("ix_wfm_swap_state", "wfm_shift_swaps", ["tenant_id", "status", "created_at"])

    if not _has_table(bind, "wfm_open_shift_bids"):
        op.create_table(
            "wfm_open_shift_bids",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("shift_id", sa.Uuid(), sa.ForeignKey("wfm_shifts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("score", sa.Numeric(precision=10, scale=4), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("environment_id", sa.Uuid(), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("status IN ('placed','awarded','rejected','withdrawn')", name="ck_wfm_bid_status"),
            sa.UniqueConstraint("shift_id", "user_id", name="uq_wfm_bid_person_shift"),
        )
        op.create_index("ix_wfm_open_shift_bids_environment_id", "wfm_open_shift_bids", ["environment_id"])
        op.create_index("ix_wfm_open_shift_bids_shift_id", "wfm_open_shift_bids", ["shift_id"])
        op.create_index("ix_wfm_open_shift_bids_tenant_id", "wfm_open_shift_bids", ["tenant_id"])

    if not _has_table(bind, "wfm_adherence_exceptions"):
        op.create_table(
            "wfm_adherence_exceptions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("shift_id", sa.Uuid(), sa.ForeignKey("wfm_shifts.id", ondelete="SET NULL")),
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("kind", sa.String(length=24), nullable=False),
            sa.Column("minutes", sa.Integer(), nullable=False),
            sa.Column("detail", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("decided_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("decided_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("environment_id", sa.Uuid(), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("kind IN ('late_start','early_leave','unapproved_break','over_break','no_show','off_queue')", name="ck_wfm_adherence_kind"),
            sa.CheckConstraint("status IN ('open','approved','denied')", name="ck_wfm_adherence_status"),
        )
        op.create_index("ix_wfm_adherence_exceptions_environment_id", "wfm_adherence_exceptions", ["environment_id"])
        op.create_index("ix_wfm_adherence_exceptions_tenant_id", "wfm_adherence_exceptions", ["tenant_id"])
        op.create_index("ix_wfm_adherence_person_day", "wfm_adherence_exceptions", ["tenant_id", "user_id", "day"])

    if not _has_table(bind, "wl_custom_domains"):
        op.create_table(
            "wl_custom_domains",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("environment_id", sa.Uuid(), sa.ForeignKey("environments.id", ondelete="SET NULL")),
            sa.Column("domain", sa.String(length=253), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("ownership_token_hash", sa.String(length=64), nullable=False),
            sa.Column("ownership_record_name", sa.String(length=300), nullable=False),
            sa.Column("cname_target", sa.String(length=253), nullable=False),
            sa.Column("last_checked_at", sa.DateTime(timezone=True)),
            sa.Column("verified_at", sa.DateTime(timezone=True)),
            sa.Column("activated_at", sa.DateTime(timezone=True)),
            sa.Column("reverify_due_at", sa.DateTime(timezone=True)),
            sa.Column("failure_reason", sa.String(length=300), nullable=False),
            sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("status IN ('pending_dns','verifying','issuing_cert','active','failed','disabled')", name="ck_wl_domain_status"),
            sa.UniqueConstraint("domain", name="uq_wl_custom_domains_domain"),
        )
        op.create_index("ix_wl_custom_domains_tenant_id", "wl_custom_domains", ["tenant_id"])
        op.create_index("ix_wl_domain_reverify", "wl_custom_domains", ["status", "reverify_due_at"])
        op.create_index("ix_wl_domain_tenant_status", "wl_custom_domains", ["tenant_id", "status"])

    if not _has_table(bind, "wl_domain_checks"):
        op.create_table(
            "wl_domain_checks",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("domain_id", sa.Uuid(), sa.ForeignKey("wl_custom_domains.id", ondelete="CASCADE"), nullable=False),
            sa.Column("check_type", sa.String(length=16), nullable=False),
            sa.Column("passed", sa.Boolean(), nullable=False),
            sa.Column("consistent", sa.Boolean(), nullable=False),
            sa.Column("resolvers", sa.JSON(), nullable=False),
            sa.Column("detail", sa.JSON(), nullable=False),
            sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint(
                "check_type IN ('ownership_txt','cname','address','reverify')",
                name="ck_wl_check_type",
            ),
        )
        op.create_index("ix_wl_domain_check_domain", "wl_domain_checks", ["domain_id", "checked_at"])
        op.create_index("ix_wl_domain_checks_tenant_id", "wl_domain_checks", ["tenant_id"])

    if not _has_table(bind, "wl_tls_cert_status"):
        op.create_table(
            "wl_tls_cert_status",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("domain_id", sa.Uuid(), sa.ForeignKey("wl_custom_domains.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("issuer", sa.String(length=120), nullable=False),
            sa.Column("serial", sa.String(length=120), nullable=False),
            sa.Column("not_before", sa.DateTime(timezone=True)),
            sa.Column("not_after", sa.DateTime(timezone=True)),
            sa.Column("last_error", sa.String(length=300), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint(
                "status IN ('pending','valid','expired','error')",
                name="ck_wl_cert_status",
            ),
            sa.UniqueConstraint("domain_id", name="uq_wl_cert_domain"),
        )
        op.create_index("ix_wl_tls_cert_status_tenant_id", "wl_tls_cert_status", ["tenant_id"])

    if not _has_table(bind, "wl_brand_themes"):
        op.create_table(
            "wl_brand_themes",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("name", sa.String(length=80), nullable=False),
            sa.Column("active_key", sa.String(length=8)),
            sa.Column("current_version_id", sa.Uuid()),
            sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("active_key IS NULL OR active_key = 'active'", name="ck_wl_theme_active_key"),
            sa.UniqueConstraint("tenant_id", "name", name="uq_wl_theme_name"),
            sa.UniqueConstraint("tenant_id", "active_key", name="uq_wl_theme_one_active"),
        )
        op.create_index("ix_wl_brand_themes_tenant_id", "wl_brand_themes", ["tenant_id"])

    if not _has_table(bind, "wl_brand_theme_versions"):
        op.create_table(
            "wl_brand_theme_versions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("theme_id", sa.Uuid(), sa.ForeignKey("wl_brand_themes.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("contrast", sa.JSON(), nullable=False),
            sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("published_at", sa.DateTime(timezone=True)),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("version_number > 0", name="ck_wl_theme_version_number"),
            sa.UniqueConstraint("theme_id", "version_number", name="uq_wl_theme_version"),
        )
        op.create_index("ix_wl_brand_theme_versions_tenant_id", "wl_brand_theme_versions", ["tenant_id"])
        op.create_index("ix_wl_brand_theme_versions_theme_id", "wl_brand_theme_versions", ["theme_id"])

    if not _has_table(bind, "wl_brand_assets"):
        op.create_table(
            "wl_brand_assets",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("filename", sa.String(length=200), nullable=False),
            sa.Column("content_type", sa.String(length=80), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("storage_path", sa.String(length=400), nullable=False),
            sa.Column("sanitized", sa.Boolean(), nullable=False),
            sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("kind IN ('logo','favicon','background','font')", name="ck_wl_asset_kind"),
            sa.CheckConstraint("size_bytes > 0", name="ck_wl_asset_size"),
            sa.UniqueConstraint("tenant_id", "sha256", name="uq_wl_asset_content"),
        )
        op.create_index("ix_wl_brand_assets_tenant_id", "wl_brand_assets", ["tenant_id"])

    if not _has_table(bind, "wl_email_sender_domains"):
        op.create_table(
            "wl_email_sender_domains",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("domain", sa.String(length=253), nullable=False),
            sa.Column("from_name", sa.String(length=120), nullable=False),
            sa.Column("dkim_selector", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("spf_verified", sa.Boolean(), nullable=False),
            sa.Column("dkim_verified", sa.Boolean(), nullable=False),
            sa.Column("dmarc_verified", sa.Boolean(), nullable=False),
            sa.Column("records", sa.JSON(), nullable=False),
            sa.Column("last_checked_at", sa.DateTime(timezone=True)),
            sa.Column("verified_at", sa.DateTime(timezone=True)),
            sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("status IN ('pending','verified','failed','disabled')", name="ck_wl_sender_status"),
            sa.UniqueConstraint("domain", name="uq_wl_sender_domain"),
        )
        op.create_index("ix_wl_email_sender_domains_tenant_id", "wl_email_sender_domains", ["tenant_id"])

    if not _has_table(bind, "wl_dns_record_checks"):
        op.create_table(
            "wl_dns_record_checks",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("sender_domain_id", sa.Uuid(), sa.ForeignKey("wl_email_sender_domains.id", ondelete="CASCADE")),
            sa.Column("domain_id", sa.Uuid(), sa.ForeignKey("wl_custom_domains.id", ondelete="CASCADE")),
            sa.Column("record_type", sa.String(length=8), nullable=False),
            sa.Column("name", sa.String(length=300), nullable=False),
            sa.Column("expected", sa.String(length=600), nullable=False),
            sa.Column("observed", sa.JSON(), nullable=False),
            sa.Column("passed", sa.Boolean(), nullable=False),
            sa.Column("detail", sa.JSON(), nullable=False),
            sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("record_type IN ('A','AAAA','CNAME','TXT','MX','SPF','DKIM','DMARC')", name="ck_wl_record_type"),
        )
        op.create_index("ix_wl_dns_record_checks_tenant_id", "wl_dns_record_checks", ["tenant_id"])
        op.create_index("ix_wl_record_check_lookup", "wl_dns_record_checks", ["tenant_id", "name", "checked_at"])

    if not _has_table(bind, "mob_devices"):
        op.create_table(
            "mob_devices",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("platform", sa.String(length=8), nullable=False),
            sa.Column("device_id", sa.String(length=120), nullable=False),
            sa.Column("app_version", sa.String(length=40), nullable=False),
            sa.Column("os_version", sa.String(length=40), nullable=False),
            sa.Column("push_enabled", sa.Boolean(), nullable=False),
            sa.Column("quiet_hours", sa.JSON(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("platform IN ('ios','android')", name="ck_mob_device_platform"),
            sa.UniqueConstraint("tenant_id", "user_id", "platform", "device_id", name="uq_mob_device_identity"),
        )
        op.create_index("ix_mob_device_user", "mob_devices", ["tenant_id", "user_id", "revoked_at"])
        op.create_index("ix_mob_devices_tenant_id", "mob_devices", ["tenant_id"])
        op.create_index("ix_mob_devices_user_id", "mob_devices", ["user_id"])

    if not _has_table(bind, "mob_push_tokens"):
        op.create_table(
            "mob_push_tokens",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("device_id", sa.Uuid(), sa.ForeignKey("mob_devices.id", ondelete="CASCADE"), nullable=False),
            sa.Column("platform", sa.String(length=8), nullable=False),
            sa.Column("token", sa.String(length=512), nullable=False),
            sa.Column("token_hint", sa.String(length=12), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("failure_count", sa.Integer(), nullable=False),
            sa.Column("last_error", sa.String(length=200), nullable=False),
            sa.Column("invalidated_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("platform IN ('ios','android')", name="ck_mob_token_platform"),
            sa.UniqueConstraint("platform", "token", name="uq_mob_push_token"),
        )
        op.create_index("ix_mob_push_tokens_tenant_id", "mob_push_tokens", ["tenant_id"])
        op.create_index("ix_mob_token_device", "mob_push_tokens", ["device_id", "active"])

    if not _has_table(bind, "mob_push_deliveries"):
        op.create_table(
            "mob_push_deliveries",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("device_id", sa.Uuid(), sa.ForeignKey("mob_devices.id", ondelete="CASCADE"), nullable=False),
            sa.Column("template", sa.String(length=48), nullable=False),
            sa.Column("provider", sa.String(length=16), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("provider_message_id", sa.String(length=200), nullable=False),
            sa.Column("error_code", sa.String(length=80), nullable=False),
            sa.Column("error_reason", sa.String(length=200), nullable=False),
            sa.Column("dedupe_key", sa.String(length=120)),
            sa.Column("collapse_id", sa.String(length=64), nullable=False),
            sa.Column("payload_summary", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("sent_at", sa.DateTime(timezone=True)),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("status IN ('queued','sent','failed','dropped')", name="ck_mob_delivery_status"),
            sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_mob_delivery_dedupe"),
        )
        op.create_index("ix_mob_delivery_device", "mob_push_deliveries", ["device_id", "created_at"])
        op.create_index("ix_mob_push_deliveries_tenant_id", "mob_push_deliveries", ["tenant_id"])

    if not _has_table(bind, "mob_auth_codes"):
        op.create_table(
            "mob_auth_codes",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("code_hash", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("device_id", sa.String(length=120), nullable=False),
            sa.Column("platform", sa.String(length=8), nullable=False),
            sa.Column("code_challenge", sa.String(length=128), nullable=False),
            sa.Column("code_challenge_method", sa.String(length=8), nullable=False),
            sa.Column("redirect_uri", sa.String(length=300), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("consumed_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.CheckConstraint("code_challenge_method IN ('S256')", name="ck_mob_pkce_method"),
            sa.UniqueConstraint("code_hash", name="uq_mob_auth_code"),
        )
        op.create_index("ix_mob_auth_codes_tenant_id", "mob_auth_codes", ["tenant_id"])

    if not _has_table(bind, "mob_refresh_tokens"):
        op.create_table(
            "mob_refresh_tokens",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("family_id", sa.Uuid(), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("device_id", sa.String(length=120), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True)),
            sa.Column("revoked_reason", sa.String(length=64), nullable=False),
            sa.Column("replaced_by_id", sa.Uuid()),
            sa.Column("reuse_detected_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
            sa.UniqueConstraint("token_hash", name="uq_mob_refresh_token"),
        )
        op.create_index("ix_mob_refresh_family", "mob_refresh_tokens", ["tenant_id", "family_id", "revoked_at"])
        op.create_index("ix_mob_refresh_tokens_family_id", "mob_refresh_tokens", ["family_id"])
        op.create_index("ix_mob_refresh_tokens_tenant_id", "mob_refresh_tokens", ["tenant_id"])


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in (
        "mob_refresh_tokens",
        "mob_auth_codes",
        "mob_push_deliveries",
        "mob_push_tokens",
        "mob_devices",
        "wl_dns_record_checks",
        "wl_email_sender_domains",
        "wl_brand_assets",
        "wl_brand_theme_versions",
        "wl_brand_themes",
        "wl_tls_cert_status",
        "wl_domain_checks",
        "wl_custom_domains",
        "wfm_adherence_exceptions",
        "wfm_open_shift_bids",
        "wfm_shift_swaps",
        "wfm_time_off_balances",
        "wfm_time_off_requests",
        "wfm_shifts",
        "wfm_schedule_versions",
        "wfm_schedules",
        "wfm_labor_rules",
        "wfm_shift_templates",
        "wfm_forecast_accuracy",
        "wfm_forecasts",
        "wfm_interval_history",
    ):
        if _has_table(bind, table_name):
            op.drop_table(table_name)
