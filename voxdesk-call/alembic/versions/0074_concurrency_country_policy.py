"""Concurrency entitlement and country policy (PART 13, P13-05/06).

Revision ID: 0074_concurrency_country_policy
Revises: 0073_agent_folders_drafts
Create Date: 2026-11-10

Three tables and one trunk table:

* `telephony_concurrency_limits` -- one row per tenant, holding the paid base,
  the inbound reserve and the burst billing switch. The burst ceiling is not a
  column: it is `min(3*base, base+300)` computed in
  `app.telephony.concurrency_policy`, so a database edit cannot invent a
  capacity the billing system has never seen.
* `telephony_concurrency_samples` -- the 24 h series, one row per minute per
  tenant. Unique on `(tenant_id, bucket_start)` so a sampler restart overwrites
  instead of double counting.
* `telephony_country_policy` -- allow/deny rules scoped to the tenant, a number
  or an agent. Unique per scope so "allow BD twice" is impossible.
* `sip_trunks` -- the carrier-facing trunk PART 13 advertises. Credentials are
  a reference into the secret store; there is no password column to leak.

Every new tenant gets a limit row lazily (the API creates one on first read), so
no backfill is required and a tenant that never makes a call never gets a row.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Uuid as UUID

revision = "0074_concurrency_country_policy"
down_revision = "0073_agent_folders_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sip_trunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "environment_id",
            UUID(as_uuid=True),
            sa.ForeignKey("environments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(120), nullable=False, server_default="Primary trunk"),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="5061"),
        sa.Column("transport", sa.String(8), nullable=False, server_default="tls"),
        sa.Column("srtp_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("auth_username", sa.String(128), nullable=True),
        sa.Column("auth_secret_ref", sa.String(255), nullable=True),
        sa.Column("caller_id", sa.String(32), nullable=True),
        sa.Column("egress_address", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="NOT_CONFIGURED"),
        sa.Column("last_test_at", sa.DateTime(), nullable=True),
        sa.Column("last_test_ms", sa.Float(), nullable=True),
        sa.Column("last_error", sa.String(400), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "name", name="uq_sip_trunks_tenant_name"),
    )
    op.create_index("ix_sip_trunks_tenant_status", "sip_trunks", ["tenant_id", "status"])

    op.create_table(
        "telephony_concurrency_limits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("base", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("reserved_inbound", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("burst_billing", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("provider_cps", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("base >= 1", name="ck_concurrency_base_positive"),
        sa.CheckConstraint(
            "reserved_inbound >= 0 AND reserved_inbound < base",
            name="ck_concurrency_reserve_leaves_room",
        ),
    )

    op.create_table(
        "telephony_concurrency_samples",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("captured_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("bucket_start", sa.DateTime(), nullable=False),
        sa.Column("inbound", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outbound", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("peak", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint(
            "tenant_id", "bucket_start", name="uq_concurrency_sample_bucket"
        ),
    )
    op.create_index(
        "ix_concurrency_samples_tenant_bucket",
        "telephony_concurrency_samples",
        ["tenant_id", "bucket_start"],
    )

    op.create_table(
        "telephony_country_policy",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(16), nullable=False, server_default="tenant"),
        sa.Column("scope_id", sa.String(64), nullable=True),
        sa.Column("country", sa.String(2), nullable=False),
        sa.Column("action", sa.String(8), nullable=False, server_default="allow"),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "tenant_id", "scope", "scope_id", "country", name="uq_country_policy_scope"
        ),
        sa.CheckConstraint("action IN ('allow','deny')", name="ck_country_policy_action"),
        sa.CheckConstraint(
            "scope IN ('tenant','number','agent')", name="ck_country_policy_scope"
        ),
    )
    op.create_index("ix_country_policy_tenant", "telephony_country_policy", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_country_policy_tenant", table_name="telephony_country_policy")
    op.drop_table("telephony_country_policy")
    op.drop_index(
        "ix_concurrency_samples_tenant_bucket", table_name="telephony_concurrency_samples"
    )
    op.drop_table("telephony_concurrency_samples")
    op.drop_table("telephony_concurrency_limits")
    op.drop_index("ix_sip_trunks_tenant_status", table_name="sip_trunks")
    op.drop_table("sip_trunks")
