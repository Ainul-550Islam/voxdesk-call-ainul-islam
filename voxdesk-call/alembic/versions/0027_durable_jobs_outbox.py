"""durable jobs outbox

Batch 07 (durable async jobs + transactional outbox) schema.

Inspection first, per the batch contract — what already exists and is
*reused unchanged* by this revision:

* ``jobs`` (revision 0020) is the one job source-of-truth: tenant/
  environment scope, payload, status, attempt_count/max_attempts,
  replay_count, available_at, leased_until + worker_id (the lease),
  last_error_category/last_error, idempotency_key with the tenant-unique
  constraint, created/started/completed timestamps, and the claim index
  ``(status, available_at)``. **No second job table is created.**
* ``job_attempts`` (0020) already stores per-attempt provenance including
  the ``expired`` state the reaper writes — reused as-is.
* ``job_idempotency`` (0020) is the existing side-effect ledger — reused
  as-is; no second idempotency ledger is created.
* ``webhook_subscriptions`` / ``webhook_deliveries`` (0020) are the existing
  outbound endpoint + per-subscription delivery ledger the outbox dispatcher
  delivers through — reused as-is.

What genuinely did not exist, and is therefore added here:

1. ``jobs.priority`` (Integer, NOT NULL, server_default '1' = normal):
   claim ordering (0=high, 1=normal, 2=low). Existing rows backfill to
   normal, so pre-Batch-07 claim order (``available_at``) is preserved —
   with every row equal the new ordering term is a no-op.
2. ``jobs.cancel_requested`` (Boolean, NOT NULL, server_default false):
   durable cancellation intent for running jobs. Existing rows backfill to
   false, so nothing becomes unclaimable.
3. Four ``jobs`` indexes the Batch 07 access paths need: the priority-aware
   claim ``(status, priority, available_at)``, the reaper's
   ``(leased_until)``, and the operator/concurrency composites
   ``(tenant_id, status)`` and ``(tenant_id, environment_id, status)``.
   The idempotency-key index requirement is already met by the unique
   constraint ``uq_jobs_tenant_idempotency`` (a PG unique constraint is an
   index), so no duplicate is added.
4. ``outbox_events``: the transactional-outbox event record — nothing
   existing stores generic business events written inside a business
   transaction (``webhook_deliveries`` is the per-endpoint delivery ledger,
   which stays exactly that). Tenant-scoped with the tenant-unique
   idempotency key that makes duplicate publication race-safe, the claim
   index ``(status, available_at)`` the dispatcher scans, and the
   environment composites for scoped delivery and inspection.

No status CHECK constraints are added: ``jobs.status`` has always been an
application-enumerated string (``app.jobs.models.JobState``), and Batch 07's
new value (``quarantined``) joins it without DDL — consistent with how
``dead_letter`` was introduced in 0020.

PostgreSQL is the design target (the claim path uses ``FOR UPDATE SKIP
LOCKED`` there); every statement here is plain transactional DDL that
PostgreSQL applies atomically. Guards make re-application a no-op where the
dialect's inspector can see the object already exists.

Revision ID: 0027_durable_jobs_outbox
Revises: 0026_campaign_environment_scope
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027_durable_jobs_outbox"
down_revision = "0026_campaign_environment_scope"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(inspector, table: str) -> set[str]:
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    job_columns = _columns(inspector, "jobs")
    if "priority" not in job_columns:
        op.add_column(
            "jobs",
            sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
        )
    if "cancel_requested" not in job_columns:
        op.add_column(
            "jobs",
            sa.Column(
                "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
        )

    job_indexes = _indexes(inspector, "jobs")
    if "ix_jobs_claim_priority" not in job_indexes:
        op.create_index("ix_jobs_claim_priority", "jobs", ["status", "priority", "available_at"])
    if "ix_jobs_leased_until" not in job_indexes:
        op.create_index("ix_jobs_leased_until", "jobs", ["leased_until"])
    if "ix_jobs_tenant_status" not in job_indexes:
        op.create_index("ix_jobs_tenant_status", "jobs", ["tenant_id", "status"])
    if "ix_jobs_tenant_environment_status" not in job_indexes:
        op.create_index(
            "ix_jobs_tenant_environment_status", "jobs", ["tenant_id", "environment_id", "status"]
        )

    existing_tables = set(inspector.get_table_names())
    if "outbox_events" not in existing_tables:
        op.create_table(
            "outbox_events",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("tenant_id", sa.Uuid(), nullable=False),
            sa.Column("environment_id", sa.Uuid(), nullable=True),
            sa.Column("event_type", sa.String(128), nullable=False),
            sa.Column("event_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("aggregate_type", sa.String(64), nullable=False, server_default=""),
            sa.Column("aggregate_id", sa.String(128), nullable=False, server_default=""),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="8"),
            sa.Column("replay_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "last_error_category", sa.String(64), nullable=False, server_default=""
            ),
            sa.Column("last_error", sa.String(500), nullable=False, server_default=""),
            sa.Column("idempotency_key", sa.String(128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["environment_id"], ["environments.id"], ondelete="RESTRICT"
            ),
            sa.UniqueConstraint(
                "tenant_id", "idempotency_key", name="uq_outbox_tenant_idempotency"
            ),
        )
        op.create_index("ix_outbox_events_tenant_id", "outbox_events", ["tenant_id"])
        op.create_index("ix_outbox_claim", "outbox_events", ["status", "available_at"])
        op.create_index(
            "ix_outbox_tenant_environment", "outbox_events", ["tenant_id", "environment_id"]
        )
        op.create_index("ix_outbox_tenant_status", "outbox_events", ["tenant_id", "status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "outbox_events" in set(inspector.get_table_names()):
        outbox_indexes = _indexes(inspector, "outbox_events")
        for name in (
            "ix_outbox_tenant_status",
            "ix_outbox_tenant_environment",
            "ix_outbox_claim",
            "ix_outbox_events_tenant_id",
        ):
            if name in outbox_indexes:
                op.drop_index(name, table_name="outbox_events")
        op.drop_table("outbox_events")

    job_indexes = _indexes(inspector, "jobs")
    for name in (
        "ix_jobs_tenant_environment_status",
        "ix_jobs_tenant_status",
        "ix_jobs_leased_until",
        "ix_jobs_claim_priority",
    ):
        if name in job_indexes:
            op.drop_index(name, table_name="jobs")

    job_columns = _columns(inspector, "jobs")
    if "cancel_requested" in job_columns:
        op.drop_column("jobs", "cancel_requested")
    if "priority" in job_columns:
        op.drop_column("jobs", "priority")
