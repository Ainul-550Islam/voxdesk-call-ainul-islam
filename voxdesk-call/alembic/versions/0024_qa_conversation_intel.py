"""qa conversation intelligence

Scorecards, reviews, evidence references and AI suggestion runs. Call, Turn
and recording storage stay where they are. This revision does not copy them.

Revision ID: 0024_qa_conversation_intel
Revises: 0023_contact_center_acd
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# FIX: revision id shortened from the 38-80 char string
# "0024_qa_conversation_intelligence" (originally 33 chars) because Alembic's
# default alembic_version.version_num is VARCHAR(32) and
# PostgreSQL rejects the longer value with
# StringDataRightTruncationError, aborting `alembic upgrade head`.
# No deployed database can have recorded the old id: the write itself
# was impossible on Postgres, so renaming is safe.
revision = "0024_qa_conversation_intel"
down_revision = "0023_contact_center_acd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qa_scorecards",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("pass_threshold", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", "version", name="uq_qa_scorecards_version"),
        sa.CheckConstraint("version >= 1", name="ck_qa_scorecards_version"),
        sa.CheckConstraint("pass_threshold >= 0 AND pass_threshold <= 10000", name="ck_qa_scorecards_threshold"),
    )
    op.create_index("ix_qa_scorecards_tenant", "qa_scorecards", ["tenant_id", "status"])
    op.create_table(
        "qa_scorecard_sections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scorecard_id", sa.Uuid(), sa.ForeignKey("qa_scorecards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("scorecard_id", "name", name="uq_qa_sections_name"),
        sa.CheckConstraint("weight >= 1", name="ck_qa_sections_weight"),
    )
    op.create_table(
        "qa_scorecard_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scorecard_id", sa.Uuid(), sa.ForeignKey("qa_scorecards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", sa.Uuid(), sa.ForeignKey("qa_scorecard_sections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("min_score", sa.Integer(), nullable=False),
        sa.Column("max_score", sa.Integer(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("allow_na", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("section_id", "name", name="uq_qa_items_name"),
        sa.CheckConstraint("weight >= 1", name="ck_qa_items_weight"),
        sa.CheckConstraint("max_score > min_score", name="ck_qa_items_range"),
    )
    op.create_table(
        "qa_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("queue_id", sa.Uuid(), nullable=True),
        sa.Column("scorecard_id", sa.Uuid(), sa.ForeignKey("qa_scorecards.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("scorecard_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("open_key", sa.String(80), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("overall_score", sa.Integer(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("calculation_snapshot", sa.JSON(), nullable=False),
        sa.Column("prior_snapshots", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_by", sa.Uuid(), nullable=True),
        sa.UniqueConstraint("open_key", name="uq_qa_reviews_open"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_qa_reviews_idempotency"),
    )
    op.create_index("ix_qa_reviews_tenant_status", "qa_reviews", ["tenant_id", "status"])
    op.create_index("ix_qa_reviews_call", "qa_reviews", ["tenant_id", "call_id"])
    op.create_table(
        "qa_review_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_id", sa.Uuid(), sa.ForeignKey("qa_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", sa.Uuid(), sa.ForeignKey("qa_scorecard_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("human_score", sa.Integer(), nullable=True),
        sa.Column("human_na", sa.Boolean(), nullable=False),
        sa.Column("ai_score", sa.Integer(), nullable=True),
        sa.Column("ai_na", sa.Boolean(), nullable=False),
        sa.Column("accepted_source", sa.String(16), nullable=False),
        sa.Column("override_reason", sa.String(300), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("review_id", "item_id", name="uq_qa_review_items"),
    )
    op.create_table(
        "qa_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_id", sa.Uuid(), sa.ForeignKey("qa_reviews.id", ondelete="CASCADE"), nullable=True),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("turn_id", sa.Uuid(), sa.ForeignKey("turns.id", ondelete="SET NULL"), nullable=True),
        sa.Column("speaker", sa.String(16), nullable=False),
        sa.Column("start_ms", sa.Integer(), nullable=True),
        sa.Column("end_ms", sa.Integer(), nullable=True),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("evidence_type", sa.String(32), nullable=False),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_qa_evidence_review", "qa_evidence", ["tenant_id", "review_id"])
    op.create_table(
        "qa_findings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=True),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("summary", sa.String(300), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_qa_findings_idempotency"),
    )
    op.create_table(
        "qa_compliance_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("required_phrase", sa.String(200), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "code", "version", name="uq_qa_policies_version"),
    )
    op.create_table(
        "qa_compliance_findings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=True),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("policy_id", sa.Uuid(), sa.ForeignKey("qa_compliance_policies.id", ondelete="SET NULL"), nullable=True),
        sa.Column("policy_code", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("reviewer_disposition", sa.String(32), nullable=False),
        sa.Column("remediation_state", sa.String(32), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_qa_compliance_idempotency"),
    )
    op.create_index("ix_qa_compliance_call", "qa_compliance_findings", ["tenant_id", "call_id"])
    op.create_table(
        "qa_coaching_signals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_id", sa.Uuid(), sa.ForeignKey("qa_reviews.id", ondelete="SET NULL"), nullable=True),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("recommendation", sa.String(500), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_qa_coaching_agent", "qa_coaching_signals", ["tenant_id", "agent_user_id"])
    op.create_table(
        "qa_calibration_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reference_review_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("resolution_notes", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "qa_calibration_scores",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", sa.Uuid(), sa.ForeignKey("qa_calibration_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=False),
        sa.Column("item_key", sa.String(40), nullable=False),
        sa.Column("reference_score", sa.Integer(), nullable=False),
        sa.Column("reviewer_score", sa.Integer(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("notes", sa.String(300), nullable=False),
        sa.UniqueConstraint("session_id", "reviewer_id", "review_id", "item_key", name="uq_qa_calibration_score"),
    )
    op.create_table(
        "qa_sampling_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("percent", sa.Integer(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("min_per_agent", sa.Integer(), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("queue_id", sa.Uuid(), nullable=True),
        sa.Column("agent_user_id", sa.Uuid(), nullable=True),
        sa.Column("qos_threshold", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_qa_sampling_name"),
    )
    op.create_table(
        "qa_sample_selections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_id", sa.Uuid(), sa.ForeignKey("qa_sampling_rules.id", ondelete="CASCADE"), nullable=False),
        sa.Column("window_key", sa.String(40), nullable=False),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=True),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "rule_id", "window_key", "call_id", name="uq_qa_sample_once"),
    )
    op.create_table(
        "qa_auto_review_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_id", sa.Uuid(), sa.ForeignKey("qa_reviews.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scorecard_id", sa.Uuid(), nullable=False),
        sa.Column("scorecard_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("error_class", sa.String(64), nullable=False),
        sa.Column("suggestion", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_qa_auto_review_key"),
    )
    op.create_index("ix_qa_auto_review_review", "qa_auto_review_runs", ["tenant_id", "review_id"])
    op.create_table(
        "qa_sentiment_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("scope_key", sa.String(120), nullable=False),
        sa.Column("label", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "scope_key", name="uq_qa_sentiment_scope"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_qa_sentiment_confidence"),
    )
    op.create_index("ix_qa_sentiment_call", "qa_sentiment_results", ["tenant_id", "call_id"])
    op.create_table(
        "qa_topic_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("call_id", sa.Uuid(), sa.ForeignKey("calls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("taxonomy_version", sa.String(40), nullable=False),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=False),
        sa.Column("rank", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("evidence_turn_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "call_id", "taxonomy_version", "label", name="uq_qa_topics_label"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 100", name="ck_qa_topics_confidence"),
    )


def downgrade() -> None:
    op.drop_table("qa_topic_results")
    op.drop_index("ix_qa_sentiment_call", table_name="qa_sentiment_results")
    op.drop_table("qa_sentiment_results")
    op.drop_index("ix_qa_auto_review_review", table_name="qa_auto_review_runs")
    op.drop_table("qa_auto_review_runs")
    op.drop_table("qa_sample_selections")
    op.drop_table("qa_sampling_rules")
    op.drop_table("qa_calibration_scores")
    op.drop_table("qa_calibration_sessions")
    op.drop_index("ix_qa_coaching_agent", table_name="qa_coaching_signals")
    op.drop_table("qa_coaching_signals")
    op.drop_index("ix_qa_compliance_call", table_name="qa_compliance_findings")
    op.drop_table("qa_compliance_findings")
    op.drop_table("qa_compliance_policies")
    op.drop_table("qa_findings")
    op.drop_index("ix_qa_evidence_review", table_name="qa_evidence")
    op.drop_table("qa_evidence")
    op.drop_table("qa_review_items")
    op.drop_index("ix_qa_reviews_call", table_name="qa_reviews")
    op.drop_index("ix_qa_reviews_tenant_status", table_name="qa_reviews")
    op.drop_table("qa_reviews")
    op.drop_table("qa_scorecard_items")
    op.drop_table("qa_scorecard_sections")
    op.drop_index("ix_qa_scorecards_tenant", table_name="qa_scorecards")
    op.drop_table("qa_scorecards")
