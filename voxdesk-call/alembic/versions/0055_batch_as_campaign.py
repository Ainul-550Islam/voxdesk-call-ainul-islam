"""Bridge BatchCall/BatchRecipient to Campaign/Lead and normalize DNC phones (Part 1B / Gate G1)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.telephony import phone as phone_util

revision = "0055_batch_as_campaign"
down_revision = "0054_qa_runtime"
branch_labels = None
depends_on = None


def _has_column(inspector: sa.Inspector, table: str, column: str) -> bool:
    if not inspector.has_table(table):
        return False
    return any(col["name"] == column for col in inspector.get_columns(table))


def _has_index(inspector: sa.Inspector, table: str, index_name: str) -> bool:
    if not inspector.has_table(table):
        return False
    return any(ix["name"] == index_name for ix in inspector.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'PII_REDACTION_DISABLED'")

    if inspector.has_table("batch_recipients"):
        if not _has_column(inspector, "batch_recipients", "campaign_id"):
            op.add_column(
                "batch_recipients",
                sa.Column("campaign_id", sa.Uuid(), nullable=True),
            )
            if bind.dialect.name == "postgresql":
                op.create_foreign_key(
                    "fk_batch_recipients_campaign_id",
                    "batch_recipients",
                    "campaigns",
                    ["campaign_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
        if not _has_column(inspector, "batch_recipients", "lead_id"):
            op.add_column(
                "batch_recipients",
                sa.Column("lead_id", sa.Uuid(), nullable=True),
            )
            if bind.dialect.name == "postgresql":
                op.create_foreign_key(
                    "fk_batch_recipients_lead_id",
                    "batch_recipients",
                    "leads",
                    ["lead_id"],
                    ["id"],
                    ondelete="SET NULL",
                )

    if inspector.has_table("campaigns") and not _has_column(inspector, "campaigns", "batch_call_id"):
        op.add_column(
            "campaigns",
            sa.Column("batch_call_id", sa.Uuid(), nullable=True),
        )
        if bind.dialect.name == "postgresql" and inspector.has_table("batch_calls"):
            op.create_foreign_key(
                "fk_campaigns_batch_call_id",
                "campaigns",
                "batch_calls",
                ["batch_call_id"],
                ["id"],
                ondelete="SET NULL",
            )

    if inspector.has_table("dnc_entries"):
        rows = bind.execute(
            sa.text("SELECT id, tenant_id, phone FROM dnc_entries ORDER BY created_at ASC")
        ).fetchall()
        seen: set[tuple[str, str]] = set()
        for row_id, tenant_id, raw_phone in rows:
            try:
                norm = phone_util.normalize(raw_phone or "")
            except phone_util.InvalidPhoneNumber:
                norm = (raw_phone or "").strip()
            key = (str(tenant_id), norm)
            if key in seen:
                bind.execute(
                    sa.text("DELETE FROM dnc_entries WHERE id = :id"),
                    {"id": row_id},
                )
            else:
                seen.add(key)
                if norm != raw_phone:
                    bind.execute(
                        sa.text("UPDATE dnc_entries SET phone = :phone WHERE id = :id"),
                        {"phone": norm, "id": row_id},
                    )
        if not _has_index(inspector, "dnc_entries", "ix_dnc_entries_tenant_phone"):
            op.create_index(
                "ix_dnc_entries_tenant_phone",
                "dnc_entries",
                ["tenant_id", "phone"],
                unique=True,
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_index(inspector, "dnc_entries", "ix_dnc_entries_tenant_phone"):
        op.drop_index("ix_dnc_entries_tenant_phone", table_name="dnc_entries")

    if _has_column(inspector, "campaigns", "batch_call_id"):
        with op.batch_alter_table("campaigns") as batch_op:
            if _has_index(inspector, "campaigns", "ix_campaigns_batch_call_id"):
                batch_op.drop_index("ix_campaigns_batch_call_id")
            if bind.dialect.name == "postgresql":
                batch_op.drop_constraint("fk_campaigns_batch_call_id", type_="foreignkey")
            batch_op.drop_column("batch_call_id")

    if inspector.has_table("batch_recipients"):
        with op.batch_alter_table("batch_recipients") as batch_op:
            if _has_column(inspector, "batch_recipients", "lead_id"):
                if _has_index(inspector, "batch_recipients", "ix_batch_recipients_lead_id"):
                    batch_op.drop_index("ix_batch_recipients_lead_id")
                if bind.dialect.name == "postgresql":
                    batch_op.drop_constraint("fk_batch_recipients_lead_id", type_="foreignkey")
                batch_op.drop_column("lead_id")
            if _has_column(inspector, "batch_recipients", "campaign_id"):
                if _has_index(inspector, "batch_recipients", "ix_batch_recipients_campaign_id"):
                    batch_op.drop_index("ix_batch_recipients_campaign_id")
                if bind.dialect.name == "postgresql":
                    batch_op.drop_constraint("fk_batch_recipients_campaign_id", type_="foreignkey")
                batch_op.drop_column("campaign_id")
