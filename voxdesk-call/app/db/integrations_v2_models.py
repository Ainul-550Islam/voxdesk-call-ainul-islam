"""SQLAlchemy 2.0 ORM models for Enterprise Helpdesk, CRM v2, Collaboration, Sync, and Agent Tools (Batch 05).

All tables register onto ``app.db.models.Base`` so ``alembic/env.py`` and
test schema bootstrap discover them deterministically. Every tenant-owned row
carries ``tenant_id`` (non-nullable FK to ``tenants.id`` with ``CASCADE``) and
optional ``environment_id`` scoping. Raw OAuth tokens, API keys, and webhook
signing secrets are never stored in plaintext columns.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IntegrationConnection(Base):
    """Per-tenant, per-environment connection to an external Helpdesk, CRM v2, or Collaboration provider."""

    __tablename__ = "int2_connections"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "external_account_id",
            name="uq_int2_conn_tenant_provider_ext",
        ),
        CheckConstraint(
            "status IN ('pending','active','expired','revoked','needs_reauth','disabled','error')",
            name="ck_int2_conn_status",
        ),
        CheckConstraint(
            "category IN ('helpdesk','crm','collab')",
            name="ck_int2_conn_category",
        ),
        Index("ix_int2_conn_tenant_env", "tenant_id", "environment_id"),
        Index("ix_int2_conn_tenant_category_status", "tenant_id", "category", "status"),
        Index("ix_int2_conn_token_expires", "status", "token_expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    provider: Mapped[str] = mapped_column(String(48), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    auth_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    external_account_id: Mapped[str] = mapped_column(
        String(160), nullable=False, default="default"
    )
    instance_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    region: Mapped[str] = mapped_column(String(32), nullable=False, default="us")
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    token_secret_ref: Mapped[str] = mapped_column(Text, nullable=False, default="")
    refresh_secret_ref: Mapped[str] = mapped_column(Text, nullable=False, default="")
    webhook_secret_ref: Mapped[str] = mapped_column(Text, nullable=False, default="")
    encryption_key_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_health_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_health_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_error: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class OAuthState(Base):
    """Single-use CSRF + PKCE state nonce for OAuth2 authorization flows."""

    __tablename__ = "int2_oauth_states"
    __table_args__ = (
        UniqueConstraint("state_hash", name="uq_int2_oauth_state_hash"),
        Index("ix_int2_oauth_state_tenant_exp", "tenant_id", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(48), nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    pkce_verifier_secret_ref: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(512), nullable=False)
    extra_params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


OAuthStateNonce = OAuthState


class FieldMappingProfile(Base):
    """Named per-tenant declarative field-mapping profile for an integration connection."""

    __tablename__ = "int2_field_mapping_profiles"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "entity_type", "name", name="uq_int2_mapping_profile_name"
        ),
        Index("ix_int2_mapping_profile_tenant_env", "tenant_id", "environment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("int2_connections.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="default")
    rules_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class FieldMappingRule(Base):
    """Per-connection field mapping and transform specification."""

    __tablename__ = "int2_field_mappings"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "entity_type",
            "voxdesk_field",
            "external_field",
            name="uq_int2_field_map_rule",
        ),
        CheckConstraint(
            "direction IN ('inbound','outbound','bidirectional')",
            name="ck_int2_field_map_direction",
        ),
        Index("ix_int2_field_map_tenant_conn", "tenant_id", "connection_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("int2_connections.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    direction: Mapped[str] = mapped_column(
        String(16), nullable=False, default="bidirectional"
    )
    voxdesk_field: Mapped[str] = mapped_column(String(120), nullable=False)
    external_field: Mapped[str] = mapped_column(String(160), nullable=False)
    transform: Mapped[str] = mapped_column(String(32), nullable=False, default="identity")
    transform_config: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_value: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class SyncCursor(Base):
    """Per-connection, per-entity incremental sync watermark checkpoint."""

    __tablename__ = "int2_sync_cursors"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "entity_type", name="uq_int2_sync_cursor_conn_entity"
        ),
        Index("ix_int2_sync_cursor_tenant", "tenant_id", "connection_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("int2_connections.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    cursor_value: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    cursor_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="updated_since"
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class SyncRun(Base):
    """Execution record for a manual or scheduled incremental sync job."""

    __tablename__ = "int2_sync_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running','succeeded','partial','failed')",
            name="ck_int2_sync_runs_status",
        ),
        Index("ix_int2_sync_runs_tenant_env", "tenant_id", "environment_id"),
        Index(
            "ix_int2_sync_runs_tenant_conn_started",
            "tenant_id",
            "connection_id",
            "started_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("int2_connections.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, default="inbound")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="running")
    cursor_before: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    cursor_after: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    records_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dead_letters: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    error_summary: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ExternalObjectLink(Base):
    """Bidirectional identity mapping between a VoxDesk entity and a vendor record."""

    __tablename__ = "int2_external_links"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "entity_type",
            "external_id",
            name="uq_int2_ext_link_external",
        ),
        Index(
            "ix_int2_ext_link_voxdesk",
            "tenant_id",
            "connection_id",
            "entity_type",
            "voxdesk_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("int2_connections.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    voxdesk_id: Mapped[str] = mapped_column(String(120), nullable=False)
    external_id: Mapped[str] = mapped_column(String(160), nullable=False)
    external_etag: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    external_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


ExternalIdentityLink = ExternalObjectLink


class WritebackLedger(Base):
    """Idempotent post-call writeback ledger keyed by (call_id, provider, entity)."""

    __tablename__ = "int2_writeback_ledger"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_int2_writeback_tenant_idem"
        ),
        UniqueConstraint(
            "call_id", "provider", "entity", name="uq_int2_ledger_call_provider_entity"
        ),
        CheckConstraint(
            "status IN ('pending','succeeded','failed','dead_letter')",
            name="ck_int2_writeback_status",
        ),
        Index("ix_int2_writeback_call", "tenant_id", "call_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("int2_connections.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(48), nullable=False, default="unknown")
    entity: Mapped[str] = mapped_column(String(64), nullable=False, default="call_log")
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    external_id: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    outbox_event_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class InboundWebhookEvent(Base):
    """Deduplicated inbound webhook receipt log with cryptographic nonce replay protection."""

    __tablename__ = "int2_webhook_events"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "external_event_id", name="uq_int2_wh_conn_event"
        ),
        UniqueConstraint("nonce", name="uq_int2_wh_nonce"),
        CheckConstraint(
            "status IN ('queued','processed','failed','dead_letter','duplicate')",
            name="ck_int2_wh_status",
        ),
        Index("ix_int2_wh_tenant_status", "tenant_id", "status", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("int2_connections.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(48), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(180), nullable=False)
    nonce: Mapped[str] = mapped_column(
        String(192), nullable=False, default=lambda: uuid.uuid4().hex
    )
    event_type: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    signature_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_excerpt: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class NotificationRule(Base):
    """Per-tenant event-to-channel notification rule for Slack and Microsoft Teams."""

    __tablename__ = "int2_collab_routes"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "event_type",
            "channel_id",
            name="uq_int2_collab_route_target",
        ),
        Index("ix_int2_collab_route_tenant_env", "tenant_id", "environment_id"),
        Index("ix_int2_collab_route_tenant_event", "tenant_id", "event_type", "enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("int2_connections.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    team_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    channel_id: Mapped[str] = mapped_column(String(160), nullable=False)
    channel_name: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    template: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    rate_limit_per_min: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    dedupe_window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    filter_config: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


CollabChannelRoute = NotificationRule


class MeetingRecord(Base):
    """Idempotent record of a scheduled Zoom or Microsoft Teams meeting."""

    __tablename__ = "int2_meetings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_int2_meeting_tenant_idem"
        ),
        Index("ix_int2_meetings_tenant_env", "tenant_id", "environment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("int2_connections.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(48), nullable=False)
    lead_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    call_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    external_meeting_id: Mapped[str] = mapped_column(String(160), nullable=False)
    topic: Mapped[str] = mapped_column(String(240), nullable=False)
    join_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="scheduled")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class ToolEnablement(Base):
    """Per-tenant, per-agent mid-call integration tool enablement and safety ceilings."""

    __tablename__ = "int2_tool_enablements"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "agent_id",
            "tool_name",
            name="uq_int2_tool_enablement_agent_tool",
        ),
        Index("ix_int2_tool_enablement_tenant", "tenant_id", "enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("int2_connections.id", ondelete="CASCADE"), nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    max_calls_per_conversation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3
    )
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=3000)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
