"""Durable voice profiles and provider-backed voice clone jobs.

Revision: 0030_voice_runtime
Revises: 0029_prompt3_surfaces
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_voice_runtime"
down_revision = "0029_prompt3_surfaces"
branch_labels = None
depends_on = None

TENANT_TABLES = ("voice_profiles", "voice_clone_jobs")


def _uuid() -> sa.Uuid:
    return sa.Uuid()


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "voice_profiles" not in tables:
        op.create_table(
            "voice_profiles",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tenant_id", _uuid(), nullable=False),
            sa.Column("environment_id", _uuid(), nullable=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("provider", sa.String(64), nullable=False),
            sa.Column("provider_voice_id", sa.String(255), nullable=False),
            sa.Column("language", sa.String(32), nullable=False, server_default="en-US"),
            sa.Column("locale", sa.String(32), nullable=False, server_default="en-US"),
            sa.Column("voice_type", sa.String(24), nullable=False, server_default="provider"),
            sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
            sa.Column("capabilities", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_by", _uuid(), nullable=True),
            _created(),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
            sa.UniqueConstraint(
                "tenant_id", "provider", "provider_voice_id", name="uq_voice_profile_provider_voice"
            ),
        )
        op.create_index("ix_voice_profiles_tenant", "voice_profiles", ["tenant_id"])
        op.create_index(
            "ix_voice_profiles_tenant_status", "voice_profiles", ["tenant_id", "status"]
        )
        op.create_index(
            "ix_voice_profiles_tenant_name", "voice_profiles", ["tenant_id", "name"]
        )
    if "voice_clone_jobs" not in tables:
        op.create_table(
            "voice_clone_jobs",
            sa.Column("id", _uuid(), primary_key=True),
            sa.Column("tenant_id", _uuid(), nullable=False),
            sa.Column("environment_id", _uuid(), nullable=True),
            sa.Column("voice_profile_id", _uuid(), nullable=True),
            sa.Column("provider", sa.String(64), nullable=False),
            sa.Column("provider_job_id", sa.String(255), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="requested"),
            sa.Column("input_object_reference", sa.String(1000), nullable=False),
            sa.Column("request_fingerprint", sa.String(64), nullable=False),
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("error_message_redacted", sa.String(500), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("requested_by", _uuid(), nullable=True),
            _created(),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["voice_profile_id"], ["voice_profiles.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
            sa.UniqueConstraint(
                "tenant_id", "request_fingerprint", name="uq_voice_clone_job_idempotency"
            ),
        )
        op.create_index("ix_voice_clone_jobs_tenant", "voice_clone_jobs", ["tenant_id"])
        op.create_index(
            "ix_voice_clone_jobs_tenant_status", "voice_clone_jobs", ["tenant_id", "status"]
        )
        op.create_index(
            "ix_voice_clone_jobs_provider_job", "voice_clone_jobs", ["provider", "provider_job_id"]
        )

    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
            op.execute(
                sa.text(
                    f"CREATE POLICY {table}_tenant_isolation ON {table} "
                    "USING (tenant_id::text = current_setting('app.tenant_id', true)) "
                    "WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"
                )
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in TENANT_TABLES:
            op.execute(sa.text(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}"))
            op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
