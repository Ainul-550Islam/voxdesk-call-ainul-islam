"""campaign environment scope

Binds every campaign to the environment it operates in. ``campaigns`` kept
only ``tenant_id`` while leads, calls and appointments gained
``environment_id`` in 0019 — which permitted a same-tenant ambiguity: a
production campaign could sit beside staging leads and (before this revision
and the application gates that ship with it) target them.

Adds ``campaigns.environment_id`` with the same shape the other
environment-scoped tables use: a single-column FK with ``ON DELETE
RESTRICT``, the composite ``(tenant_id, environment_id)`` FK against
``uq_environments_tenant_identity`` so a campaign can never point at another
tenant's environment, and the two scope indexes.

Backfill rule (deterministic, safe, never invents an environment): each
existing campaign is bound to its own tenant's *active* production
environment, preferring the default row — the same
``kind = 'production' ORDER BY is_default DESC LIMIT 1`` shape revision 0019
established, narrowed to ``status = 'active'`` because a campaign is an
operational resource and a suspended/archived production environment must
not receive new bindings. Rows whose tenant has no active production
environment are left unbound and ``NOT NULL`` is *not* enforced in that
case — the repository's explicitly defined safe behaviour from 0019 (fail
visible, never fabricate) rather than an invented environment. When every
row is bound, ``NOT NULL`` is enforced after the backfill.

Revision ID: 0026_campaign_environment_scope
Revises: 0025_enterprise_leads
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026_campaign_environment_scope"
down_revision = "0025_enterprise_leads"
branch_labels = None
depends_on = None


def backfill_campaign_environments(connection) -> int:
    """Bind unbound campaigns to their own tenant's active production environment.

    Deterministic and idempotent: a second call updates nothing. Never copies
    a campaign onto another tenant's environment and never creates an
    environment row.
    """
    result = connection.execute(sa.text(
        "UPDATE campaigns SET environment_id = ("
        "SELECT e.id FROM environments e "
        "WHERE e.tenant_id = campaigns.tenant_id AND e.kind = 'production' "
        "AND e.status = 'active' "
        "ORDER BY e.is_default DESC LIMIT 1) "
        "WHERE environment_id IS NULL "
        "AND EXISTS ("
        "SELECT 1 FROM environments e "
        "WHERE e.tenant_id = campaigns.tenant_id AND e.kind = 'production' "
        "AND e.status = 'active')"
    ))
    return int(result.rowcount or 0)


def unbound_campaign_count(connection) -> int:
    """Campaigns still without an environment after the backfill attempt."""
    return int(connection.execute(sa.text(
        "SELECT COUNT(*) FROM campaigns WHERE environment_id IS NULL"
    )).scalar_one())


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("campaigns", sa.Column("environment_id", sa.Uuid(), nullable=True))

    backfill_campaign_environments(bind)
    remaining = unbound_campaign_count(bind)
    if remaining:
        # Fail loudly, never fabricate (0019's rule): the column stays
        # nullable, the unbound rows stay visible, and the deploy log says
        # exactly what an operator must fix before tightening is possible.
        print(
            f"0026_campaign_environment_scope: WARNING — {remaining} campaign(s) "
            "could not be bound because their tenant has no ACTIVE production "
            "environment. campaigns.environment_id stays NULLABLE; restore or "
            "designate an active production environment for those tenants, "
            "bind the remaining rows, then enforce NOT NULL.",
            flush=True,
        )

    op.create_index("ix_campaigns_environment_id", "campaigns", ["environment_id"])
    op.create_index(
        "ix_campaigns_tenant_environment", "campaigns", ["tenant_id", "environment_id"]
    )
    op.create_foreign_key(
        "fk_campaigns_environment",
        "campaigns", "environments",
        ["environment_id"], ["id"],
        ondelete="RESTRICT",
    )
    if bind.dialect.name == "postgresql":
        # The composite FK is what makes a cross-tenant environment pointer
        # impossible at the database level. SQLite cannot gain constraints
        # via ALTER TABLE; there the application hooks and the ORM-level
        # constraint (declared on the model) are the enforcement.
        op.create_foreign_key(
            "fk_campaigns_tenant_environment",
            "campaigns", "environments",
            ["tenant_id", "environment_id"], ["tenant_id", "id"],
        )
    if remaining == 0:
        # Every campaign could be bound safely, so the schema becomes strict.
        # When a tenant had no active production environment the column stays
        # nullable and the unbound rows stay visible — a loud, inspectable
        # state instead of a fabricated binding (0019's rule).
        op.alter_column("campaigns", "environment_id", nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_constraint(
            "fk_campaigns_tenant_environment", "campaigns", type_="foreignkey"
        )
    op.drop_constraint("fk_campaigns_environment", "campaigns", type_="foreignkey")
    op.drop_index("ix_campaigns_tenant_environment", table_name="campaigns")
    op.drop_index("ix_campaigns_environment_id", table_name="campaigns")
    op.drop_column("campaigns", "environment_id")
