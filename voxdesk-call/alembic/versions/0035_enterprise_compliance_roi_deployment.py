"""Enterprise compliance, ROI measurement, and deployment control plane.

Revision: 0035_enterprise_compliance_roi_deployment
Revises: 0034_review_and_specialized_persistence
"""
from __future__ import annotations
import sqlalchemy as sa
from alembic import op

revision = "0035_enterprise_compliance_roi_deployment"
down_revision = "0034_review_and_specialized_persistence"
branch_labels = None
depends_on = None
U=sa.Uuid; S=sa.String; J=sa.JSON; D=sa.DateTime(timezone=True)
def _stamp(): return sa.func.now()
def _tenant_rls(table: str):
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"CREATE POLICY {table}_tenant_isolation ON {table} USING (tenant_id::text = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"))
def _scope_cols():
    return [sa.Column("tenant_id",U(),sa.ForeignKey("tenants.id",ondelete="CASCADE"),nullable=False),sa.Column("organization_id",U(),sa.ForeignKey("organizations.id",ondelete="CASCADE"),nullable=False),sa.Column("environment_id",U(),sa.ForeignKey("environments.id",ondelete="RESTRICT"),nullable=False)]
def _indexes(table, *, with_status=False):
    for col in ("tenant_id","organization_id","environment_id"):
        op.create_index(f"ix_{table}_{col}",table,[col])
    if with_status:
        op.create_index(f"ix_{table}_status",table,["status"])
def upgrade():
    op.create_table("compliance_frameworks",sa.Column("id",U(),primary_key=True),*_scope_cols(),sa.Column("framework_type",S(24),nullable=False),sa.Column("name",S(200),nullable=False),sa.Column("version",S(100),nullable=False),sa.Column("status",S(24),nullable=False),sa.Column("configuration",J(),nullable=False),sa.Column("effective_from",D),sa.Column("effective_to",D),sa.Column("created_at",D,nullable=False,server_default=_stamp()),sa.Column("updated_at",D,nullable=False,server_default=_stamp()),sa.UniqueConstraint("tenant_id","environment_id","framework_type","name","version",name="uq_compliance_framework_version"))
    op.create_table("compliance_controls",sa.Column("id",U(),primary_key=True),sa.Column("framework_id",U(),sa.ForeignKey("compliance_frameworks.id",ondelete="CASCADE"),nullable=False),sa.Column("control_key",S(120),nullable=False),sa.Column("name",S(300),nullable=False),sa.Column("description",sa.Text(),nullable=False),sa.Column("severity",S(24),nullable=False),sa.Column("source_reference",S(1000)),sa.Column("required_evidence",J(),nullable=False),sa.Column("rules",J(),nullable=False),sa.Column("active",sa.Boolean(),nullable=False),sa.Column("created_at",D,nullable=False,server_default=_stamp()),sa.Column("updated_at",D,nullable=False,server_default=_stamp()),sa.UniqueConstraint("framework_id","control_key",name="uq_compliance_control_key"))
    op.create_table("compliance_findings",sa.Column("id",U(),primary_key=True),sa.Column("framework_id",U(),sa.ForeignKey("compliance_frameworks.id",ondelete="CASCADE"),nullable=False),sa.Column("control_id",U(),sa.ForeignKey("compliance_controls.id",ondelete="RESTRICT"),nullable=False),*_scope_cols(),sa.Column("subject_type",S(100),nullable=False),sa.Column("subject_id",S(200),nullable=False),sa.Column("status",S(32),nullable=False),sa.Column("severity",S(24),nullable=False),sa.Column("result",J(),nullable=False),sa.Column("rationale",sa.Text(),nullable=False),sa.Column("source_reference",S(1000)),sa.Column("evidence_fingerprint",S(64),nullable=False),sa.Column("review_required",sa.Boolean(),nullable=False),sa.Column("created_at",D,nullable=False,server_default=_stamp()),sa.Column("updated_at",D,nullable=False,server_default=_stamp()),sa.UniqueConstraint("tenant_id","environment_id","framework_id","control_id","subject_type","subject_id","evidence_fingerprint",name="uq_compliance_finding_dedupe"))
    op.create_table("compliance_remediations",sa.Column("id",U(),primary_key=True),sa.Column("finding_id",U(),sa.ForeignKey("compliance_findings.id",ondelete="CASCADE"),nullable=False),*_scope_cols(),sa.Column("status",S(24),nullable=False),sa.Column("owner_id",U(),sa.ForeignKey("users.id",ondelete="SET NULL")),sa.Column("due_at",D),sa.Column("resolution_reference",S(1000)),sa.Column("resolved_at",D),sa.Column("created_at",D,nullable=False,server_default=_stamp()),sa.Column("updated_at",D,nullable=False,server_default=_stamp()),sa.UniqueConstraint("finding_id",name="uq_compliance_remediation_finding"))
    for table in ("compliance_frameworks","compliance_findings","compliance_remediations"):
        _indexes(table)
    op.create_index("ix_compliance_framework_scope","compliance_frameworks",["tenant_id","organization_id","environment_id","framework_type","status"])
    op.create_index("ix_compliance_finding_scope","compliance_findings",["tenant_id","organization_id","environment_id","status","created_at"])
    op.create_index("ix_compliance_remediation_scope","compliance_remediations",["tenant_id","organization_id","environment_id","status"])
    op.create_index("ix_compliance_controls_framework_id","compliance_controls",["framework_id"])
    op.create_index("ix_compliance_controls_framework", "compliance_controls", ["framework_id","active"])
    op.create_index("ix_compliance_findings_framework_id","compliance_findings",["framework_id"])
    op.create_index("ix_compliance_findings_control_id","compliance_findings",["control_id"])
    op.create_index("ix_compliance_remediations_finding_id","compliance_remediations",["finding_id"])
    op.create_table("roi_baselines",sa.Column("id",U(),primary_key=True),*_scope_cols(),sa.Column("period",S(32),nullable=False),sa.Column("metrics",J(),nullable=False),sa.Column("assumptions_version",S(100),nullable=False),sa.Column("source_references",J(),nullable=False),sa.Column("created_by",U(),sa.ForeignKey("users.id",ondelete="SET NULL")),sa.Column("created_at",D,nullable=False,server_default=_stamp()),sa.UniqueConstraint("tenant_id","environment_id","period","assumptions_version",name="uq_roi_baseline_period"))
    op.create_table("roi_outcomes",sa.Column("id",U(),primary_key=True),*_scope_cols(),sa.Column("period",S(32),nullable=False),sa.Column("metrics",J(),nullable=False),sa.Column("source_references",J(),nullable=False),sa.Column("idempotency_key",S(200),nullable=False),sa.Column("created_at",D,nullable=False,server_default=_stamp()),sa.UniqueConstraint("tenant_id","environment_id","idempotency_key",name="uq_roi_outcome_idempotency"))
    op.create_table("roi_costs",sa.Column("id",U(),primary_key=True),*_scope_cols(),sa.Column("period",S(32),nullable=False),sa.Column("component",S(40),nullable=False),sa.Column("amount",sa.Numeric(18,6)),sa.Column("currency",S(8),nullable=False),sa.Column("source_reference",S(500),nullable=False),sa.Column("source_fingerprint",S(64),nullable=False),sa.Column("created_at",D,nullable=False,server_default=_stamp()),sa.UniqueConstraint("tenant_id","environment_id","source_fingerprint",name="uq_roi_cost_source"))
    op.create_table("roi_kpis",sa.Column("id",U(),primary_key=True),*_scope_cols(),sa.Column("key",S(100),nullable=False),sa.Column("name",S(200),nullable=False),sa.Column("source_definition",J(),nullable=False),sa.Column("formula",J(),nullable=False),sa.Column("unit",S(40),nullable=False),sa.Column("aggregation",S(40),nullable=False),sa.Column("version",sa.Integer(),nullable=False),sa.Column("active",sa.Boolean(),nullable=False),sa.Column("created_at",D,nullable=False,server_default=_stamp()),sa.UniqueConstraint("tenant_id","environment_id","key","version",name="uq_roi_kpi_version"))
    op.create_table("roi_results",sa.Column("id",U(),primary_key=True),*_scope_cols(),sa.Column("period",S(32),nullable=False),sa.Column("baseline_id",U(),sa.ForeignKey("roi_baselines.id",ondelete="RESTRICT"),nullable=False),sa.Column("outcome_id",U(),sa.ForeignKey("roi_outcomes.id",ondelete="RESTRICT"),nullable=False),sa.Column("result",J(),nullable=False),sa.Column("data_quality",S(32),nullable=False),sa.Column("calculation_version",S(100),nullable=False),sa.Column("assumptions_version",S(100),nullable=False),sa.Column("evidence_event_id",U(),sa.ForeignKey("governance_evidence_events.id",ondelete="SET NULL")),sa.Column("created_at",D,nullable=False,server_default=_stamp()),sa.UniqueConstraint("tenant_id","environment_id","baseline_id","outcome_id",name="uq_roi_result_inputs"))
    op.create_table("roi_kpi_evaluations",sa.Column("id",U(),primary_key=True),*_scope_cols(),sa.Column("kpi_id",U(),sa.ForeignKey("roi_kpis.id",ondelete="CASCADE"),nullable=False),sa.Column("period",S(32),nullable=False),sa.Column("result",J(),nullable=False),sa.Column("data_quality",S(32),nullable=False),sa.Column("evidence_event_id",U(),sa.ForeignKey("governance_evidence_events.id",ondelete="SET NULL")),sa.Column("created_at",D,nullable=False,server_default=_stamp()))
    op.create_table("deployment_targets",sa.Column("id",U(),primary_key=True),*_scope_cols(),sa.Column("idempotency_key",S(200),nullable=False),sa.Column("target_type",S(32),nullable=False),sa.Column("provider",S(120)),sa.Column("region",S(120)),sa.Column("cluster_reference",S(500)),sa.Column("network_mode",S(32),nullable=False),sa.Column("data_residency_intent",J(),nullable=False),sa.Column("governance_requirements",J(),nullable=False),sa.Column("prerequisites",J(),nullable=False),sa.Column("status",S(24),nullable=False),sa.Column("created_at",D,nullable=False,server_default=_stamp()),sa.Column("updated_at",D,nullable=False,server_default=_stamp()),sa.UniqueConstraint("tenant_id","environment_id","idempotency_key",name="uq_deployment_target_idempotency"))
    op.create_table("deployment_revisions",sa.Column("id",U(),primary_key=True),sa.Column("target_id",U(),sa.ForeignKey("deployment_targets.id",ondelete="CASCADE"),nullable=False),*_scope_cols(),sa.Column("revision_number",sa.Integer(),nullable=False),sa.Column("state",S(24),nullable=False),sa.Column("artifact_reference",S(1000),nullable=False),sa.Column("artifact_digest",S(128),nullable=False),sa.Column("configuration_fingerprint",S(64),nullable=False),sa.Column("migration_revision",S(100),nullable=False),sa.Column("runtime_version",S(100),nullable=False),sa.Column("manifest_fingerprint",S(64),nullable=False),sa.Column("verification_state",S(32),nullable=False),sa.Column("policy_decision_id",U(),sa.ForeignKey("governance_policy_decisions.id",ondelete="SET NULL")),sa.Column("evidence_root",S(64)),sa.Column("created_at",D,nullable=False,server_default=_stamp()),sa.UniqueConstraint("target_id","revision_number",name="uq_deployment_revision_number"),sa.UniqueConstraint("target_id","manifest_fingerprint",name="uq_deployment_revision_manifest"))
    op.create_table("deployment_artifacts",sa.Column("id",U(),primary_key=True),sa.Column("revision_id",U(),sa.ForeignKey("deployment_revisions.id",ondelete="CASCADE"),nullable=False),*_scope_cols(),sa.Column("artifact_reference",S(1000),nullable=False),sa.Column("artifact_digest",S(128),nullable=False),sa.Column("manifest_fingerprint",S(64),nullable=False),sa.Column("metadata",J(),nullable=False),sa.Column("created_at",D,nullable=False,server_default=_stamp()),sa.UniqueConstraint("revision_id","artifact_digest",name="uq_deployment_artifact_digest"))
    op.create_table("deployment_readiness",sa.Column("id",U(),primary_key=True),sa.Column("target_id",U(),sa.ForeignKey("deployment_targets.id",ondelete="CASCADE"),nullable=False),*_scope_cols(),sa.Column("readiness",S(24),nullable=False),sa.Column("checks",J(),nullable=False),sa.Column("created_at",D,nullable=False,server_default=_stamp()))
    op.create_table("deployment_verifications",sa.Column("id",U(),primary_key=True),sa.Column("revision_id",U(),sa.ForeignKey("deployment_revisions.id",ondelete="CASCADE"),nullable=False),*_scope_cols(),sa.Column("state",S(32),nullable=False),sa.Column("authoritative_verifier",S(200)),sa.Column("evidence_reference",S(1000)),sa.Column("observed_fingerprint",S(128)),sa.Column("created_at",D,nullable=False,server_default=_stamp()))
    for table in ("roi_baselines","roi_outcomes","roi_costs","roi_kpis","roi_results","roi_kpi_evaluations","deployment_targets","deployment_revisions","deployment_artifacts","deployment_readiness","deployment_verifications"):
        _indexes(table)
    op.create_index("ix_roi_baseline_scope","roi_baselines",["tenant_id","organization_id","environment_id","period"])
    op.create_index("ix_roi_outcome_scope_period","roi_outcomes",["tenant_id","organization_id","environment_id","period"])
    op.create_index("ix_roi_cost_scope_period","roi_costs",["tenant_id","organization_id","environment_id","period"])
    op.create_index("ix_roi_kpi_scope_active","roi_kpis",["tenant_id","organization_id","environment_id","active"])
    op.create_index("ix_roi_result_scope_period","roi_results",["tenant_id","organization_id","environment_id","period"])
    op.create_index("ix_deployment_target_scope_status","deployment_targets",["tenant_id","organization_id","environment_id","status"])
    op.create_index("ix_deployment_revision_scope_state","deployment_revisions",["tenant_id","organization_id","environment_id","state"])
    op.create_index("ix_deployment_artifact_scope","deployment_artifacts",["tenant_id","organization_id","environment_id"])
    op.create_index("ix_deployment_revisions_target_id","deployment_revisions",["target_id"])
    op.create_index("ix_deployment_artifacts_revision_id","deployment_artifacts",["revision_id"])
    op.create_index("ix_deployment_readiness_target_id","deployment_readiness",["target_id"])
    op.create_index("ix_deployment_verifications_revision_id","deployment_verifications",["revision_id"])
    if op.get_bind().dialect.name=="postgresql":
        for table in ("compliance_frameworks","compliance_findings","compliance_remediations","roi_baselines","roi_outcomes","roi_costs","roi_kpis","roi_results","roi_kpi_evaluations","deployment_targets","deployment_revisions","deployment_artifacts","deployment_readiness","deployment_verifications"):_tenant_rls(table)
        op.execute(sa.text("ALTER TABLE compliance_controls ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text("ALTER TABLE compliance_controls FORCE ROW LEVEL SECURITY"))
        op.execute(sa.text("CREATE POLICY compliance_controls_tenant_isolation ON compliance_controls USING (EXISTS (SELECT 1 FROM compliance_frameworks f WHERE f.id = framework_id AND f.tenant_id::text = current_setting('app.tenant_id', true))) WITH CHECK (EXISTS (SELECT 1 FROM compliance_frameworks f WHERE f.id = framework_id AND f.tenant_id::text = current_setting('app.tenant_id', true)))"))

def downgrade():
    tables=("deployment_verifications","deployment_readiness","deployment_artifacts","deployment_revisions","deployment_targets","roi_kpi_evaluations","roi_results","roi_kpis","roi_costs","roi_outcomes","roi_baselines","compliance_remediations","compliance_findings","compliance_controls","compliance_frameworks")
    if op.get_bind().dialect.name=="postgresql":
        op.execute(sa.text("DROP POLICY IF EXISTS compliance_controls_tenant_isolation ON compliance_controls"))
        op.execute(sa.text("ALTER TABLE compliance_controls DISABLE ROW LEVEL SECURITY"))
        for table in tables:
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
            op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    for table in tables: op.drop_table(table)
