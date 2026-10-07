"""Scoped deployment runtime observations and evidence metadata.

This canonical filename is retained for the required migration target. The
Alembic revision ID is a shorter stable identifier because Alembic's default
``version_num`` column is ``VARCHAR(32)``. Its parent is the distinct,
already-existing ``0036_durable_call_outcomes`` revision. The migration
compatibility helper maps the previous overlength ID for databases that may
have recorded it in SQLite or a custom version table.
"""
from __future__ import annotations
import sqlalchemy as sa
from alembic import op

# The former 37-character revision ID exceeded Alembic's default
# alembic_version.version_num VARCHAR(32) on PostgreSQL. The short ID below is
# canonical; app/db/migration_compatibility.py recognizes the previous value
# for SQLite or custom version tables that may already contain it.
revision = "0036_runtime_deployment_observ"
down_revision = "0036_durable_call_outcomes"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"CREATE POLICY {table}_tenant_isolation ON {table} USING (tenant_id::text = current_setting('app.tenant_id', true)) WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"))


def upgrade() -> None:
    op.create_table(
        "deployment_runtime_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("target_id", sa.Uuid(), sa.ForeignKey("deployment_targets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("revision_id", sa.Uuid(), sa.ForeignKey("deployment_revisions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("environment_id", sa.Uuid(), sa.ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("adapter", sa.String(80), nullable=False),
        sa.Column("adapter_version", sa.String(80), nullable=False, server_default="unknown"),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("observed_revision", sa.String(200)),
        sa.Column("observed_artifact", sa.String(1000)),
        sa.Column("health_state", sa.String(32), nullable=False, server_default="not_verified"),
        sa.Column("observed", sa.Boolean(), nullable=False),
        sa.Column("deployed", sa.Boolean(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("observed_digest", sa.String(128)),
        sa.Column("observed_fingerprint", sa.String(128)),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("reason", sa.String(300)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for column in ("target_id", "revision_id", "tenant_id", "organization_id", "environment_id"):
        op.create_index(f"ix_deployment_runtime_observations_{column}", "deployment_runtime_observations", [column])
    op.create_index("ix_runtime_observation_scope_time", "deployment_runtime_observations", ["tenant_id", "organization_id", "environment_id", "created_at"])
    if op.get_bind().dialect.name == "postgresql":
        _rls("deployment_runtime_observations")
        op.execute(sa.text("""
            CREATE OR REPLACE FUNCTION reject_deployment_observation_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'deployment runtime observations are append-only';
            END;
            $$
        """))
        op.execute(sa.text("CREATE TRIGGER trg_deployment_runtime_observations_append_only BEFORE UPDATE OR DELETE ON deployment_runtime_observations FOR EACH ROW EXECUTE FUNCTION reject_deployment_observation_mutation()"))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("DROP TRIGGER IF EXISTS trg_deployment_runtime_observations_append_only ON deployment_runtime_observations"))
        op.execute(sa.text("DROP FUNCTION IF EXISTS reject_deployment_observation_mutation()"))
        op.execute(sa.text("DROP POLICY IF EXISTS deployment_runtime_observations_tenant_isolation ON deployment_runtime_observations"))
        op.execute(sa.text("ALTER TABLE deployment_runtime_observations DISABLE ROW LEVEL SECURITY"))
    op.drop_index("ix_runtime_observation_scope_time", table_name="deployment_runtime_observations")
    for column in ("environment_id", "organization_id", "tenant_id", "revision_id", "target_id"):
        op.drop_index(f"ix_deployment_runtime_observations_{column}", table_name="deployment_runtime_observations")
    op.drop_table("deployment_runtime_observations")
