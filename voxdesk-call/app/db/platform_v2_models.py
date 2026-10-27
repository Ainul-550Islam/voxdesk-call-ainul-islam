"""Batch 07 persistence: workforce management, white-label, mobile.

Everything is tenant scoped and additive. Three rules shaped every column:

* **Nothing here is a number the product invented.** A forecast row stores the
  points the model produced *and* the history window it saw; an accuracy row
  stores the errors computed from those points against stored actuals. There is
  no "target accuracy" column to fall back on.
* **A schedule is a document, not a mutable table.** ``WfmScheduleVersion`` rows
  are immutable once published; changes create a new version, so "what did we
  send this person on Tuesday" has one answer forever.
* **A custom domain is owned by exactly one tenant.** ``wl_custom_domains`` has
  a global unique constraint on the hostname, and the token that proves
  ownership is stored as a digest — the plaintext is shown once, at creation.

``environment_id`` is nullable on the platform tables (branding, custom domains,
mobile devices) because those are tenant-level products: a tenant has one brand
and one set of customer domains regardless of how many environments it runs.
WFM rows carry it where the data is operational (history, schedules, adherence)
and omit it where it is not (time-off balances are per person, not per
environment).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TenantScoped:
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )


class EnvironmentScoped(TenantScoped):
    """Tenant + environment. Used where the row is operational data."""

    environment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False, index=True
    )


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# =========================================================== WFM: history ===
class WfmIntervalHistory(EnvironmentScoped, Base):
    """One completed measurement interval, rebuilt from real telephony rows.

    A gap in history is *not* filled with an interpolated zero: ``handled`` and
    ``offered`` stay at what the source data actually said and ``is_gap`` marks
    the interval, because a silent zero teaches the forecast that Mondays at
    09:00 are quiet.
    """

    __tablename__ = "wfm_interval_history"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "environment_id", "queue_key", "interval_start",
            name="uq_wfm_history_interval",
        ),
        CheckConstraint("interval_minutes IN (15,30)", name="ck_wfm_history_interval_minutes"),
        CheckConstraint("offered >= 0 AND handled >= 0 AND abandoned >= 0", name="ck_wfm_history_nonnegative"),
        Index("ix_wfm_history_lookup", "tenant_id", "queue_key", "interval_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    #: ``str(queue_id)`` or ``""`` for the environment-wide queue. A string rather
    #: than a nullable FK so the unique constraint actually holds: PostgreSQL and
    #: SQLite both treat NULLs as distinct in a unique index.
    queue_key: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    interval_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    #: The IANA zone the bucket grid was built in. Stored, not inferred: a tenant
    #: that changes WFM_TIMEZONE must not have last month's buckets silently
    #: reinterpreted in a different local frame.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    offered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    handled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    abandoned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Seconds. Numeric, not float: these are summed and averaged in reports and
    #: a binary float drifts across a month of intervals.
    total_handle_seconds: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    total_speed_answer_seconds: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, default=0)
    #: Headcount planned by the rota for this interval.
    agents_scheduled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Agent-*minutes* in the available state during the interval, not a headcount:
    #: two agents available for half an interval is the same 15 as one agent for all
    #: of it, which is what occupancy and shrinkage need.
    agents_available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_gap: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="rollup")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class WfmForecast(EnvironmentScoped, Base):
    """One forecast run: the points, the method, and the history it saw."""

    __tablename__ = "wfm_forecasts"
    __table_args__ = (
        Index("ix_wfm_forecast_lookup", "tenant_id", "queue_key", "generated_at"),
        CheckConstraint("horizon_days > 0", name="ck_wfm_forecast_horizon"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_key: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    #: The IANA zone the interval grid was built in. Stored, not inferred: a
    #: schedule exported to a payroll system must carry the zone it means, and a
    #: tenant that changes its zone later must not silently shift published rota.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    history_days: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str] = mapped_column(String(48), nullable=False, default="seasonal_interval")
    model_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v1")
    #: ``[{"start": iso, "volume": float, "aht_seconds": float, "low": float,
    #: "high": float, "required_agents": int}]`` — every value computed, none defaulted.
    points: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: Holiday overrides applied to this run: ``[{"date": "...", "factor": 1.4,
    #: "label": "..."}]``, echoed so a surprising forecast is explainable.
    overrides: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class WfmForecastAccuracy(EnvironmentScoped, Base):
    """WAPE/MAPE/bias for one forecast, computed from stored actuals.

    There is deliberately no ``target`` column. A target is a business goal, not
    a measurement, and mixing the two is how a dashboard ends up showing 96% when
    nothing was measured.
    """

    __tablename__ = "wfm_forecast_accuracy"
    __table_args__ = (
        UniqueConstraint("forecast_id", "evaluated_from", name="uq_wfm_accuracy_window"),
        Index("ix_wfm_accuracy_lookup", "tenant_id", "queue_key", "computed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_key: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    forecast_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wfm_forecasts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evaluated_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evaluated_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    wape: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)
    mape: Mapped[float] = mapped_column(Numeric(8, 4), nullable=False)
    bias: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False)
    mae: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    per_interval: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


# ========================================================= WFM: staffing ====
class ShiftTemplate(TenantScoped, Base):
    """A named shift shape: start minute, length, and its break structure."""

    __tablename__ = "wfm_shift_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_wfm_shift_template_name"),
        CheckConstraint("start_minute >= 0 AND start_minute < 1440", name="ck_wfm_template_start"),
        CheckConstraint("length_minutes BETWEEN 60 AND 960", name="ck_wfm_template_length"),
        CheckConstraint("paid_break_minutes >= 0 AND unpaid_break_minutes >= 0", name="ck_wfm_template_breaks"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    start_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    length_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    unpaid_break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    #: ``[{"after_minutes": 120, "minutes": 15, "paid": true}, ...]`` — kept as
    #: data so a labour rule from a works council can be expressed without a
    #: migration.
    break_rules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    required_skill_key: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class LaborRule(TenantScoped, Base):
    """Hours, rest and consecutive-day limits a schedule must satisfy.

    These are constraints, not hints: the solver treats a violation as
    infeasible and reports it. The one thing it will never do is relax one to
    produce a schedule that looks complete.
    """

    __tablename__ = "wfm_labor_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_wfm_labor_rule_name"),
        CheckConstraint("max_hours_per_week > 0 AND max_hours_per_week <= 168", name="ck_wfm_rule_week"),
        CheckConstraint("min_rest_hours >= 0 AND min_rest_hours <= 48", name="ck_wfm_rule_rest"),
        CheckConstraint("max_consecutive_days BETWEEN 1 AND 14", name="ck_wfm_rule_consecutive"),
        CheckConstraint("max_shift_hours > 0 AND max_shift_hours <= 16", name="ck_wfm_rule_shift"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    max_hours_per_week: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=40)
    min_rest_hours: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=11)
    max_consecutive_days: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    max_shift_hours: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, default=10)
    #: Minimum gap between the end of one shift and the start of the next on the
    #: same day, in minutes (a second split shift is legal only above this).
    min_split_gap_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


# ======================================================== WFM: schedules ====
class WfmSchedule(EnvironmentScoped, Base):
    """The container. Versions below are the immutable documents."""

    __tablename__ = "wfm_schedules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "environment_id", "week_start", "name", name="uq_wfm_schedule_week"),
        CheckConstraint("status IN ('draft','published','archived')", name="ck_wfm_schedule_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="Weekly schedule")
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    #: Zone the rota was built in; published shifts are absolute instants but the
    #: document that produced them is expressed in this zone.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class WfmScheduleVersion(EnvironmentScoped, Base):
    """An immutable snapshot of assignments, coverage and the solver's own report."""

    __tablename__ = "wfm_schedule_versions"
    __table_args__ = (
        UniqueConstraint("schedule_id", "version_number", name="uq_wfm_schedule_version"),
        CheckConstraint("status IN ('draft','published','superseded')", name="ck_wfm_version_status"),
        CheckConstraint("version_number > 0", name="ck_wfm_version_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schedule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wfm_schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    assignments: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    coverage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: ``{"status": "optimal"|"feasible"|"infeasible"|"unknown", "wall_time_s":
    #: 1.2, "truncated": bool, "infeasible": [...], "objective": ...}``
    solver_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    note: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class WfmShift(EnvironmentScoped, Base):
    """One person working one window. Materialised from a published version."""

    __tablename__ = "wfm_shifts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('scheduled','published','swapped','dropped','open')",
            name="ck_wfm_shift_status",
        ),
        Index("ix_wfm_shift_person", "tenant_id", "user_id", "starts_at"),
        Index("ix_wfm_shift_window", "tenant_id", "environment_id", "starts_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    schedule_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wfm_schedule_versions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wfm_shift_templates.id", ondelete="SET NULL"), nullable=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="scheduled")
    skill_key: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


# ============================================ WFM: time off, swaps, bids ====
class TimeOffRequest(TenantScoped, Base):
    """A request, its decision, and the coverage answer it was judged against."""

    __tablename__ = "wfm_time_off_requests"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('vacation','sick','unpaid','training','bereavement')",
            name="ck_wfm_timeoff_kind",
        ),
        CheckConstraint(
            "status IN ('requested','approved','denied','cancelled')",
            name="ck_wfm_timeoff_status",
        ),
        CheckConstraint("ends_at > starts_at", name="ck_wfm_timeoff_window"),
        Index("ix_wfm_timeoff_person", "tenant_id", "user_id", "starts_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="vacation")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    hours: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="requested")
    reason: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    #: ``{"required": 6, "scheduled": 7, "projected": 5, "ok": false}`` — the
    #: coverage check the approver actually made, kept so the decision is
    #: reviewable without re-running history.
    coverage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class TimeOffBalance(TenantScoped, Base):
    """Per person, per kind. Unique so accrual cannot fork into two rows."""

    __tablename__ = "wfm_time_off_balances"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "kind", name="uq_wfm_balance_person_kind"),
        # Used hours can never be negative; the balance can go negative when an
        # approver explicitly grants an exception (``decide(enforce_balance=False)``)
        # or when the kind is sick/bereavement. The floor here catches corruption,
        # while the overdraft *policy* lives in ``timeoff.decide`` where the reason
        # can be returned to the caller.
        CheckConstraint(
            "used_hours >= 0 AND balance_hours >= -1000", name="ck_wfm_balance_nonnegative"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="vacation")
    balance_hours: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False, default=0)
    accrued_hours: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False, default=0)
    used_hours: Mapped[float] = mapped_column(Numeric(8, 2), nullable=False, default=0)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class ShiftSwap(EnvironmentScoped, Base):
    """A swap offer between two people, validated against the labour rules."""

    __tablename__ = "wfm_shift_swaps"
    __table_args__ = (
        CheckConstraint(
            "status IN ('offered','accepted','approved','rejected','cancelled')",
            name="ck_wfm_swap_status",
        ),
        Index("ix_wfm_swap_state", "tenant_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shift_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wfm_shifts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    offering_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="offered")
    #: Result of the labour-rule check at the time of the offer, so an approval
    #: cannot be justified later by a rule that has since been edited.
    rule_check: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    note: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class OpenShiftBid(EnvironmentScoped, Base):
    """A bid for an open shift. One bid per person per shift."""

    __tablename__ = "wfm_open_shift_bids"
    __table_args__ = (
        UniqueConstraint("shift_id", "user_id", name="uq_wfm_bid_person_shift"),
        CheckConstraint("status IN ('placed','awarded','rejected','withdrawn')", name="ck_wfm_bid_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    shift_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wfm_shifts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="placed")
    #: Ordered by the tenant's bid policy (seniority, then fewest hours offered).
    score: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class AdherenceException(EnvironmentScoped, Base):
    """A deviation from the published schedule, and its adjudication."""

    __tablename__ = "wfm_adherence_exceptions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('late_start','early_leave','unapproved_break','over_break','no_show','off_queue')",
            name="ck_wfm_adherence_kind",
        ),
        CheckConstraint("status IN ('open','approved','denied')", name="ck_wfm_adherence_status"),
        Index("ix_wfm_adherence_person_day", "tenant_id", "user_id", "day"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    shift_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wfm_shifts.id", ondelete="SET NULL"), nullable=True
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Structured explanation (``{"text": ...}`` plus anything the detector adds). It is
    #: JSON because ``decide_exception`` appends the adjudication note to the same field.
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


# ================================================== white-label domains ====
class CustomDomain(TenantScoped, Base):
    """A customer-owned hostname served by this deployment.

    The lifecycle is explicit — ``pending_dns -> verifying -> issuing_cert ->
    active``, with ``failed`` reachable from the middle two and ``active`` only
    from ``issuing_cert``. Nothing serves a tenant's brand from a hostname until
    it is ``active``, and the TLS ``ask`` endpoint accepts only ``active``.

    Ownership is proven twice on purpose: a CNAME proves *routing* (traffic
    reaches us) and a TXT token proves *ownership* (the person who controls the
    zone asked for this). A CNAME alone is transferable by whoever controls DNS
    later, so the token is what stops a resold domain from silently becoming
    another tenant's brand.
    """

    __tablename__ = "wl_custom_domains"
    __table_args__ = (
        UniqueConstraint("domain", name="uq_wl_custom_domains_domain"),
        CheckConstraint(
            "status IN ('pending_dns','verifying','issuing_cert','active','failed','disabled')",
            name="ck_wl_domain_status",
        ),
        Index("ix_wl_domain_tenant_status", "tenant_id", "status"),
        Index("ix_wl_domain_reverify", "status", "reverify_due_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id", ondelete="SET NULL"), nullable=True
    )
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending_dns")
    #: SHA-256 of the ownership token. The plaintext is shown once at creation.
    ownership_token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ownership_record_name: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    cname_target: Mapped[str] = mapped_column(String(253), nullable=False, default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reverify_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class WlDomainCheck(TenantScoped, Base):
    """One DNS check run against one custom domain, with every resolver's answer.

    Disagreement between resolvers is stored, not averaged: ``consistent=false``
    is a finding an operator has to see, because it is how a partially propagated
    or poisoned zone looks from here.
    """

    __tablename__ = "wl_domain_checks"
    __table_args__ = (
        Index("ix_wl_domain_check_domain", "domain_id", "checked_at"),
        CheckConstraint(
            "check_type IN ('ownership_txt','cname','address','reverify')",
            name="ck_wl_check_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wl_custom_domains.id", ondelete="CASCADE"), nullable=False
    )
    check_type: Mapped[str] = mapped_column(String(16), nullable=False, default="cname")
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consistent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Structured record of the attempt: which resolvers were asked, what each was
    #: expected to say and what it actually said. Kept as JSON so a disagreement is
    #: stored rather than averaged into one string.
    resolvers: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: One human sentence for the console; ``resolvers`` holds the detail.
    detail: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class TlsCertStatus(TenantScoped, Base):
    """The certificate this deployment holds for one custom domain."""

    __tablename__ = "wl_tls_cert_status"
    __table_args__ = (
        UniqueConstraint("domain_id", name="uq_wl_cert_domain"),
        CheckConstraint(
            # The vocabulary `platform_v2.whitelabel.tls` writes: a certificate is
            # either being served and valid, not yet valid, expired, or unreadable.
            "status IN ('pending','valid','expired','error')",
            name="ck_wl_cert_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wl_custom_domains.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    issuer: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    serial: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    not_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


# ========================================================== white-label ====
class BrandTheme(TenantScoped, Base):
    """One tenant's brand. Exactly one row may be active per tenant.

    ``active_key`` is ``"active"`` on the live row and NULL otherwise, and the
    unique constraint on ``(tenant_id, active_key)`` is what enforces "one active
    theme" — a partial index would do it too, but this form works on every
    database this product runs and in the SQLite test harness.
    """

    __tablename__ = "wl_brand_themes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_wl_theme_name"),
        UniqueConstraint("tenant_id", "active_key", name="uq_wl_theme_one_active"),
        CheckConstraint("active_key IS NULL OR active_key = 'active'", name="ck_wl_theme_active_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), nullable=False, default="default")
    active_key: Mapped[str | None] = mapped_column(String(8), nullable=True)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class BrandThemeVersion(TenantScoped, Base):
    """An immutable theme snapshot. Publishing moves the pointer, never the row."""

    __tablename__ = "wl_brand_theme_versions"
    __table_args__ = (
        UniqueConstraint("theme_id", "version_number", name="uq_wl_theme_version"),
        CheckConstraint("version_number > 0", name="ck_wl_theme_version_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    theme_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wl_brand_themes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    #: Contrast ratios measured at write time: ``{"primary_on_background": 7.2, ...}``
    contrast: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BrandAsset(TenantScoped, Base):
    """A stored branding file, addressed by content hash."""

    __tablename__ = "wl_brand_assets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sha256", name="uq_wl_asset_content"),
        CheckConstraint("kind IN ('logo','favicon','background','font')", name="ck_wl_asset_kind"),
        CheckConstraint("size_bytes > 0", name="ck_wl_asset_size"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="logo")
    filename: Mapped[str] = mapped_column(String(200), nullable=False)
    content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(400), nullable=False)
    #: Sanitised in place; the flag records that the file was rewritten, which is
    #: what an incident review needs to know.
    sanitized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class EmailSenderDomain(TenantScoped, Base):
    """A domain a tenant may send branded email from.

    Sending is blocked unless every required record verified. That is the whole
    point: mail sent from an unauthenticated domain lands in spam at best and
    gets the tenant's name blacklisted at worst.
    """

    __tablename__ = "wl_email_sender_domains"
    __table_args__ = (
        UniqueConstraint("domain", name="uq_wl_sender_domain"),
        CheckConstraint(
            "status IN ('pending','verified','failed','disabled')", name="ck_wl_sender_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, nullable=False, default=uuid.uuid4
    )
    domain: Mapped[str] = mapped_column(String(253), nullable=False)
    from_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    dkim_selector: Mapped[str] = mapped_column(String(64), nullable=False, default="voxdesk")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    spf_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dkim_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dmarc_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    records: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)

    @property
    def may_send(self) -> bool:
        return self.status == "verified" and self.spf_verified and self.dkim_verified


class DnsRecordCheck(TenantScoped, Base):
    """A stored observation of one DNS record (sender domains, TXT/MX probing)."""

    __tablename__ = "wl_dns_record_checks"
    __table_args__ = (
        CheckConstraint("record_type IN ('A','AAAA','CNAME','TXT','MX','SPF','DKIM','DMARC')",
                        name="ck_wl_record_type"),
        Index("ix_wl_record_check_lookup", "tenant_id", "name", "checked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sender_domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wl_email_sender_domains.id", ondelete="CASCADE"), nullable=True
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("wl_custom_domains.id", ondelete="CASCADE"), nullable=True
    )
    record_type: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    expected: Mapped[str] = mapped_column(String(600), nullable=False, default="")
    observed: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Per-record verdict from the SPF/DKIM/DMARC parser: present, valid, reason.
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


# ================================================================= mobile ===
class MobileDevice(TenantScoped, Base):
    """A phone that has signed in. One row per tenant/user/device."""

    __tablename__ = "mob_devices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "platform", "device_id", name="uq_mob_device_identity"),
        CheckConstraint("platform IN ('ios','android')", name="ck_mob_device_platform"),
        Index("ix_mob_device_user", "tenant_id", "user_id", "revoked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(8), nullable=False)
    device_id: Mapped[str] = mapped_column(String(120), nullable=False)
    app_version: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    os_version: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    push_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: ``{"start": "22:00", "end": "07:00", "timezone": "Europe/London"}``
    quiet_hours: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class PushToken(TenantScoped, Base):
    """A provider token for one device. Stored whole; never returned by an API."""

    __tablename__ = "mob_push_tokens"
    __table_args__ = (
        UniqueConstraint("platform", "token", name="uq_mob_push_token"),
        CheckConstraint("platform IN ('ios','android')", name="ck_mob_token_platform"),
        Index("ix_mob_token_device", "device_id", "active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mob_devices.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(8), nullable=False)
    token: Mapped[str] = mapped_column(String(512), nullable=False)
    #: Last six characters, for support ("is the token that ends 9f2a present?").
    token_hint: Mapped[str] = mapped_column(String(12), nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class PushDelivery(TenantScoped, Base):
    """One attempt to notify one device, including what the provider said."""

    __tablename__ = "mob_push_deliveries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_mob_delivery_dedupe"),
        CheckConstraint(
            "status IN ('queued','sent','failed','dropped')", name="ck_mob_delivery_status"
        ),
        Index("ix_mob_delivery_device", "device_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mob_devices.id", ondelete="CASCADE"), nullable=False
    )
    template: Mapped[str] = mapped_column(String(48), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False, default="fcm")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    provider_message_id: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    error_code: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    error_reason: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: Set by the caller ("shift_swap:42") so a retried pass cannot send twice.
    dedupe_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    collapse_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    payload_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MobileAuthCode(TenantScoped, Base):
    """A PKCE authorization code: stored as a digest, single use, short lived."""

    __tablename__ = "mob_auth_codes"
    __table_args__ = (
        UniqueConstraint("code_hash", name="uq_mob_auth_code"),
        CheckConstraint("code_challenge_method IN ('S256')", name="ck_mob_pkce_method"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    device_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    platform: Mapped[str] = mapped_column(String(8), nullable=False, default="ios")
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(String(8), nullable=False, default="S256")
    redirect_uri: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class MobileRefreshToken(TenantScoped, Base):
    """A rotating refresh token, grouped into a family for reuse detection.

    Presenting a token that was already rotated means the family is compromised
    (either the real client or the thief has a stale copy), so the whole family is
    revoked. That is why ``family_id`` exists as a first-class column instead of
    being inferred from ``replaced_by`` chains at read time.
    """

    __tablename__ = "mob_refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_mob_refresh_token"),
        Index("ix_mob_refresh_family", "tenant_id", "family_id", "revoked_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    #: The client's own device identifier (the same string the PKCE flow carries),
    #: not a ``mob_devices`` row: a session can exist before the app registers for push.
    device_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reuse_detected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


__all__ = [
    "AdherenceException",
    "BrandAsset",
    "BrandTheme",
    "BrandThemeVersion",
    "CustomDomain",
    "DnsRecordCheck",
    "EmailSenderDomain",
    "LaborRule",
    "MobileAuthCode",
    "MobileDevice",
    "MobileRefreshToken",
    "OpenShiftBid",
    "PushDelivery",
    "PushToken",
    "ShiftSwap",
    "ShiftTemplate",
    "TimeOffBalance",
    "TimeOffRequest",
    "TlsCertStatus",
    "WfmForecast",
    "WfmForecastAccuracy",
    "WfmIntervalHistory",
    "WfmSchedule",
    "WfmScheduleVersion",
    "WfmShift",
    "WlDomainCheck",
]
