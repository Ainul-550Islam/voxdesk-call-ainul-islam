"""Monitoring, safety and QA tables (PART 15).

Ten tables, grouped by the question each one answers after the fact:

* `call_live_scores` -- what the live scorer thought of a call, and *why*
  (component sub-scores stored separately, so a score of 41 can be explained);
* `auto_action_rules` / `auto_action_runs` -- which rule fired on which call, and
  whether the action actually ran; the unique key on (call, rule) is what stops a
  second transfer when the same rule fires twice;
* `alert_rules` / `alert_events` -- user-defined rules and every evaluation that
  breached them, including the ones suppressed by a cooldown (a suppressed alert
  that leaves no row looks like a monitoring gap);
* `batch_test_cases` / `batch_test_runs` / `batch_test_results` -- the case set,
  one row per bulk run, one row per case result;
* `guardrail_policies` / `guardrail_events` -- per-agent category toggles, the
  hard-stop phrase, and every block with the category that caused it;
* `qa_analyst_runs` -- one row per analyst run: the metrics it produced and the
  metering units it consumed.

`app/alerts/models.py` re-exports the two alert models so the spec-declared path
is real rather than a second definition.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CallLiveScore(Base):
    """One scoring sample for one call."""

    __tablename__ = "call_live_scores"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Component scores are stored, not derived on read: a tenant that changes
    #: its weights must not silently rewrite the history of past calls.
    components: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    weights: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    band: Mapped[str] = mapped_column(String(16), nullable=False, default="ok")
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_live_score_range"),
        CheckConstraint("band IN ('ok','watch','critical')", name="ck_live_score_band"),
        Index("ix_call_live_scores_call_created", "call_id", "created_at"),
    )


class AutoActionRule(Base):
    """A rule that turns a live signal into an action on the call."""

    __tablename__ = "auto_action_rules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: `score_below` | `sentiment_below` | `keyword` | `loop_detected` | `latency_above`
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    keywords: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    action_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    scope_agent_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Actions per call. Above one, the loop guard starts counting.
    max_per_call: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_auto_action_rule_name"),
        CheckConstraint(
            "trigger IN ('score_below','sentiment_below','keyword','loop_detected','latency_above')",
            name="ck_auto_action_trigger",
        ),
        CheckConstraint(
            "action IN ('transfer','webhook','alert','end_call')", name="ck_auto_action_action"
        ),
        CheckConstraint("max_per_call BETWEEN 1 AND 5", name="ck_auto_action_max"),
    )


class AutoActionRun(Base):
    """One attempted action for one call. The unique key is the loop guard."""

    __tablename__ = "auto_action_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("auto_action_rules.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    #: `sequence` distinguishes a legitimate second application (max_per_call > 1)
    #: from a duplicate; without it the unique key would forbid both.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "call_id", "rule_id", "sequence", name="uq_auto_action_run"
        ),
        CheckConstraint(
            "status IN ('pending','applied','skipped','failed')", name="ck_auto_action_run_status"
        ),
    )


class AlertRule(Base):
    """A user-defined metric rule (P15-04)."""

    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: `cost_burn_per_minute | success_rate | sentiment_delta | p95_latency |
    #: error_rate | concurrency_saturation | consent_breach | anomaly_zscore`
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    window_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    comparator: Mapped[str] = mapped_column(String(4), nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="tenant")
    scope_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    channels: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_alert_rule_name"),
        CheckConstraint("comparator IN ('gt','lt','gte','lte')", name="ck_alert_comparator"),
        CheckConstraint("scope_kind IN ('tenant','agent','number')", name="ck_alert_scope"),
        CheckConstraint("window_minutes BETWEEN 1 AND 1440", name="ck_alert_window"),
        CheckConstraint("cooldown_minutes BETWEEN 0 AND 1440", name="ck_alert_cooldown"),
    )


class AlertEvent(Base):
    """One evaluation that breached a rule (including suppressed ones)."""

    __tablename__ = "alert_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True
    )
    metric: Mapped[str] = mapped_column(String(40), nullable=False)
    observed_value: Mapped[float] = mapped_column(Float, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    #: `fired | suppressed_cooldown | suppressed_dedupe | delivery_failed`
    state: Mapped[str] = mapped_column(String(28), nullable=False, default="fired")
    #: Hash of (rule, scope, window bucket) so the same breach cannot notify twice.
    dedupe_key: Mapped[str] = mapped_column(String(120), nullable=False)
    deliveries: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_alert_event_dedupe"),
        Index("ix_alert_events_rule_created", "rule_id", "created_at"),
    )


class BatchTestCase(Base):
    """One test case for an agent."""

    __tablename__ = "batch_test_cases"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    #: The version the case was written against. A run pins it, so a case that
    #: starts failing after an edit is comparable with the run before the edit.
    agent_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    expected_contains: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    forbidden_contains: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    max_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    __table_args__ = (UniqueConstraint("tenant_id", "agent_id", "name", name="uq_batch_case_name"),)


class BatchTestRun(Base):
    """One bulk run over a case set."""

    __tablename__ = "batch_test_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','completed','failed','cancelled')",
            name="ck_batch_run_status",
        ),
    )


class BatchTestResult(Base):
    __tablename__ = "batch_test_results"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("batch_test_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    case_name: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reply: Mapped[str] = mapped_column(Text, nullable=False, default="")
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class GuardrailPolicy(Base):
    """Per-agent guardrail switches (P15-09)."""

    __tablename__ = "guardrail_policies"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: `*` means the tenant default; a specific agent id overrides it.
    agent_id: Mapped[str] = mapped_column(String(120), nullable=False, default="*")
    enabled_categories: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    block_jailbreak: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    block_prompt_extraction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    hard_stop_phrase: Mapped[str] = mapped_column(
        Text, nullable=False, default="I can't help with that, but I can connect you to a person."
    )
    judge_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", name="uq_guardrail_policy_agent"),
    )


class GuardrailEvent(Base):
    """Every block, with the category and the text that was replaced."""

    __tablename__ = "guardrail_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    stage: Mapped[str] = mapped_column(String(16), nullable=False, default="input")
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: Never the blocked text itself: an audit trail of harmful output is a copy of
    #: harmful output. A hash plus the detector's reason is enough to reproduce.
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)

    __table_args__ = (Index("ix_guardrail_events_tenant_created", "tenant_id", "created_at"),)


class QaAnalystRun(Base):
    """One AI QA analyst run: the metrics and what they cost."""

    __tablename__ = "qa_analyst_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    calls_analysed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    clusters: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metered_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    window_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)


class CallSavedView(Base):
    """A saved call-history filter (PART 15, P15-07).

    Stored server-side rather than in the browser: a supervisor's "unresolved
    transfers last week" view has to be the same view on the next machine, and
    a view that exists only in one browser's local storage cannot be shared
    with the team it is meant for.
    """

    __tablename__ = "call_saved_views"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    columns: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_call_saved_view_name"),
    )
