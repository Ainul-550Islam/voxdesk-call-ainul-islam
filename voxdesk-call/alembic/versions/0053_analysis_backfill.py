"""Persist backfill scope, pinned definition, selection and canonical job IDs.

Legacy rows keep their original counters but have no invented job references.
They are displayed as legacy_unverified, never as running/completed work.
Drain analysis.backfill workers before downgrade; export manifests first.
"""
from alembic import op
import sqlalchemy as sa

revision = "0053_analysis_backfill"
down_revision = "0052_analysis_versions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("backfill_jobs", sa.Column("environment_id", sa.Uuid(), nullable=True))
    for name, default in (("schema_snapshot", "{}"), ("call_ids", "[]"), ("job_ids", "[]")):
        op.add_column("backfill_jobs", sa.Column(name, sa.JSON(), nullable=False, server_default=default))
    op.execute("UPDATE backfill_jobs SET environment_id = (SELECT environment_id FROM analysis_schemas WHERE analysis_schemas.id = backfill_jobs.schema_id AND analysis_schemas.tenant_id = backfill_jobs.tenant_id)")
    op.create_foreign_key("fk_backfill_environment", "backfill_jobs", "environments", ["environment_id"], ["id"])


def downgrade():
    op.drop_constraint("fk_backfill_environment", "backfill_jobs", type_="foreignkey")
    for name in ("job_ids", "call_ids", "schema_snapshot", "environment_id"):
        op.drop_column("backfill_jobs", name)
