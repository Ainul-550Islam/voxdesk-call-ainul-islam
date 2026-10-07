"""Scope custom schemas and preserve immutable version/provenance metadata.

Legacy definitions are assigned only to their tenant's default environment.
Legacy results are scoped only when their call's tenant agrees. Orphan results
remain unverified with NULL scope/version; no result is deleted or certified.
Downgrade drops version history: drain workers and export results first.
"""
from alembic import op
import sqlalchemy as sa

revision = "0052_analysis_versions"
down_revision = "0051_post_call_pipeline"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("analysis_schemas", sa.Column("environment_id", sa.Uuid(), nullable=True))
    op.add_column("analysis_schemas", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("analysis_schemas", sa.Column("revisions", sa.JSON(), nullable=False, server_default="{}"))
    op.execute("UPDATE analysis_schemas SET environment_id = (SELECT id FROM environments WHERE environments.tenant_id = analysis_schemas.tenant_id AND is_default = true)")
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT count(*) FROM analysis_schemas WHERE environment_id IS NULL")).scalar():
        raise RuntimeError("Legacy analysis schemas require a tenant default environment before migration")
    op.alter_column("analysis_schemas", "environment_id", nullable=False)
    op.create_foreign_key("fk_analysis_schema_environment", "analysis_schemas", "environments", ["environment_id"], ["id"])
    op.add_column("analysis_results", sa.Column("environment_id", sa.Uuid(), nullable=True))
    op.add_column("analysis_results", sa.Column("schema_version", sa.Integer(), nullable=True))
    op.add_column("analysis_results", sa.Column("schema_snapshot", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("analysis_results", sa.Column("provenance", sa.String(32), nullable=False, server_default="legacy_unverified"))
    op.execute("UPDATE analysis_results SET environment_id = (SELECT environment_id FROM calls WHERE calls.id = analysis_results.call_id AND calls.tenant_id = analysis_results.tenant_id)")
    op.create_foreign_key("fk_analysis_result_environment", "analysis_results", "environments", ["environment_id"], ["id"])
    op.create_unique_constraint("uq_analysis_result_version", "analysis_results", ["schema_id", "call_id", "schema_version"])


def downgrade():
    op.drop_constraint("uq_analysis_result_version", "analysis_results", type_="unique")
    op.drop_constraint("fk_analysis_result_environment", "analysis_results", type_="foreignkey")
    for column in ("provenance", "schema_snapshot", "schema_version", "environment_id"):
        op.drop_column("analysis_results", column)
    op.drop_constraint("fk_analysis_schema_environment", "analysis_schemas", type_="foreignkey")
    for column in ("revisions", "version", "environment_id"):
        op.drop_column("analysis_schemas", column)
