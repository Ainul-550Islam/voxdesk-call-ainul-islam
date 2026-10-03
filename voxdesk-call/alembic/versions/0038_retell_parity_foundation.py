"""Retell parity foundation — contacts, contact memory, chat agents + versions, dynamic variables, agent transfers + events

Revision ID: 0038_retell_parity_foundation
Revises: 0037_enterprise_missing_apis
Create Date: 2026-10-03

Seven new tables, all tenant-scoped, all with explicit indexes. Nothing
existing is altered: this revision only creates the Retell-parity surfaces
(Prompt 1). Status columns are VARCHAR on purpose -- the Python enums in
`app/db/retell_models.py` are the single source of truth, matching the
`backfill_jobs` precedent from revision 0037, so adding a state later is a
code change instead of an `ALTER TYPE`.

Downgrade drops the seven tables in reverse dependency order.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "0038_retell_parity_foundation"
down_revision = "0037_enterprise_missing_apis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------- contacts
    op.create_table(
        "contacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "environment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("environments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("phone_raw", sa.String(32), nullable=False, server_default=""),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("company", sa.String(200), nullable=True),
        sa.Column("custom_fields", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("source", sa.String(24), nullable=False, server_default="manual"),
        sa.Column("lifecycle", sa.String(16), nullable=False, server_default="active"),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("crm_provider", sa.String(40), nullable=True),
        sa.Column("crm_external_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_contacts_tenant_id", "contacts", ["tenant_id"])
    op.create_unique_constraint("uq_contacts_tenant_phone", "contacts", ["tenant_id", "phone"])
    op.create_index("ix_contacts_tenant_lifecycle", "contacts", ["tenant_id", "lifecycle"])
    op.create_index("ix_contacts_tenant_name", "contacts", ["tenant_id", "name"])
    op.create_index("ix_contacts_tenant_created", "contacts", ["tenant_id", "created_at"])
    op.create_index("ix_contacts_crm_ref", "contacts", ["tenant_id", "crm_provider", "crm_external_id"])

    # ------------------------------------------------------- contact memory
    op.create_table(
        "contact_memory_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("value", sa.Text, nullable=False, server_default=""),
        sa.Column("value_type", sa.String(16), nullable=False, server_default="string"),
        sa.Column("source", sa.String(24), nullable=False, server_default="manual"),
        sa.Column("source_ref", sa.String(255), nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("importance", sa.Integer, nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_contact_memory_entries_contact_id", "contact_memory_entries", ["contact_id"])
    op.create_unique_constraint(
        "uq_contact_memory_key", "contact_memory_entries", ["tenant_id", "contact_id", "key"]
    )
    op.create_index(
        "ix_contact_memory_contact",
        "contact_memory_entries",
        ["tenant_id", "contact_id", "importance", "updated_at"],
    )
    op.create_index("ix_contact_memory_expiry", "contact_memory_entries", ["tenant_id", "expires_at"])

    # ----------------------------------------------------------- chat agents
    op.create_table(
        "chat_agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("draft_config", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("published_config", sa.JSON, nullable=True),
        sa.Column("draft_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column("published_version", sa.Integer, nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_chat_agents_tenant_id", "chat_agents", ["tenant_id"])
    op.create_unique_constraint("uq_chat_agents_tenant_name", "chat_agents", ["tenant_id", "name"])
    op.create_index("ix_chat_agents_tenant_status", "chat_agents", ["tenant_id", "status"])
    op.create_index("ix_chat_agents_tenant_created", "chat_agents", ["tenant_id", "created_at"])

    op.create_table(
        "chat_agent_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chat_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="published"),
        sa.Column("config", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("change_summary", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_chat_agent_versions_chat_agent_id", "chat_agent_versions", ["chat_agent_id"])
    op.create_unique_constraint("uq_chat_agent_version", "chat_agent_versions", ["chat_agent_id", "version"])
    op.create_index(
        "ix_chat_agent_versions_tenant", "chat_agent_versions", ["tenant_id", "chat_agent_id", "version"]
    )

    # ----------------------------------------------------- dynamic variables
    op.create_table(
        "dynamic_variable_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chat_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_agents.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("var_type", sa.String(16), nullable=False),
        sa.Column("description", sa.String(300), nullable=False, server_default=""),
        sa.Column("default_value", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("constraints", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("is_required", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_nullable", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dynamic_variable_definitions_tenant_id", "dynamic_variable_definitions", ["tenant_id"])
    op.create_index("ix_dynamic_variable_definitions_chat_agent_id", "dynamic_variable_definitions", ["chat_agent_id"])
    op.create_unique_constraint(
        "uq_dynamic_variable_scope_name",
        "dynamic_variable_definitions",
        ["tenant_id", "chat_agent_id", "name"],
    )
    op.create_index(
        "ix_dynamic_variables_tenant", "dynamic_variable_definitions", ["tenant_id", "chat_agent_id"]
    )

    # ------------------------------------------------------- agent transfers
    op.create_table(
        "agent_transfers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "call_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calls.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "contact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("mode", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("from_agent_ref", sa.String(120), nullable=False, server_default=""),
        sa.Column("to_agent_ref", sa.String(120), nullable=False, server_default=""),
        sa.Column(
            "to_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("destination", sa.String(120), nullable=False, server_default=""),
        sa.Column("reason", sa.String(400), nullable=False, server_default=""),
        sa.Column("failure_reason", sa.String(300), nullable=True),
        sa.Column("context_snapshot", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("idempotency_key", sa.String(128), nullable=False, server_default=""),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_transfers_tenant_id", "agent_transfers", ["tenant_id"])
    op.create_unique_constraint(
        "uq_agent_transfer_idempotency", "agent_transfers", ["tenant_id", "idempotency_key"]
    )
    op.create_index("ix_agent_transfers_tenant_status", "agent_transfers", ["tenant_id", "status"])
    op.create_index("ix_agent_transfers_call", "agent_transfers", ["tenant_id", "call_id"])
    op.create_index("ix_agent_transfers_contact", "agent_transfers", ["tenant_id", "contact_id"])

    op.create_table(
        "agent_transfer_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "transfer_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_transfers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event", sa.String(48), nullable=False),
        sa.Column("detail", sa.JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_transfer_events_transfer_id", "agent_transfer_events", ["transfer_id"])
    op.create_index("ix_agent_transfer_events_transfer", "agent_transfer_events", ["transfer_id", "created_at"])


def downgrade() -> None:
    op.drop_table("agent_transfer_events")
    op.drop_table("agent_transfers")
    op.drop_table("dynamic_variable_definitions")
    op.drop_table("chat_agent_versions")
    op.drop_table("chat_agents")
    op.drop_table("contact_memory_entries")
    op.drop_table("contacts")
