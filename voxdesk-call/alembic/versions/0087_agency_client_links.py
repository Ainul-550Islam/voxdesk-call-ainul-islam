"""Agency portfolio links (PART 16, P16-02).

Revision ID: 0087_agency_client_links
Revises: 0086_custom_roles
Create Date: 2026-11-11

The spec's PART 16 file tree has no migration for the agency flows, because it
assumed `app/organization/` could express a portfolio. It cannot: no column links
two organizations, and a membership row is pinned to the organization of the
user's own tenant. So one small table is added and the drift is stated here.

`client_organization_id` is unique: one client, one agency. That is a deliberate
constraint, not an oversight — see `app/db/agency_models.py`.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0087_agency_client_links"
down_revision = "0086_custom_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agency_client_links",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agency_organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "client_organization_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_by_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("client_organization_id", name="uq_agency_client_link_client"),
    )
    op.create_index(
        "ix_agency_client_link_agency",
        "agency_client_links",
        ["agency_organization_id", "created_at"],
    )
    op.create_index(
        "ix_agency_client_links_agency_organization_id",
        "agency_client_links",
        ["agency_organization_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agency_client_links_agency_organization_id", table_name="agency_client_links")
    op.drop_index("ix_agency_client_link_agency", table_name="agency_client_links")
    op.drop_table("agency_client_links")
