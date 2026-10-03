"""organization memberships, invitations and quota limits

Adds membership bindings beside the existing ``users.tenant_id``. It does not
change user ids, tenant ids, or the organization ids created in 0017. It does
not add ``environment_id`` to calls, leads, billing or identity.

Backfill creates one active organization membership and one active tenant
membership per existing user, copying that user's role. It does not create a
second production environment and it does not insert a quota row (a missing
quota is unknown, not zero).

Revision ID: 0018_org_memberships_quotas
Revises: 0017_org_environment_foundation
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from alembic import op

# FIX: revision id shortened from the 38-80 char string
# "0018_organization_memberships_quotas" (originally 36 chars) because Alembic's
# default alembic_version.version_num is VARCHAR(32) and
# PostgreSQL rejects the longer value with
# StringDataRightTruncationError, aborting `alembic upgrade head`.
# No deployed database can have recorded the old id: the write itself
# was impossible on Postgres, so renaming is safe.
revision = "0018_org_memberships_quotas"
down_revision = "0017_org_environment_foundation"
branch_labels = None
depends_on = None

NEW_AUDIT_ACTIONS = (
    "MEMBERSHIP_CREATED",
    "MEMBERSHIP_UPDATED",
    "MEMBERSHIP_SUSPENDED",
    "MEMBERSHIP_RESTORED",
    "MEMBERSHIP_REVOKED",
    "INVITATION_CREATED",
    "INVITATION_ACCEPTED",
    "INVITATION_REVOKED",
    "INVITATION_RESENT",
    "ENVIRONMENT_SELECTED",
    "QUOTA_UPDATED",
    "QUOTA_DENIED",
)

_STATUS = "status IN ('invited', 'active', 'suspended', 'revoked', 'expired')"


def _role_type():
    return sa.Enum(
        "OWNER", "ADMIN", "MANAGER", "AGENT", "VIEWER",
        name="userrole",
        create_type=False,
    )


def _uuid_col():
    return sa.Column("id", sa.Uuid(), nullable=False)


def backfill_memberships(connection) -> int:
    """One organization membership and one tenant membership per existing user.

    Idempotent. Does not rewrite user ids, tenant ids or organization ids.
    Returns the number of membership rows inserted.
    """
    rows = connection.execute(sa.text(
        "SELECT u.id AS user_id, u.role AS role, u.tenant_id AS tenant_id, "
        "t.organization_id AS organization_id "
        "FROM users u JOIN tenants t ON t.id = u.tenant_id "
        "WHERE t.organization_id IS NOT NULL"
    )).mappings().all()
    created = 0
    now = datetime.utcnow()
    for row in rows:
        user_id = str(row["user_id"])
        tenant_id = str(row["tenant_id"])
        organization_id = str(row["organization_id"])
        role = row["role"]
        org_exists = connection.execute(
            sa.text(
                "SELECT id FROM organization_memberships "
                "WHERE organization_id = :org AND user_id = :user"
            ),
            {"org": organization_id, "user": user_id},
        ).first()
        if org_exists is None:
            connection.execute(
                sa.text(
                    "INSERT INTO organization_memberships ("
                    "id, organization_id, user_id, role, status, created_at, updated_at, "
                    "invited_at, accepted_at, suspended_at, revoked_at"
                    ") VALUES ("
                    ":id, :org, :user, :role, 'active', :now, :now, NULL, :now, NULL, NULL)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "org": organization_id,
                    "user": user_id,
                    "role": role,
                    "now": now,
                },
            )
            created += 1
        tenant_exists = connection.execute(
            sa.text(
                "SELECT id FROM tenant_memberships "
                "WHERE tenant_id = :tenant AND user_id = :user"
            ),
            {"tenant": tenant_id, "user": user_id},
        ).first()
        if tenant_exists is None:
            connection.execute(
                sa.text(
                    "INSERT INTO tenant_memberships ("
                    "id, tenant_id, user_id, role, status, created_at, updated_at, "
                    "invited_at, accepted_at, suspended_at, revoked_at"
                    ") VALUES ("
                    ":id, :tenant, :user, :role, 'active', :now, :now, NULL, :now, NULL, NULL)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tenant": tenant_id,
                    "user": user_id,
                    "role": role,
                    "now": now,
                },
            )
            created += 1
    return created


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for value in NEW_AUDIT_ACTIONS:
            op.execute(f"ALTER TYPE auditaction ADD VALUE IF NOT EXISTS '{value}'")

    op.create_table(
        "organization_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", _role_type(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_STATUS, name="ck_organization_memberships_status"),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_organization_memberships_principal"
        ),
    )
    op.create_index(
        "ix_organization_memberships_user", "organization_memberships", ["user_id"]
    )

    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", _role_type(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_STATUS, name="ck_tenant_memberships_status"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_tenant_memberships_principal"),
    )
    op.create_index("ix_tenant_memberships_user", "tenant_memberships", ["user_id"])

    op.create_table(
        "environment_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", _role_type(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_STATUS, name="ck_environment_memberships_status"),
        sa.ForeignKeyConstraint(
            ["environment_id"], ["environments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "environment_id", "user_id", name="uq_environment_memberships_principal"
        ),
    )
    op.create_index(
        "ix_environment_memberships_user", "environment_memberships", ["user_id"]
    )

    op.create_table(
        "membership_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("home_tenant_id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", _role_type(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("binding_key", sa.String(length=80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invited_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("accepted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "scope IN ('organization', 'tenant')",
            name="ck_membership_invitations_scope",
        ),
        sa.CheckConstraint(
            "status IN ('invited', 'accepted', 'revoked', 'expired')",
            name="ck_membership_invitations_status",
        ),
        sa.CheckConstraint(
            "(scope = 'organization' AND tenant_id IS NULL) "
            "OR (scope = 'tenant' AND tenant_id IS NOT NULL)",
            name="ck_membership_invitations_scope_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["home_tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_membership_invitations_token"),
    )
    op.create_index(
        "ix_membership_invitations_org", "membership_invitations", ["organization_id"]
    )
    op.create_index(
        "uq_membership_invitations_active",
        "membership_invitations",
        ["binding_key", "email"],
        unique=True,
        sqlite_where=sa.text("status = 'invited'"),
        postgresql_where=sa.text("status = 'invited'"),
    )

    op.create_table(
        "quota_limits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("quota_key", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("limit_value", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_kind IN ('organization', 'tenant', 'environment')",
            name="ck_quota_limits_scope",
        ),
        sa.CheckConstraint(
            "mode IN ('hard', 'soft', 'unlimited', 'unknown')",
            name="ck_quota_limits_mode",
        ),
        sa.CheckConstraint(
            "(mode IN ('hard', 'soft') AND limit_value IS NOT NULL AND limit_value >= 0) "
            "OR (mode IN ('unlimited', 'unknown') AND limit_value IS NULL)",
            name="ck_quota_limits_value",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope_kind", "scope_id", "quota_key", name="uq_quota_limits_scope_key"
        ),
    )
    op.create_index("ix_quota_limits_scope", "quota_limits", ["scope_kind", "scope_id"])

    op.create_table(
        "scope_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("mfa_required", sa.Boolean(), nullable=True),
        sa.Column("mfa_required_for_admins", sa.Boolean(), nullable=True),
        sa.Column("sso_required", sa.Boolean(), nullable=True),
        sa.Column("password_login_allowed", sa.Boolean(), nullable=True),
        sa.Column("api_keys_allowed", sa.Boolean(), nullable=True),
        sa.Column("service_accounts_allowed", sa.Boolean(), nullable=True),
        sa.Column("session_idle_minutes", sa.Integer(), nullable=True),
        sa.Column("session_max_active", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_kind IN ('organization', 'tenant', 'environment')",
            name="ck_scope_policies_kind",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_kind", "scope_id", name="uq_scope_policies_scope"),
    )

    op.create_table(
        "environment_selections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("environment_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["environment_id"], ["environments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "tenant_id", name="uq_environment_selections_principal"
        ),
    )
    op.create_index(
        "ix_environment_selections_environment",
        "environment_selections",
        ["environment_id"],
    )

    backfill_memberships(bind)


def downgrade() -> None:
    # Enum labels stay. Business rows, users, tenants and organizations stay.
    op.drop_index(
        "ix_environment_selections_environment", table_name="environment_selections"
    )
    op.drop_table("environment_selections")
    op.drop_table("scope_policies")
    op.drop_index("ix_quota_limits_scope", table_name="quota_limits")
    op.drop_table("quota_limits")
    op.drop_index(
        "uq_membership_invitations_active", table_name="membership_invitations"
    )
    op.drop_index("ix_membership_invitations_org", table_name="membership_invitations")
    op.drop_table("membership_invitations")
    op.drop_index("ix_environment_memberships_user", table_name="environment_memberships")
    op.drop_table("environment_memberships")
    op.drop_index("ix_tenant_memberships_user", table_name="tenant_memberships")
    op.drop_table("tenant_memberships")
    op.drop_index(
        "ix_organization_memberships_user", table_name="organization_memberships"
    )
    op.drop_table("organization_memberships")
