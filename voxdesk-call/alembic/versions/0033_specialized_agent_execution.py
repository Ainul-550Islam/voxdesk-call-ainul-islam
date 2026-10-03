"""Durable specialized-agent execution and result metadata.

Revision: 0033_specialized_agent_execution
Revises: 0032_enterprise_governance_found

The main execution row stores fingerprints, governance references, lifecycle
state, and redacted result metadata. Raw prompts, source text, credentials, and
full provider responses are intentionally not columns in this revision.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_specialized_agent_execution"
down_revision = "0032_enterprise_governance_found"
branch_labels = None
depends_on = None

_UUID = sa.Uuid
_STR = sa.String
_JSON = sa.JSON
_DT = sa.DateTime(timezone=True)


def _now():
    return sa.func.now()


def _tenant_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
    expression = "tenant_id::text = current_setting('app.tenant_id', true)"
    op.execute(
        sa.text(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING ({expression}) WITH CHECK ({expression})"
        )
    )


def upgrade() -> None:
    op.create_table(
        "specialized_agent_executions",
        sa.Column("id", _UUID(), primary_key=True),
        sa.Column("tenant_id", _UUID(), nullable=False),
        sa.Column("organization_id", _UUID(), nullable=False),
        sa.Column("environment_id", _UUID(), nullable=False),
        sa.Column("request_id", _STR(128), nullable=False),
        sa.Column("trace_id", _STR(128), nullable=False),
        sa.Column("idempotency_key", _STR(200), nullable=False),
        sa.Column("agent_type", _STR(32), nullable=False),
        sa.Column("agent_version", _STR(100), nullable=False),
        sa.Column(
            "model_version_id",
            _UUID(),
            sa.ForeignKey("governance_model_versions.id"),
            nullable=False,
        ),
        sa.Column("risk_tier", _STR(24), nullable=False),
        sa.Column("status", _STR(24), nullable=False),
        sa.Column("input_fingerprint", _STR(64), nullable=False),
        sa.Column("output_fingerprint", _STR(64), nullable=True),
        sa.Column(
            "policy_decision_id",
            _UUID(),
            sa.ForeignKey("governance_policy_decisions.id"),
            nullable=True,
        ),
        sa.Column(
            "lineage_root_id",
            _UUID(),
            sa.ForeignKey("governance_lineage_records.id"),
            nullable=True,
        ),
        sa.Column("evidence_root_hash", _STR(64), nullable=True),
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_state", _STR(24), nullable=False),
        sa.Column("result", _JSON(), nullable=False),
        sa.Column("failure_code", _STR(100), nullable=True),
        sa.Column("started_at", _DT, nullable=True),
        sa.Column("completed_at", _DT, nullable=True),
        sa.Column("created_at", _DT, nullable=False, server_default=_now()),
        sa.Column("updated_at", _DT, nullable=False, server_default=_now()),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_specialized_execution_idempotency"),
        sa.CheckConstraint(
            "status IN ('pending','admitted','running','review_required','succeeded','failed','denied','cancelled')",
            name="ck_specialized_execution_status",
        ),
    )
    for name, columns in (
        ("ix_specialized_execution_tenant_status", ["tenant_id", "organization_id", "status"]),
        ("ix_specialized_execution_request", ["tenant_id", "request_id"]),
        ("ix_specialized_execution_trace", ["tenant_id", "trace_id"]),
        ("ix_specialized_execution_environment", ["tenant_id", "organization_id", "environment_id"]),
        ("ix_specialized_execution_model", ["tenant_id", "model_version_id"]),
        ("ix_specialized_execution_policy", ["tenant_id", "policy_decision_id"]),
        ("ix_specialized_execution_lineage", ["tenant_id", "lineage_root_id"]),
    ):
        op.create_index(name, "specialized_agent_executions", columns)

    op.create_table(
        "specialized_agent_legal_reviews",
        sa.Column("execution_id", _UUID(), sa.ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", _UUID(), nullable=False),
        sa.Column("organization_id", _UUID(), nullable=False),
        sa.Column("environment_id", _UUID(), nullable=False),
        sa.Column("document_id", _STR(200), nullable=False),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column("review_state", _STR(24), nullable=False),
        sa.Column("source_fingerprints", _JSON(), nullable=False),
        sa.Column("result_metadata", _JSON(), nullable=False),
        sa.Column("created_at", _DT, nullable=False, server_default=_now()),
    )
    op.create_index("ix_specialized_legal_tenant", "specialized_agent_legal_reviews", ["tenant_id", "organization_id", "environment_id"])

    op.create_table(
        "specialized_agent_translation_jobs",
        sa.Column("execution_id", _UUID(), sa.ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", _UUID(), nullable=False),
        sa.Column("organization_id", _UUID(), nullable=False),
        sa.Column("environment_id", _UUID(), nullable=False),
        sa.Column("source_language", _STR(32), nullable=False),
        sa.Column("target_language", _STR(32), nullable=False),
        sa.Column("glossary_version", _STR(100), nullable=False),
        sa.Column("segment_count", sa.Integer(), nullable=False),
        sa.Column("quality_status", _STR(32), nullable=False),
        sa.Column("review_state", _STR(24), nullable=False),
        sa.Column("metadata", _JSON(), nullable=False),
        sa.Column("created_at", _DT, nullable=False, server_default=_now()),
    )
    op.create_index("ix_specialized_translation_tenant", "specialized_agent_translation_jobs", ["tenant_id", "organization_id", "environment_id"])

    op.create_table(
        "specialized_agent_glossary_versions",
        sa.Column("id", _UUID(), primary_key=True),
        sa.Column("tenant_id", _UUID(), nullable=False),
        sa.Column("organization_id", _UUID(), nullable=False),
        sa.Column("environment_id", _UUID(), nullable=False),
        sa.Column("version", _STR(100), nullable=False),
        sa.Column("entries", _JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", _DT, nullable=False, server_default=_now()),
        sa.UniqueConstraint("tenant_id", "environment_id", "version", name="uq_specialized_glossary_version"),
    )
    op.create_index("ix_specialized_glossary_tenant", "specialized_agent_glossary_versions", ["tenant_id", "organization_id", "environment_id"])

    op.create_table(
        "specialized_agent_anomaly_runs",
        sa.Column("execution_id", _UUID(), sa.ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", _UUID(), nullable=False),
        sa.Column("organization_id", _UUID(), nullable=False),
        sa.Column("environment_id", _UUID(), nullable=False),
        sa.Column("metric", _STR(200), nullable=False),
        sa.Column("configuration", _JSON(), nullable=False),
        sa.Column("quality_state", _STR(32), nullable=False),
        sa.Column("results", _JSON(), nullable=False),
        sa.Column("created_at", _DT, nullable=False, server_default=_now()),
    )
    op.create_index("ix_specialized_anomaly_tenant", "specialized_agent_anomaly_runs", ["tenant_id", "organization_id", "environment_id"])

    if op.get_bind().dialect.name == "postgresql":
        for table in (
            "specialized_agent_executions",
            "specialized_agent_legal_reviews",
            "specialized_agent_translation_jobs",
            "specialized_agent_glossary_versions",
            "specialized_agent_anomaly_runs",
        ):
            _tenant_rls(table)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in (
            "specialized_agent_anomaly_runs",
            "specialized_agent_glossary_versions",
            "specialized_agent_translation_jobs",
            "specialized_agent_legal_reviews",
            "specialized_agent_executions",
        ):
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
            op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    for table in (
        "specialized_agent_anomaly_runs",
        "specialized_agent_glossary_versions",
        "specialized_agent_translation_jobs",
        "specialized_agent_legal_reviews",
        "specialized_agent_executions",
    ):
        op.drop_table(table)
