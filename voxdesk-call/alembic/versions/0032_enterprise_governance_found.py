"""Enterprise AI governance foundation.

Revision: 0032_enterprise_governance_found
Revises: 0031_workflow_persistence_hard

This revision is additive. It does not alter the existing AI policy/runtime
schema or workflow tables. Customer-scoped rows carry tenant and organization
scope, PostgreSQL RLS follows the established transaction-local tenant
setting, and evidence events receive a database append-only guard.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# FIX: revision id shortened from the 38-80 char string
# "0032_enterprise_governance_foundation" (originally 37 chars) because Alembic's
# default alembic_version.version_num is VARCHAR(32) and
# PostgreSQL rejects the longer value with
# StringDataRightTruncationError, aborting `alembic upgrade head`.
# No deployed database can have recorded the old id: the write itself
# was impossible on Postgres, so renaming is safe.
revision = "0032_enterprise_governance_found"
down_revision = "0031_workflow_persistence_hard"
branch_labels = None
depends_on = None


_STR = sa.String
_UUID = sa.Uuid
_JSON = sa.JSON
_DT = sa.DateTime(timezone=True)


def _now() -> sa.sql.functions.Function:
    return sa.func.now()


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # AuditLog remains the existing audit bridge. Its native enum needs one
        # additive label for successful governance events.
        op.execute(sa.text("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'GOVERNANCE_EVENT'"))

    op.create_table(
        "governance_policies",
        sa.Column("id", _UUID(), primary_key=True),
        sa.Column("tenant_id", _UUID(), nullable=True),
        sa.Column("organization_id", _UUID(), nullable=True),
        sa.Column("environment_id", _UUID(), nullable=True),
        sa.Column("name", _STR(200), nullable=False),
        sa.Column("policy_type", _STR(40), nullable=False),
        sa.Column("status", _STR(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rules", _JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("created_by", _UUID(), nullable=True),
        sa.Column("published_by", _UUID(), nullable=True),
        sa.Column("effective_from", _DT, nullable=True),
        sa.Column("effective_to", _DT, nullable=True),
        sa.Column("created_at", _DT, nullable=False, server_default=_now()),
        sa.Column("updated_at", _DT, nullable=False, server_default=_now()),
        sa.Column("published_at", _DT, nullable=True),
        sa.CheckConstraint(
            "tenant_id IS NOT NULL OR organization_id IS NOT NULL", name="ck_gov_policy_scope"
        ),
        sa.UniqueConstraint(
            "tenant_id", "policy_type", "name", "version", name="uq_gov_policy_version"
        ),
    )
    op.create_index(
        "ix_gov_policy_tenant_type_status",
        "governance_policies",
        ["tenant_id", "policy_type", "status"],
    )

    op.create_table(
        "governance_policy_decisions",
        sa.Column("id", _UUID(), primary_key=True),
        sa.Column("tenant_id", _UUID(), nullable=False),
        sa.Column("organization_id", _UUID(), nullable=False),
        sa.Column("environment_id", _UUID(), nullable=True),
        sa.Column("principal_id", _UUID(), nullable=True),
        sa.Column("policy_id", _UUID(), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=True),
        sa.Column("decision", _STR(16), nullable=False),
        sa.Column("reason_code", _STR(100), nullable=False),
        sa.Column("input_fingerprint", _STR(64), nullable=False),
        sa.Column("output_fingerprint", _STR(64), nullable=True),
        sa.Column("correlation_id", _STR(128), nullable=True),
        sa.Column("request_metadata", _JSON(), nullable=False),
        sa.Column("created_at", _DT, nullable=False, server_default=_now()),
        sa.ForeignKeyConstraint(["policy_id"], ["governance_policies.id"]),
    )
    op.create_index(
        "ix_gov_decision_scope_created",
        "governance_policy_decisions",
        ["tenant_id", "organization_id", "created_at"],
    )
    op.create_index(
        "ix_gov_decision_correlation", "governance_policy_decisions", ["correlation_id"]
    )

    op.create_table(
        "governance_risk_assessments",
        sa.Column("id", _UUID(), primary_key=True),
        sa.Column("tenant_id", _UUID(), nullable=False),
        sa.Column("organization_id", _UUID(), nullable=False),
        sa.Column("environment_id", _UUID(), nullable=True),
        sa.Column("subject_type", _STR(60), nullable=False),
        sa.Column("subject_id", _STR(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("tier", _STR(24), nullable=False),
        sa.Column("status", _STR(32), nullable=False),
        sa.Column("factors", _JSON(), nullable=False),
        sa.Column("required_controls", _JSON(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("assessed_by", _UUID(), nullable=True),
        sa.Column("approved_by", _UUID(), nullable=True),
        sa.Column("assessed_at", _DT, nullable=False, server_default=_now()),
        sa.Column("approved_at", _DT, nullable=True),
        sa.Column("updated_at", _DT, nullable=False, server_default=_now()),
        sa.UniqueConstraint(
            "tenant_id", "subject_type", "subject_id", "version", name="uq_gov_risk_version"
        ),
    )
    op.create_index(
        "ix_gov_risk_tenant_status", "governance_risk_assessments", ["tenant_id", "status"]
    )

    op.create_table(
        "governance_model_registries",
        sa.Column("id", _UUID(), primary_key=True),
        sa.Column("tenant_id", _UUID(), nullable=True),
        sa.Column("organization_id", _UUID(), nullable=True),
        sa.Column("provider", _STR(100), nullable=False),
        sa.Column("model_name", _STR(200), nullable=False),
        sa.Column("display_name", _STR(200), nullable=False),
        sa.Column("family", _STR(200), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("status", _STR(24), nullable=False),
        sa.Column("risk_tier", _STR(24), nullable=False),
        sa.Column("intended_use", sa.Text(), nullable=False),
        sa.Column("data_classes", _JSON(), nullable=False),
        sa.Column("capabilities", _JSON(), nullable=False),
        sa.Column("approved_for_channels", _JSON(), nullable=False),
        sa.Column("approved_for_environments", _JSON(), nullable=False),
        sa.Column("owner_user_id", _UUID(), nullable=True),
        sa.Column("metadata", _JSON(), nullable=False),
        sa.Column("created_at", _DT, nullable=False, server_default=_now()),
        sa.Column("updated_at", _DT, nullable=False, server_default=_now()),
        sa.UniqueConstraint("tenant_id", "provider", "model_name", name="uq_gov_model_registry_name"),
    )
    op.create_index(
        "ix_gov_model_registry_scope_status",
        "governance_model_registries",
        ["tenant_id", "organization_id", "status"],
    )

    op.create_table(
        "governance_model_versions",
        sa.Column("id", _UUID(), primary_key=True),
        sa.Column("registry_id", _UUID(), nullable=False),
        sa.Column("tenant_id", _UUID(), nullable=True),
        sa.Column("organization_id", _UUID(), nullable=True),
        sa.Column("provider", _STR(100), nullable=True),
        sa.Column("model_name", _STR(200), nullable=True),
        sa.Column("version", _STR(100), nullable=False),
        sa.Column("fingerprint", _STR(128), nullable=True),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("artifact_digest", _STR(128), nullable=False),
        sa.Column("capabilities", _JSON(), nullable=False),
        sa.Column("input_modalities", _JSON(), nullable=False),
        sa.Column("output_modalities", _JSON(), nullable=False),
        sa.Column("status", _STR(24), nullable=False),
        sa.Column("evaluation_status", _STR(24), nullable=False),
        sa.Column("approval_reference", _STR(200), nullable=True),
        sa.Column("evaluation_summary", _JSON(), nullable=False),
        sa.Column("created_by", _UUID(), nullable=True),
        sa.Column("approved_by", _UUID(), nullable=True),
        sa.Column("created_at", _DT, nullable=False, server_default=_now()),
        sa.Column("approved_at", _DT, nullable=True),
        sa.Column("retired_at", _DT, nullable=True),
        sa.ForeignKeyConstraint(["registry_id"], ["governance_model_registries.id"]),
        sa.UniqueConstraint("registry_id", "version", name="uq_gov_model_version"),
    )
    op.create_index(
        "ix_gov_model_version_tenant_status",
        "governance_model_versions",
        ["tenant_id", "status"],
    )

    op.create_table(
        "governance_lineage_records",
        sa.Column("id", _UUID(), primary_key=True),
        sa.Column("tenant_id", _UUID(), nullable=False),
        sa.Column("organization_id", _UUID(), nullable=False),
        sa.Column("environment_id", _UUID(), nullable=True),
        sa.Column("correlation_id", _STR(128), nullable=False),
        sa.Column("trace_id", _STR(128), nullable=True),
        sa.Column("request_id", _STR(128), nullable=True),
        sa.Column("subject_type", _STR(80), nullable=True),
        sa.Column("subject_id", _STR(200), nullable=True),
        sa.Column("source_type", _STR(80), nullable=True),
        sa.Column("source_id", _STR(200), nullable=True),
        sa.Column("model_registry_id", _UUID(), nullable=True),
        sa.Column("model_version_id", _UUID(), nullable=True),
        sa.Column("input_fingerprint", _STR(64), nullable=False),
        sa.Column("output_fingerprint", _STR(64), nullable=True),
        sa.Column("tool_name", _STR(200), nullable=True),
        sa.Column("tool_fingerprints", _JSON(), nullable=False),
        sa.Column("source_references", _JSON(), nullable=False),
        sa.Column("parent_lineage_id", _UUID(), nullable=True),
        sa.Column("decision_id", _UUID(), nullable=True),
        sa.Column("metadata", _JSON(), nullable=False),
        sa.Column("created_at", _DT, nullable=False, server_default=_now()),
    )
    op.create_index(
        "ix_gov_lineage_tenant_created", "governance_lineage_records", ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_gov_lineage_correlation", "governance_lineage_records", ["correlation_id"]
    )

    op.create_table(
        "governance_evidence_events",
        sa.Column("id", _UUID(), primary_key=True),
        sa.Column("tenant_id", _UUID(), nullable=False),
        sa.Column("organization_id", _UUID(), nullable=False),
        sa.Column("environment_id", _UUID(), nullable=True),
        sa.Column("chain_scope", _STR(200), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", _STR(80), nullable=False),
        sa.Column("actor_type", _STR(24), nullable=False),
        sa.Column("actor_id", _UUID(), nullable=True),
        sa.Column("subject_type", _STR(80), nullable=True),
        sa.Column("subject_id", _STR(200), nullable=True),
        sa.Column("correlation_id", _STR(128), nullable=True),
        sa.Column("payload", _JSON(), nullable=False),
        sa.Column("payload_hash", _STR(64), nullable=False),
        sa.Column("previous_hash", _STR(64), nullable=True),
        sa.Column("event_hash", _STR(64), nullable=False),
        sa.Column("occurred_at", _DT, nullable=False, server_default=_now()),
        sa.Column("created_at", _DT, nullable=False, server_default=_now()),
        sa.UniqueConstraint("chain_scope", "sequence", name="uq_gov_evidence_sequence"),
        sa.UniqueConstraint("event_hash", name="uq_gov_evidence_hash"),
    )
    op.create_index(
        "ix_gov_evidence_tenant_created", "governance_evidence_events", ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_gov_evidence_chain", "governance_evidence_events", ["chain_scope", "sequence"]
    )

    op.create_table(
        "governance_retention_rules",
        sa.Column("id", _UUID(), primary_key=True),
        sa.Column("tenant_id", _UUID(), nullable=True),
        sa.Column("organization_id", _UUID(), nullable=True),
        sa.Column("evidence_type", _STR(80), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("legal_hold", sa.Boolean(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_by", _UUID(), nullable=True),
        sa.Column("created_at", _DT, nullable=False, server_default=_now()),
        sa.UniqueConstraint("tenant_id", "evidence_type", "version", name="uq_gov_retention_version"),
    )
    op.create_index(
        "ix_gov_retention_scope_active", "governance_retention_rules", ["tenant_id", "active"]
    )

    op.create_table(
        "governance_residency_intents",
        sa.Column("id", _UUID(), primary_key=True),
        sa.Column("tenant_id", _UUID(), nullable=False),
        sa.Column("organization_id", _UUID(), nullable=False),
        sa.Column("environment_id", _UUID(), nullable=True),
        sa.Column("requested_region", _STR(64), nullable=False),
        sa.Column("status", _STR(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("physical_residency_proven", sa.Boolean(), nullable=False),
        sa.Column("authoritative_verifier", _STR(200), nullable=True),
        sa.Column("verification_reference", _STR(200), nullable=True),
        sa.Column("verification_metadata", _JSON(), nullable=False),
        sa.Column("requested_by", _UUID(), nullable=True),
        sa.Column("verified_at", _DT, nullable=True),
        sa.Column("created_at", _DT, nullable=False, server_default=_now()),
        sa.UniqueConstraint(
            "tenant_id", "requested_region", "version", name="uq_gov_residency_version"
        ),
    )
    op.create_index(
        "ix_gov_residency_tenant_status", "governance_residency_intents", ["tenant_id", "status"]
    )

    op.create_table(
        "governance_attestations",
        sa.Column("id", _UUID(), primary_key=True),
        sa.Column("tenant_id", _UUID(), nullable=False),
        sa.Column("organization_id", _UUID(), nullable=False),
        sa.Column("environment_id", _UUID(), nullable=True),
        sa.Column("attestation_type", _STR(80), nullable=False),
        sa.Column("subject_type", _STR(80), nullable=False),
        sa.Column("subject_id", _STR(200), nullable=False),
        sa.Column("period_start", _DT, nullable=True),
        sa.Column("period_end", _DT, nullable=True),
        sa.Column("status", _STR(24), nullable=False),
        sa.Column("issuer", _STR(200), nullable=False),
        sa.Column("issued_by", _UUID(), nullable=True),
        sa.Column("claims", _JSON(), nullable=False),
        sa.Column("metadata", _JSON(), nullable=False),
        sa.Column("evidence_root", _STR(64), nullable=False),
        sa.Column("from_sequence", sa.Integer(), nullable=True),
        sa.Column("to_sequence", sa.Integer(), nullable=True),
        sa.Column("issued_at", _DT, nullable=True),
        sa.Column("expires_at", _DT, nullable=True),
        sa.Column("created_at", _DT, nullable=False, server_default=_now()),
    )
    op.create_index(
        "ix_gov_attestation_tenant_subject",
        "governance_attestations",
        ["tenant_id", "subject_type", "subject_id"],
    )

    op.create_table(
        "governance_evidence_packages",
        sa.Column("id", _UUID(), primary_key=True),
        sa.Column("tenant_id", _UUID(), nullable=False),
        sa.Column("organization_id", _UUID(), nullable=False),
        sa.Column("environment_id", _UUID(), nullable=True),
        sa.Column("status", _STR(24), nullable=False),
        sa.Column("from_sequence", sa.Integer(), nullable=True),
        sa.Column("to_sequence", sa.Integer(), nullable=True),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("evidence_root", _STR(64), nullable=False),
        sa.Column("package_hash", _STR(64), nullable=False),
        sa.Column("manifest", _JSON(), nullable=False),
        sa.Column("created_by", _UUID(), nullable=True),
        sa.Column("created_at", _DT, nullable=False, server_default=_now()),
    )
    op.create_index(
        "ix_gov_package_tenant_created", "governance_evidence_packages", ["tenant_id", "created_at"]
    )

    # Match the model-level tenant/organization/environment indexes used by
    # the shared metadata (the compound indexes above serve common queries).
    for table, columns in (
        ("governance_policies", ("tenant_id", "organization_id", "environment_id")),
        ("governance_policy_decisions", ("tenant_id", "organization_id", "environment_id", "principal_id")),
        ("governance_risk_assessments", ("tenant_id", "organization_id", "environment_id")),
        ("governance_model_registries", ("tenant_id", "organization_id")),
        ("governance_model_versions", ("registry_id", "tenant_id", "organization_id")),
        ("governance_lineage_records", ("tenant_id", "organization_id", "environment_id", "trace_id", "request_id", "model_registry_id", "model_version_id", "parent_lineage_id", "decision_id")),
        ("governance_evidence_events", ("tenant_id", "organization_id", "environment_id")),
        ("governance_retention_rules", ("tenant_id", "organization_id")),
        ("governance_residency_intents", ("tenant_id", "organization_id", "environment_id")),
        ("governance_attestations", ("tenant_id", "organization_id", "environment_id")),
        ("governance_evidence_packages", ("tenant_id", "organization_id", "environment_id")),
    ):
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])

    if op.get_bind().dialect.name == "postgresql":
        strict_tenant_tables = (
            "governance_policy_decisions",
            "governance_risk_assessments",
            "governance_model_versions",
            "governance_lineage_records",
            "governance_evidence_events",
            "governance_residency_intents",
            "governance_attestations",
            "governance_evidence_packages",
        )
        nullable_tenant_tables = (
            "governance_policies",
            "governance_model_registries",
            "governance_retention_rules",
        )
        for table in strict_tenant_tables + nullable_tenant_tables:
            op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
            expression = "tenant_id::text = current_setting('app.tenant_id', true)"
            if table in nullable_tenant_tables:
                expression = f"({expression} OR tenant_id IS NULL)"
            op.execute(
                sa.text(
                    f"CREATE POLICY {table}_tenant_isolation ON {table} "
                    f"USING ({expression}) WITH CHECK ({expression})"
                )
            )
        op.execute(
            sa.text(
                "CREATE OR REPLACE FUNCTION governance_evidence_append_only() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                "IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'governance evidence is append-only'; END IF; "
                "RETURN NEW; END; $$"
            )
        )
        op.execute(
            sa.text(
                "CREATE TRIGGER governance_evidence_append_only_trigger "
                "BEFORE UPDATE OR DELETE ON governance_evidence_events "
                "FOR EACH ROW EXECUTE FUNCTION governance_evidence_append_only()"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "DROP TRIGGER IF EXISTS governance_evidence_append_only_trigger "
                "ON governance_evidence_events"
            )
        )
        op.execute(sa.text("DROP FUNCTION IF EXISTS governance_evidence_append_only()"))
        for table in (
            "governance_policy_decisions",
            "governance_risk_assessments",
            "governance_model_versions",
            "governance_lineage_records",
            "governance_evidence_events",
            "governance_residency_intents",
            "governance_attestations",
            "governance_evidence_packages",
            "governance_policies",
            "governance_model_registries",
            "governance_retention_rules",
        ):
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
            op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))

    for table in (
        "governance_evidence_packages",
        "governance_attestations",
        "governance_residency_intents",
        "governance_retention_rules",
        "governance_evidence_events",
        "governance_lineage_records",
        "governance_model_versions",
        "governance_model_registries",
        "governance_risk_assessments",
        "governance_policy_decisions",
        "governance_policies",
    ):
        op.drop_table(table)
