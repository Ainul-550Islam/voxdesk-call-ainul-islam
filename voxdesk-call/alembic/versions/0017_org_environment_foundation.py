"""organization, tenant parent, and environment foundation

Adds the hierarchy around the existing ``tenants`` row. It does not rename
``tenants``, does not change ``tenants.id`` or ``twilio_number`` uniqueness,
and does not add ``environment_id`` to calls, leads, billing or identity.

* ``organizations`` — one parent. Backfill creates one per existing tenant,
  named from that tenant, not one global Default Organization.
* ``tenants.organization_id`` — nullable only while the backfill runs, then
  NOT NULL. ``tenants.lifecycle_status`` defaults to ``active`` and does not
  rewrite ``is_active``.
* ``environments`` — one production environment per existing tenant. Slug is
  unique within a tenant. One production and one default are enforced by
  unique guard columns (NULL does not collide, so development and staging can
  be added later).

The new ``AuditAction`` labels are the member names, upper case, matching
``Enum(AuditAction)``. Downgrade drops the new tables and columns. It does
not remove enum labels and it does not delete business rows.

Revision ID: 0017_org_environment_foundation
Revises: 0016_sso_account_unlinked
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from alembic import op

from app.organization.models import legacy_organization_name, legacy_organization_slug

# FIX: revision id shortened from the 38-80 char string
# "0017_organization_environment_foundation" (originally 40 chars) because Alembic's
# default alembic_version.version_num is VARCHAR(32) and
# PostgreSQL rejects the longer value with
# StringDataRightTruncationError, aborting `alembic upgrade head`.
# No deployed database can have recorded the old id: the write itself
# was impossible on Postgres, so renaming is safe.
revision = "0017_org_environment_foundation"
down_revision = "0016_sso_account_unlinked"
branch_labels = None
depends_on = None

NEW_AUDIT_ACTIONS = (
    "ORGANIZATION_CREATED",
    "ORGANIZATION_UPDATED",
    "ORGANIZATION_SUSPENDED",
    "ORGANIZATION_RESTORED",
    "ORGANIZATION_READ_ONLY",
    "TENANT_LIFECYCLE_CHANGED",
    "ENVIRONMENT_CREATED",
    "ENVIRONMENT_UPDATED",
    "ENVIRONMENT_SUSPENDED",
    "ENVIRONMENT_RESTORED",
    "ENVIRONMENT_ARCHIVED",
    "ENVIRONMENT_DEFAULT_CHANGED",
)


def _bind_id(value, dialect: str):
    parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    if dialect == "postgresql":
        return str(parsed)
    return str(parsed)


def backfill_tenants(connection) -> int:
    """Attach one organization and one production environment to each tenant.

    Idempotent: a tenant that already has ``organization_id`` is not given a
    second parent. A tenant that already has a production environment is not
    given a second one. Tenant primary keys are not rewritten. Returns the
    number of organizations created.
    """
    dialect = connection.dialect.name
    rows = connection.execute(
        sa.text("SELECT id, name, organization_id FROM tenants")
    ).mappings().all()
    created = 0
    now = datetime.utcnow()
    for row in rows:
        tenant_id = uuid.UUID(str(row["id"]))
        if row["organization_id"] is None:
            org_id = uuid.uuid4()
            _insert_organization(
                connection,
                dialect,
                org_id=org_id,
                name=legacy_organization_name(row["name"]),
                slug=legacy_organization_slug(tenant_id),
                now=now,
            )
            _assign_organization(connection, dialect, tenant_id=tenant_id, org_id=org_id)
            created += 1
        _ensure_production(connection, dialect, tenant_id=tenant_id, now=now)
    return created


def _insert_organization(connection, dialect: str, *, org_id, name: str, slug: str, now) -> None:
    if dialect == "postgresql":
        statement = sa.text(
            "INSERT INTO organizations (id, name, slug, status, created_at, updated_at) "
            "VALUES (CAST(:id AS uuid), :name, :slug, 'active', :now, :now)"
        )
    else:
        statement = sa.text(
            "INSERT INTO organizations (id, name, slug, status, created_at, updated_at) "
            "VALUES (:id, :name, :slug, 'active', :now, :now)"
        )
    connection.execute(
        statement,
        {"id": _bind_id(org_id, dialect), "name": name, "slug": slug, "now": now},
    )


def _assign_organization(connection, dialect: str, *, tenant_id, org_id) -> None:
    if dialect == "postgresql":
        statement = sa.text(
            "UPDATE tenants SET organization_id = CAST(:org AS uuid) "
            "WHERE id = CAST(:tid AS uuid)"
        )
    else:
        statement = sa.text(
            "UPDATE tenants SET organization_id = :org WHERE id = :tid"
        )
    connection.execute(
        statement,
        {"org": _bind_id(org_id, dialect), "tid": _bind_id(tenant_id, dialect)},
    )


def _ensure_production(connection, dialect: str, *, tenant_id, now) -> None:
    if dialect == "postgresql":
        existing = connection.execute(
            sa.text(
                "SELECT id FROM environments WHERE tenant_id = CAST(:tid AS uuid) "
                "AND kind = 'production'"
            ),
            {"tid": _bind_id(tenant_id, dialect)},
        ).first()
    else:
        existing = connection.execute(
            sa.text(
                "SELECT id FROM environments WHERE tenant_id = :tid AND kind = 'production'"
            ),
            {"tid": _bind_id(tenant_id, dialect)},
        ).first()
    if existing is not None:
        return
    env_id = uuid.uuid4()
    if dialect == "postgresql":
        statement = sa.text(
            "INSERT INTO environments ("
            "id, tenant_id, name, slug, kind, status, is_default, "
            "production_guard, default_guard, release_version, deployed_at, "
            "deployment_status, deployment_source, health_state, created_at, updated_at"
            ") VALUES ("
            "CAST(:id AS uuid), CAST(:tid AS uuid), 'Production', 'production', "
            "'production', 'active', true, CAST(:tid AS uuid), CAST(:tid AS uuid), "
            "'', NULL, 'idle', '', 'unknown', :now, :now)"
        )
    else:
        statement = sa.text(
            "INSERT INTO environments ("
            "id, tenant_id, name, slug, kind, status, is_default, "
            "production_guard, default_guard, release_version, deployed_at, "
            "deployment_status, deployment_source, health_state, created_at, updated_at"
            ") VALUES ("
            ":id, :tid, 'Production', 'production', 'production', 'active', 1, "
            ":tid, :tid, '', NULL, 'idle', '', 'unknown', :now, :now)"
        )
    connection.execute(
        statement,
        {
            "id": _bind_id(env_id, dialect),
            "tid": _bind_id(tenant_id, dialect),
            "now": now,
        },
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for value in NEW_AUDIT_ACTIONS:
            op.execute(f"ALTER TYPE auditaction ADD VALUE IF NOT EXISTS '{value}'")

    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'read_only', 'deleted')",
            name="ck_organizations_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )

    op.add_column("tenants", sa.Column("organization_id", sa.Uuid(), nullable=True))
    op.add_column(
        "tenants",
        sa.Column(
            "lifecycle_status",
            sa.String(length=16),
            nullable=False,
            server_default="active",
        ),
    )

    op.create_table(
        "environments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("production_guard", sa.Uuid(), nullable=True),
        sa.Column("default_guard", sa.Uuid(), nullable=True),
        sa.Column("release_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "deployment_status", sa.String(length=16), nullable=False, server_default="idle"
        ),
        sa.Column(
            "deployment_source", sa.String(length=64), nullable=False, server_default=""
        ),
        sa.Column(
            "health_state", sa.String(length=16), nullable=False, server_default="unknown"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('development', 'staging', 'production')",
            name="ck_environments_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'archived')",
            name="ck_environments_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_environments_tenant_slug"),
        sa.UniqueConstraint("tenant_id", "kind", name="uq_environments_tenant_kind"),
        sa.UniqueConstraint("production_guard", name="uq_environments_production_guard"),
        sa.UniqueConstraint("default_guard", name="uq_environments_default_guard"),
    )
    op.create_index("ix_environments_tenant_id", "environments", ["tenant_id"])

    backfill_tenants(bind)

    op.create_foreign_key(
        "fk_tenants_organization_id",
        "tenants",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_tenants_organization_id", "tenants", ["organization_id"])

    if bind.dialect.name == "postgresql":
        op.alter_column("tenants", "organization_id", nullable=False)
        op.create_check_constraint(
            "ck_tenants_lifecycle_status",
            "tenants",
            "lifecycle_status IN ('active', 'suspended', 'read_only', 'deleted')",
        )
    else:
        with op.batch_alter_table("tenants") as batch:
            batch.alter_column("organization_id", existing_type=sa.Uuid(), nullable=False)
            batch.create_check_constraint(
                "ck_tenants_lifecycle_status",
                "lifecycle_status IN ('active', 'suspended', 'read_only', 'deleted')",
            )


def downgrade() -> None:
    # Enum labels stay. PostgreSQL cannot drop a value, and an audit row that
    # already used one must keep resolving. Business rows are not deleted.
    bind = op.get_bind()
    op.drop_index("ix_environments_tenant_id", table_name="environments")
    op.drop_table("environments")
    op.drop_index("ix_tenants_organization_id", table_name="tenants")
    if bind.dialect.name == "postgresql":
        op.drop_constraint("fk_tenants_organization_id", "tenants", type_="foreignkey")
        op.drop_constraint("ck_tenants_lifecycle_status", "tenants", type_="check")
        op.drop_column("tenants", "organization_id")
        op.drop_column("tenants", "lifecycle_status")
    else:
        with op.batch_alter_table("tenants") as batch:
            batch.drop_constraint("fk_tenants_organization_id", type_="foreignkey")
            batch.drop_constraint("ck_tenants_lifecycle_status", type_="check")
            batch.drop_column("organization_id")
            batch.drop_column("lifecycle_status")
    op.drop_table("organizations")
