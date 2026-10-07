# File: app/db/enterprise_models.py — Enterprise extension models for missing APIs: batch calls, A/B testing, PCAP, retention, webhooks, Salesforce, knowledge base, simulation, tool registry, workflow triggers, multichannel, call policies
"""
Enterprise extension models for P0/P1 missing API closure.
All models are tenant-scoped, use UUID primary keys, and are registered on Base.metadata
so development/test create_all creates them. Production uses Alembic migrations.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import (
    Agent as Agent,
    AgentLifecycleStatus as AgentLifecycleStatus,
    AgentValidationStatus as AgentValidationStatus,
    AgentVersion as AgentVersion,
    AgentVersionStatus as AgentVersionStatus,
    Base,
)

def _uuid() -> uuid.UUID:
    return uuid.uuid4()

def _now() -> datetime:
    return datetime.now(timezone.utc)

# ---------------------------------------------------------------- Batch Calls
class BatchStatus(str, PyEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"

class BatchRecipientStatus(str, PyEnum):
    PENDING = "pending"
    QUEUED = "queued"
    DIALING = "dialing"
    COMPLETED = "completed"
    FAILED = "failed"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    VOICEMAIL = "voicemail"
    RETRY_SCHEDULED = "retry_scheduled"
    DNC_BLOCKED = "dnc_blocked"
    WINDOW_BLOCKED = "window_blocked"

class BatchCall(Base):
    __tablename__ = "batch_calls"
    __table_args__ = (
        Index("ix_batch_calls_tenant", "tenant_id"),
        Index("ix_batch_calls_status", "tenant_id", "status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    agent_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    campaign_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default=BatchStatus.DRAFT.value, nullable=False)
    concurrency: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    retry_delay_seconds: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)
    voicemail_action: Mapped[str] = mapped_column(String(32), default="hangup", nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    calling_window_start: Mapped[str] = mapped_column(String(8), default="09:00", nullable=False)
    calling_window_end: Mapped[str] = mapped_column(String(8), default="20:00", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    total_recipients: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_recipients: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_recipients: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "environment_id": str(self.environment_id) if self.environment_id else None,
            "name": self.name,
            "description": self.description,
            "agent_id": self.agent_id,
            "campaign_id": str(self.campaign_id) if self.campaign_id else None,
            "status": self.status,
            "concurrency": self.concurrency,
            "max_attempts": self.max_attempts,
            "retry_delay_seconds": self.retry_delay_seconds,
            "voicemail_action": self.voicemail_action,
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "calling_window": {"start": self.calling_window_start, "end": self.calling_window_end, "timezone": self.timezone},
            "total_recipients": self.total_recipients,
            "completed_recipients": self.completed_recipients,
            "failed_recipients": self.failed_recipients,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "meta": self.meta,
        }

class BatchRecipient(Base):
    __tablename__ = "batch_recipients"
    __table_args__ = (
        Index("ix_batch_recipients_batch", "batch_id"),
        Index("ix_batch_recipients_tenant", "tenant_id"),
        UniqueConstraint("batch_id", "phone", name="uq_batch_recipients_phone"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("batch_calls.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    custom_fields: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default=BatchRecipientStatus.PENDING.value, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "batch_id": str(self.batch_id),
            "tenant_id": str(self.tenant_id),
            "phone": self.phone,
            "name": self.name,
            "custom_fields": self.custom_fields,
            "status": self.status,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "next_attempt_at": self.next_attempt_at.isoformat() if self.next_attempt_at else None,
            "call_id": str(self.call_id) if self.call_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

# -------------------------------------------------------------- A/B Testing
class ExperimentStatus(str, PyEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"

class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (Index("ix_experiments_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default=ExperimentStatus.DRAFT.value, nullable=False)
    traffic_split: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    winner_variant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "traffic_split": self.traffic_split,
            "winner_variant_id": str(self.winner_variant_id) if self.winner_variant_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

class ExperimentVariant(Base):
    __tablename__ = "experiment_variants"
    __table_args__ = (Index("ix_exp_variants_exp", "experiment_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_control: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "experiment_id": str(self.experiment_id),
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "weight": self.weight,
            "config": self.config,
            "prompt": self.prompt,
            "is_control": self.is_control,
            "metrics": self.metrics,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

# -------------------------------------------------------------- PCAP / Debug
class PcapArtifact(Base):
    __tablename__ = "pcap_artifacts"
    __table_args__ = (Index("ix_pcap_tenant", "tenant_id"), Index("ix_pcap_call", "call_id"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), default="twilio", nullable=False)
    capture_type: Mapped[str] = mapped_column(String(32), default="sip", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    retention_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "call_id": str(self.call_id),
            "provider": self.provider,
            "capture_type": self.capture_type,
            "size_bytes": self.size_bytes,
            "has_storage": bool(self.storage_key),
            "checksum": self.checksum or None,
            "retention_deadline": self.retention_deadline.isoformat() if self.retention_deadline else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

# -------------------------------------------------------------- Retention
class RetentionPolicy(Base):
    __tablename__ = "retention_policies"
    __table_args__ = (
        Index("ix_retention_tenant", "tenant_id"),
        UniqueConstraint("tenant_id", "agent_id", "resource_type", name="uq_retention_agent_resource"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False, default="call")
    retention_days: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    purge_after_days: Mapped[int] = mapped_column(Integer, default=365, nullable=False)
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "agent_id": self.agent_id,
            "resource_type": self.resource_type,
            "retention_days": self.retention_days,
            "purge_after_days": self.purge_after_days,
            "legal_hold": self.legal_hold,
            "last_purge_at": self.last_purge_at.isoformat() if self.last_purge_at else None,
            "next_purge_at": self.next_purge_at.isoformat() if self.next_purge_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "meta": self.meta,
        }

# -------------------------------------------------------------- Webhooks


# -------------------------------------------------------------- Salesforce / CRM
class SalesforceConnection(Base):
    __tablename__ = "salesforce_connections"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_salesforce_tenant"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    instance_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    access_token_encrypted: Mapped[str] = mapped_column(Text, default="", nullable=False)
    refresh_token_encrypted: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "instance_url": self.instance_url,
            "is_active": self.is_active,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "meta": self.meta,
        }

class CrmWritebackLog(Base):
    __tablename__ = "crm_writeback_logs"
    __table_args__ = (Index("ix_crm_writeback_tenant", "tenant_id"), Index("ix_crm_writeback_call", "call_id"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "call_id": str(self.call_id),
            "provider": self.provider,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "action": self.action,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

# -------------------------------------------------------------- Knowledge Base Collections
class KnowledgeCollection(Base):
    __tablename__ = "knowledge_collections"
    __table_args__ = (Index("ix_kb_collections_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    agent_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_status: Mapped[str] = mapped_column(String(24), default="idle", nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "description": self.description,
            "agent_ids": self.agent_ids,
            "is_active": self.is_active,
            "sync_status": self.sync_status,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "meta": self.meta,
        }

class KnowledgeCollectionSource(Base):
    __tablename__ = "knowledge_collection_sources"
    __table_args__ = (Index("ix_kb_coll_src_coll", "collection_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_collections.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(200), nullable=False)
    uri: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    sync_status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "collection_id": str(self.collection_id),
            "tenant_id": str(self.tenant_id),
            "source_type": self.source_type,
            "source_id": self.source_id,
            "uri": self.uri,
            "sync_status": self.sync_status,
            "last_sync_at": self.last_sync_at.isoformat() if self.last_sync_at else None,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

# -------------------------------------------------------------- Call Simulation
class CallSimulation(Base):
    __tablename__ = "call_simulations"
    __table_args__ = (Index("ix_call_sim_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    scenario: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "agent_id": self.agent_id,
            "name": self.name,
            "scenario": self.scenario,
            "status": self.status,
            "result": self.result,
            "evidence": self.evidence,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

# -------------------------------------------------------------- Tool Registry
class AgentTool(Base):
    __tablename__ = "agent_tools"
    __table_args__ = (
        Index("ix_agent_tools_tenant", "tenant_id"),
        UniqueConstraint("tenant_id", "agent_id", "name", name="uq_agent_tools_name"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    schema: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    auth_binding: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "agent_id": self.agent_id,
            "name": self.name,
            "description": self.description,
            "schema": self.schema,
            "auth_binding": self.auth_binding,
            "is_enabled": self.is_enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

# -------------------------------------------------------------- Workflow Event Triggers
class WorkflowTrigger(Base):
    __tablename__ = "workflow_triggers"
    __table_args__ = (Index("ix_wf_triggers_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(80), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "workflow_id": self.workflow_id,
            "event_type": self.event_type,
            "is_enabled": self.is_enabled,
            "config": self.config,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

# -------------------------------------------------------------- Multichannel
class MessageChannel(Base):
    __tablename__ = "message_channels"
    __table_args__ = (Index("ix_msg_channels_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    health_status: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "channel_type": self.channel_type,
            "provider": self.provider,
            "is_active": self.is_active,
            "config": self.config,
            "health_status": self.health_status,
            "last_health_check_at": self.last_health_check_at.isoformat() if self.last_health_check_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

# -------------------------------------------------------------- Call Policies
class CallPolicy(Base):
    __tablename__ = "call_policies"
    __table_args__ = (
        Index("ix_call_policies_tenant", "tenant_id"),
        UniqueConstraint("tenant_id", "agent_id", "policy_type", name="uq_call_policies_agent_type"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    policy_type: Mapped[str] = mapped_column(String(32), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "agent_id": self.agent_id,
            "policy_type": self.policy_type,
            "config": self.config,
            "is_enabled": self.is_enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

# -------------------------------------------------------------- Post-call Analysis
class AnalysisSchema(Base):
    __tablename__ = "analysis_schemas"
    __table_args__ = (Index("ix_analysis_schemas_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    fields: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "description": self.description,
            "fields": self.fields,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

class AnalysisResult(Base):
    __tablename__ = "analysis_results"
    __table_args__ = (Index("ix_analysis_results_tenant", "tenant_id"), Index("ix_analysis_results_call", "call_id"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    schema_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_schemas.id", ondelete="CASCADE"), nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="completed", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "call_id": str(self.call_id),
            "schema_id": str(self.schema_id),
            "result": self.result,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

class BackfillJob(Base):
    __tablename__ = "backfill_jobs"
    __table_args__ = (Index("ix_backfill_tenant", "tenant_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    schema_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("analysis_schemas.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    total_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "schema_id": str(self.schema_id),
            "status": self.status,
            "total_calls": self.total_calls,
            "processed_calls": self.processed_calls,
            "failed_calls": self.failed_calls,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

# -------------------------------------------------------------- Live Monitoring / Takeover
class LiveCallSession(Base):
    __tablename__ = "live_call_sessions"
    __table_args__ = (Index("ix_live_sessions_tenant", "tenant_id"), Index("ix_live_sessions_call", "call_id"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    call_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    supervisor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    mode: Mapped[str] = mapped_column(String(24), default="listen", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "call_id": str(self.call_id),
            "supervisor_id": str(self.supervisor_id),
            "mode": self.mode,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "meta": self.meta,
        }

# -------------------------------------------------------------- DNC
class DncEntry(Base):
    __tablename__ = "dnc_entries"
    __table_args__ = (
        Index("ix_dnc_tenant", "tenant_id"),
        UniqueConstraint("tenant_id", "phone", name="uq_dnc_phone"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="manual", nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "phone": self.phone,
            "reason": self.reason,
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
