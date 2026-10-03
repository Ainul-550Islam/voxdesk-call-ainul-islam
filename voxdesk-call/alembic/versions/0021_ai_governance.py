"""ai governance persistence

Prompt versions, model policy, evaluation datasets and an admission counter.
No provider API key is stored. Billing usage events are not copied.

Revision ID: 0021_ai_governance
Revises: 0020_durable_enterprise_ops
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021_ai_governance"
down_revision = "0020_durable_enterprise_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_model_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("allowed_presets", sa.JSON(), nullable=False),
        sa.Column("disabled_providers", sa.JSON(), nullable=False),
        sa.Column("development_only_presets", sa.JSON(), nullable=False),
        sa.Column("token_ceiling", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", name="uq_ai_model_policies_tenant"),
        sa.CheckConstraint("status IN ('active', 'disabled')", name="ck_ai_model_policies_status"),
    )
    op.create_table(
        "ai_prompts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_scope", sa.String(64), nullable=False),
        sa.Column("prompt_key", sa.String(80), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=True),
        sa.Column("current_version", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "environment_scope", "prompt_key", name="uq_ai_prompts_scope_key"),
        sa.CheckConstraint("status IN ('active', 'retired')", name="ck_ai_prompts_status"),
    )
    op.create_index("ix_ai_prompts_tenant_id", "ai_prompts", ["tenant_id"])
    op.create_table(
        "ai_prompt_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("prompt_id", sa.Uuid(), sa.ForeignKey("ai_prompts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("prompt_id", "version_number", name="uq_ai_prompt_versions_number"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'retired')", name="ck_ai_prompt_versions_status"
        ),
    )
    op.create_index("ix_ai_prompt_versions_prompt_id", "ai_prompt_versions", ["prompt_id"])
    op.create_index("ix_ai_prompt_versions_tenant_id", "ai_prompt_versions", ["tenant_id"])
    op.create_table(
        "ai_prompt_rollouts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("prompt_id", sa.Uuid(), sa.ForeignKey("ai_prompts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_scope", sa.String(64), nullable=False),
        sa.Column("stable_version", sa.Integer(), nullable=False),
        sa.Column("canary_version", sa.Integer(), nullable=True),
        sa.Column("percent", sa.Integer(), nullable=False),
        sa.Column("salt", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "prompt_id", "environment_scope", name="uq_ai_prompt_rollouts_scope"
        ),
        sa.CheckConstraint("percent >= 0 AND percent <= 100", name="ck_ai_prompt_rollouts_percent"),
    )
    op.create_index("ix_ai_prompt_rollouts_tenant_id", "ai_prompt_rollouts", ["tenant_id"])
    op.create_table(
        "ai_eval_datasets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("cases", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_ai_eval_datasets_name"),
    )
    op.create_index("ix_ai_eval_datasets_tenant_id", "ai_eval_datasets", ["tenant_id"])
    op.create_table(
        "ai_eval_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dataset_id", sa.Uuid(), sa.ForeignKey("ai_eval_datasets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("case_limit", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('completed', 'failed', 'cancelled')", name="ck_ai_eval_runs_status"
        ),
    )
    op.create_index("ix_ai_eval_runs_tenant_id", "ai_eval_runs", ["tenant_id"])
    op.create_table(
        "ai_admission_counters",
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tokens_reserved", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ai_admission_counters")
    op.drop_index("ix_ai_eval_runs_tenant_id", table_name="ai_eval_runs")
    op.drop_table("ai_eval_runs")
    op.drop_index("ix_ai_eval_datasets_tenant_id", table_name="ai_eval_datasets")
    op.drop_table("ai_eval_datasets")
    op.drop_index("ix_ai_prompt_rollouts_tenant_id", table_name="ai_prompt_rollouts")
    op.drop_table("ai_prompt_rollouts")
    op.drop_index("ix_ai_prompt_versions_tenant_id", table_name="ai_prompt_versions")
    op.drop_index("ix_ai_prompt_versions_prompt_id", table_name="ai_prompt_versions")
    op.drop_table("ai_prompt_versions")
    op.drop_index("ix_ai_prompts_tenant_id", table_name="ai_prompts")
    op.drop_table("ai_prompts")
    op.drop_table("ai_model_policies")
