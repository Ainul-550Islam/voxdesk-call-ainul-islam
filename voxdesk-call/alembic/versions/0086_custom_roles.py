"""Tenant-defined roles and their grants (PART 16, P16-01).

Revision ID: 0086_custom_roles
Revises: 0085_call_saved_views
Create Date: 2026-11-11

The spec's PART 16 asks for custom roles at migration `0080`, but `0080` was
already taken by the alert-rules migration in PART 15 (the repository's rule is
to append, never to renumber a migration that has run somewhere). This is
`0086`; the inventory generator resolves spec paths, so the number drifting does
not hide the file.

Two tables:

* `custom_roles` — the role. `permissions` is a JSON list of permission **ids**
  from `app.auth.permissions`; there is no second vocabulary and no free text.
* `custom_role_assignments` — the grant, unique on
  `(tenant_id, role_id, subject_type, subject_id)`. A duplicate grant is refused
  by the database rather than by a `SELECT` that can race, and the subject kind
  is pinned by a check constraint so a hand-written INSERT cannot invent one.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0086_custom_roles"
down_revision = "0085_call_saved_views"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "custom_roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("permissions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_custom_roles_tenant_name"),
    )
    op.create_index("ix_custom_roles_tenant_id", "custom_roles", ["tenant_id"])
    op.create_index("ix_custom_roles_tenant_active", "custom_roles", ["tenant_id", "is_active"])

    op.create_table(
        "custom_role_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_type", sa.String(24), nullable=False),
        sa.Column("subject_id", sa.String(120), nullable=False),
        sa.Column(
            "assigned_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "tenant_id", "role_id", "subject_type", "subject_id",
            name="uq_custom_role_assignment",
        ),
        sa.CheckConstraint(
            "subject_type IN ('user', 'service_account', 'scim_group')",
            name="ck_custom_role_assignment_subject",
        ),
    )
    op.create_index("ix_custom_role_assignments_tenant_id", "custom_role_assignments", ["tenant_id"])
    op.create_index("ix_custom_role_assignments_role_id", "custom_role_assignments", ["role_id"])
    op.create_index(
        "ix_custom_role_assignments_subject",
        "custom_role_assignments",
        ["tenant_id", "subject_type", "subject_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_custom_role_assignments_subject", table_name="custom_role_assignments")
    op.drop_index("ix_custom_role_assignments_role_id", table_name="custom_role_assignments")
    op.drop_index("ix_custom_role_assignments_tenant_id", table_name="custom_role_assignments")
    op.drop_table("custom_role_assignments")
    op.drop_index("ix_custom_roles_tenant_active", table_name="custom_roles")
    op.drop_index("ix_custom_roles_tenant_id", table_name="custom_roles")
    op.drop_table("custom_roles")
