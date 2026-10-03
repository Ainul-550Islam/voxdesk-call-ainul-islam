"""Durable human review and specialized-agent result projections.

Revision: 0034_review_specialized_persist
Revises: 0033_specialized_agent_execution

Prompt-2 parent executions and Legal/Translation/Glossary/Anomaly run tables
already exist in 0033. This revision extends translation lifecycle metadata
and adds only child/result/review tables required by this batch.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# FIX: revision id shortened from the 38-80 char string
# "0034_review_and_specialized_persistence" (originally 39 chars) because Alembic's
# default alembic_version.version_num is VARCHAR(32) and
# PostgreSQL rejects the longer value with
# StringDataRightTruncationError, aborting `alembic upgrade head`.
# No deployed database can have recorded the old id: the write itself
# was impossible on Postgres, so renaming is safe.
revision = "0034_review_specialized_persist"
down_revision = "0033_specialized_agent_execution"
branch_labels = None
depends_on = None

UUID = sa.Uuid
JSON = sa.JSON
DT = sa.DateTime(timezone=True)
STR = sa.String


def _tenant_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
    predicate = "tenant_id::text = current_setting('app.tenant_id', true)"
    op.execute(sa.text(f"CREATE POLICY {table}_tenant_isolation ON {table} USING ({predicate}) WITH CHECK ({predicate})"))


def upgrade() -> None:
    # Additive lifecycle state on the 0033 translation parent table.
    with op.batch_alter_table("specialized_agent_translation_jobs") as batch:
        batch.add_column(sa.Column("dedupe_key", STR(200), nullable=False, server_default=""))
        batch.add_column(sa.Column("status", STR(24), nullable=False, server_default="completed"))
        batch.add_column(sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("total_segments", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("completed_segments", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("failed_segments", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("started_at", DT, nullable=True))
        batch.add_column(sa.Column("completed_at", DT, nullable=True))
        batch.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"))
        batch.add_column(sa.Column("updated_at", DT, nullable=False, server_default=sa.func.now()))
    op.create_index("ix_specialized_translation_dedupe", "specialized_agent_translation_jobs", ["tenant_id", "organization_id", "environment_id", "dedupe_key"])
    with op.batch_alter_table("specialized_agent_legal_reviews") as batch:
        batch.add_column(sa.Column("source_fingerprint", STR(64), nullable=False, server_default=""))
        batch.add_column(sa.Column("status", STR(32), nullable=False, server_default="review_required"))
        batch.add_column(sa.Column("disclaimer", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("completed_at", DT, nullable=True))
    with op.batch_alter_table("specialized_agent_anomaly_runs") as batch:
        batch.add_column(sa.Column("detector", STR(64), nullable=False, server_default=""))
        batch.add_column(sa.Column("configuration_fingerprint", STR(64), nullable=False, server_default=""))
        batch.add_column(sa.Column("observation_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("status", STR(24), nullable=False, server_default="completed"))

    op.create_table(
        "review_cases",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("tenant_id", UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", UUID(), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("execution_id", UUID(), sa.ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("case_type", STR(64), nullable=False),
        sa.Column("agent_type", STR(32), nullable=False),
        sa.Column("subject_type", STR(80), nullable=False),
        sa.Column("subject_id", STR(200), nullable=False),
        sa.Column("status", STR(32), nullable=False),
        sa.Column("priority", STR(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_controls", JSON(), nullable=False),
        sa.Column("requested_by", UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata", JSON(), nullable=False),
        sa.Column("created_at", DT, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", DT, nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", DT, nullable=True),
        sa.CheckConstraint("status IN ('pending','assigned','in_review','approved','rejected','changes_requested','cancelled','expired')", name="ck_review_case_status"),
    )
    op.create_index("ix_review_cases_scope_status", "review_cases", ["tenant_id", "organization_id", "environment_id", "status"])
    op.create_index("ix_review_cases_execution", "review_cases", ["tenant_id", "execution_id"])
    op.create_index("ix_review_cases_queue", "review_cases", ["tenant_id", "environment_id", "status", "priority", "created_at"])

    op.create_table(
        "review_assignments",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("review_case_id", UUID(), sa.ForeignKey("review_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", UUID(), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reviewer_id", UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("assigned_by", UUID(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", STR(16), nullable=False),
        sa.Column("assignment_version", sa.Integer(), nullable=False),
        sa.Column("assigned_at", DT, nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", DT, nullable=True),
        sa.Column("expires_at", DT, nullable=True),
        sa.UniqueConstraint("review_case_id", "reviewer_id", "assignment_version", name="uq_review_assignment_version"),
        sa.CheckConstraint("status IN ('active','completed','cancelled','expired')", name="ck_review_assignment_status"),
    )
    op.create_index("ix_review_assignments_reviewer", "review_assignments", ["tenant_id", "reviewer_id", "status"])
    op.create_index("ix_review_assignments_case", "review_assignments", ["tenant_id", "review_case_id", "status"])

    op.create_table(
        "review_decisions",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("review_case_id", UUID(), sa.ForeignKey("review_cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assignment_id", UUID(), sa.ForeignKey("review_assignments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("tenant_id", UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", UUID(), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reviewer_id", UUID(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("decision", STR(24), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence", JSON(), nullable=False),
        sa.Column("policy_decision_id", UUID(), sa.ForeignKey("governance_policy_decisions.id"), nullable=True),
        sa.Column("evidence_event_id", UUID(), sa.ForeignKey("governance_evidence_events.id"), nullable=True),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("created_at", DT, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("review_case_id", "decision_version", name="uq_review_decision_version"),
        sa.CheckConstraint("decision IN ('approve','reject','request_changes')", name="ck_review_decision_type"),
    )
    op.create_index("ix_review_decisions_scope", "review_decisions", ["tenant_id", "organization_id", "environment_id", "created_at"])

    op.create_table(
        "specialized_agent_legal_citations",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("execution_id", UUID(), sa.ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", UUID(), nullable=False), sa.Column("organization_id", UUID(), nullable=False), sa.Column("environment_id", UUID(), nullable=False),
        sa.Column("document_id", STR(200), nullable=False), sa.Column("chunk_id", STR(200), nullable=False), sa.Column("source_title", STR(500), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True), sa.Column("character_start", sa.Integer(), nullable=True), sa.Column("character_end", sa.Integer(), nullable=True),
        sa.Column("retrieval_timestamp", DT, nullable=True), sa.Column("content_fingerprint", STR(64), nullable=False),
        sa.UniqueConstraint("execution_id", "document_id", "chunk_id", "content_fingerprint", name="uq_legal_citation_source"),
    )
    op.create_table(
        "specialized_agent_legal_findings",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("execution_id", UUID(), sa.ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", UUID(), nullable=False), sa.Column("organization_id", UUID(), nullable=False), sa.Column("environment_id", UUID(), nullable=False),
        sa.Column("finding_index", sa.Integer(), nullable=False), sa.Column("clause", STR(200), nullable=False), sa.Column("category", STR(200), nullable=False),
        sa.Column("issue_type", STR(100), nullable=False), sa.Column("severity", STR(32), nullable=False), sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("extracted_text_reference", STR(128), nullable=False), sa.Column("location", JSON(), nullable=False), sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("recommended_follow_up", sa.Text(), nullable=False), sa.Column("citation_id", UUID(), sa.ForeignKey("specialized_agent_legal_citations.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("execution_id", "finding_index", name="uq_legal_finding_index"),
    )
    op.create_index("ix_legal_findings_scope", "specialized_agent_legal_findings", ["tenant_id", "organization_id", "environment_id"])
    op.create_table(
        "specialized_agent_legal_outcomes",
        sa.Column("id", UUID(), primary_key=True),
        sa.Column("execution_id", UUID(), sa.ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("review_case_id", UUID(), sa.ForeignKey("review_cases.id", ondelete="SET NULL"), nullable=True),
        sa.Column("tenant_id", UUID(), nullable=False), sa.Column("organization_id", UUID(), nullable=False), sa.Column("environment_id", UUID(), nullable=False),
        sa.Column("outcome_version", sa.Integer(), nullable=False), sa.Column("outcome", STR(32), nullable=False), sa.Column("rationale_fingerprint", STR(64), nullable=True),
        sa.Column("evidence_event_id", UUID(), sa.ForeignKey("governance_evidence_events.id"), nullable=True), sa.Column("metadata", JSON(), nullable=False), sa.Column("created_at", DT, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("execution_id", "outcome_version", name="uq_legal_outcome_version"),
    )

    op.create_table(
        "specialized_agent_translation_segments",
        sa.Column("id", UUID(), primary_key=True), sa.Column("execution_id", UUID(), sa.ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", UUID(), nullable=False), sa.Column("organization_id", UUID(), nullable=False), sa.Column("environment_id", UUID(), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False), sa.Column("segment_id", STR(200), nullable=False), sa.Column("source_fingerprint", STR(64), nullable=False),
        sa.Column("target_fingerprint", STR(64), nullable=True), sa.Column("source_length", sa.Integer(), nullable=False, server_default="0"), sa.Column("target_length", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", STR(24), nullable=False), sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("review_required", sa.Boolean(), nullable=False), sa.Column("quality_metadata", JSON(), nullable=False),
        sa.UniqueConstraint("execution_id", "segment_index", name="uq_translation_segment_index"),
    )
    op.create_index("ix_translation_segments_scope", "specialized_agent_translation_segments", ["tenant_id", "organization_id", "environment_id"])
    op.create_table(
        "specialized_agent_translation_progress",
        sa.Column("execution_id", UUID(), sa.ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", UUID(), nullable=False), sa.Column("organization_id", UUID(), nullable=False), sa.Column("environment_id", UUID(), nullable=False),
        sa.Column("total_segments", sa.Integer(), nullable=False), sa.Column("completed_segments", sa.Integer(), nullable=False), sa.Column("failed_segments", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False), sa.Column("state", STR(24), nullable=False), sa.Column("review_state", STR(24), nullable=False), sa.Column("last_error", STR(500), nullable=False), sa.Column("updated_at", DT, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "specialized_agent_translation_attempts",
        sa.Column("id", UUID(), primary_key=True), sa.Column("execution_id", UUID(), sa.ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", UUID(), nullable=False), sa.Column("organization_id", UUID(), nullable=False), sa.Column("environment_id", UUID(), nullable=False),
        sa.Column("segment_index", sa.Integer(), nullable=False), sa.Column("attempt_number", sa.Integer(), nullable=False), sa.Column("status", STR(24), nullable=False),
        sa.Column("error_code", STR(100), nullable=False), sa.Column("source_fingerprint", STR(64), nullable=False), sa.Column("target_fingerprint", STR(64), nullable=True), sa.Column("created_at", DT, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("execution_id", "segment_index", "attempt_number", name="uq_translation_attempt"),
    )

    op.create_table(
        "specialized_agent_anomaly_results",
        sa.Column("id", UUID(), primary_key=True), sa.Column("execution_id", UUID(), sa.ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", UUID(), nullable=False), sa.Column("organization_id", UUID(), nullable=False), sa.Column("environment_id", UUID(), nullable=False),
        sa.Column("result_index", sa.Integer(), nullable=False), sa.Column("metric", STR(200), nullable=False), sa.Column("observation_time", DT, nullable=False), sa.Column("observed_value", sa.Float(), nullable=False),
        sa.Column("baseline", sa.Float(), nullable=True), sa.Column("deviation", sa.Float(), nullable=True), sa.Column("detector", STR(64), nullable=False), sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("severity", STR(24), nullable=False), sa.Column("confidence", sa.Float(), nullable=False), sa.Column("quality_state", STR(32), nullable=False), sa.Column("anomalous", sa.Boolean(), nullable=False),
        sa.Column("review_required", sa.Boolean(), nullable=False), sa.Column("explanation", sa.Text(), nullable=False), sa.UniqueConstraint("execution_id", "result_index", name="uq_anomaly_result_index"),
    )
    op.create_index("ix_anomaly_results_scope", "specialized_agent_anomaly_results", ["tenant_id", "organization_id", "environment_id"])
    op.create_table(
        "specialized_agent_anomaly_alerts",
        sa.Column("id", UUID(), primary_key=True), sa.Column("execution_id", UUID(), sa.ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", UUID(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False), sa.Column("organization_id", UUID(), nullable=False), sa.Column("environment_id", UUID(), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("result_id", UUID(), sa.ForeignKey("specialized_agent_anomaly_results.id", ondelete="SET NULL"), nullable=True), sa.Column("dedupe_key", STR(200), nullable=False), sa.Column("severity", STR(24), nullable=False),
        sa.Column("delivery_state", STR(24), nullable=False), sa.Column("outbox_event_id", UUID(), sa.ForeignKey("outbox_events.id", ondelete="SET NULL"), nullable=True), sa.Column("delivery_detail", STR(500), nullable=False),
        sa.Column("created_at", DT, nullable=False, server_default=sa.func.now()), sa.Column("updated_at", DT, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "environment_id", "dedupe_key", name="uq_anomaly_alert_dedupe"),
        sa.CheckConstraint("delivery_state IN ('queued','sent','failed','suppressed')", name="ck_anomaly_alert_delivery_state"),
    )
    op.create_index("ix_anomaly_alert_scope_state", "specialized_agent_anomaly_alerts", ["tenant_id", "organization_id", "environment_id", "delivery_state"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION reject_completed_glossary_mutation() RETURNS trigger AS $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM specialized_agent_translation_jobs job
                    WHERE job.tenant_id = OLD.tenant_id
                      AND job.organization_id = OLD.organization_id
                      AND job.environment_id = OLD.environment_id
                      AND job.glossary_version = OLD.version
                      AND job.status = 'completed'
                ) THEN
                    RAISE EXCEPTION 'glossary version is referenced by a completed translation job';
                END IF;
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))
        op.execute(sa.text("CREATE TRIGGER trg_glossary_immutable_if_completed BEFORE UPDATE OR DELETE ON specialized_agent_glossary_versions FOR EACH ROW EXECUTE FUNCTION reject_completed_glossary_mutation()"))

    tables = (
        "review_cases", "review_assignments", "review_decisions",
        "specialized_agent_legal_citations", "specialized_agent_legal_findings", "specialized_agent_legal_outcomes",
        "specialized_agent_translation_segments", "specialized_agent_translation_progress", "specialized_agent_translation_attempts",
        "specialized_agent_anomaly_results", "specialized_agent_anomaly_alerts",
    )
    if op.get_bind().dialect.name == "postgresql":
        for table in tables:
            _tenant_rls(table)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_glossary_immutable_if_completed ON specialized_agent_glossary_versions"))
        op.execute(sa.text("DROP FUNCTION IF EXISTS reject_completed_glossary_mutation()"))
    tables = (
        "specialized_agent_anomaly_alerts", "specialized_agent_anomaly_results",
        "specialized_agent_translation_attempts", "specialized_agent_translation_progress", "specialized_agent_translation_segments",
        "specialized_agent_legal_outcomes", "specialized_agent_legal_findings", "specialized_agent_legal_citations",
        "review_decisions", "review_assignments", "review_cases",
    )
    if bind.dialect.name == "postgresql":
        for table in tables:
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
            op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    for table in tables:
        op.drop_table(table)
    op.drop_index("ix_specialized_translation_dedupe", table_name="specialized_agent_translation_jobs")
    with op.batch_alter_table("specialized_agent_anomaly_runs") as batch:
        batch.drop_column("status")
        batch.drop_column("observation_count")
        batch.drop_column("configuration_fingerprint")
        batch.drop_column("detector")
    with op.batch_alter_table("specialized_agent_legal_reviews") as batch:
        batch.drop_column("completed_at")
        batch.drop_column("review_required")
        batch.drop_column("disclaimer")
        batch.drop_column("status")
        batch.drop_column("source_fingerprint")
    with op.batch_alter_table("specialized_agent_translation_jobs") as batch:
        batch.drop_column("updated_at")
        batch.drop_column("completed_at")
        batch.drop_column("started_at")
        batch.drop_column("failed_segments")
        batch.drop_column("completed_segments")
        batch.drop_column("total_segments")
        batch.drop_column("review_required")
        batch.drop_column("max_attempts")
        batch.drop_column("attempt_count")
        batch.drop_column("status")
        batch.drop_column("dedupe_key")
