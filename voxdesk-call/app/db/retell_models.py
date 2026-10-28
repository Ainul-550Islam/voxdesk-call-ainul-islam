"""SQLAlchemy ORM models for Retell parity foundation (Migration 0038).

Defines the tenant-scoped tables introduced in
``alembic/versions/0038_retell_parity_foundation.py`` and
``alembic/versions/0038_agent_chat_conductor.py``:

* ``contacts`` (``Contact``)
* ``contact_memory_entries`` (``ContactMemoryEntry``)
* ``chat_agents`` (``ChatAgent``)
* ``chat_agent_versions`` (``ChatAgentVersion``)
* ``chat_sessions`` (``ChatSession``)
* ``chat_messages`` (``ChatMessage``)
* ``dynamic_variable_definitions`` (``DynamicVariableDefinition``)
* ``agent_transfers`` (``AgentTransfer``)
* ``agent_transfer_events`` (``AgentTransferEvent``)
* ``test_suites`` (``TestSuite``)
* ``test_cases`` (``TestCase``)
* ``test_runs`` (``TestRun``)
* ``evaluation_rules`` (``EvaluationRule``)
* ``evaluation_results`` (``EvaluationResult``)

Status/source/mode columns are stored as VARCHAR with Python string-Enums as the
single source of truth, matching the migration contract.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Uuid as UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ContactSourceEnum(str, PyEnum):
    MANUAL = "manual"
    CALL = "call"
    CHAT = "chat"
    SMS = "sms"
    CRM = "crm"
    IMPORT = "import"
    API = "api"


class ContactLifecycleEnum(str, PyEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    BLOCKED = "blocked"


class MemoryValueTypeEnum(str, PyEnum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    JSON = "json"


class MemorySourceEnum(str, PyEnum):
    MANUAL = "manual"
    CALL = "call"
    CHAT = "chat"
    CRM = "crm"
    AGENT = "agent"
    API = "api"


class ChatAgentStatusEnum(str, PyEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class ChatAgentVersionStatusEnum(str, PyEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class ChatSessionStatusEnum(str, PyEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    EXPIRED = "expired"


class ChatMessageRoleEnum(str, PyEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class DynamicVariableTypeEnum(str, PyEnum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ENUM = "enum"
    JSON = "json"


class AgentTransferModeEnum(str, PyEnum):
    AGENT_TO_AGENT = "agent_to_agent"
    AGENT_TO_HUMAN = "agent_to_human"
    HUMAN_TO_AGENT = "human_to_agent"
    QUEUE_ESCALATION = "queue_escalation"


class AgentTransferStatusEnum(str, PyEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TestSuiteStatusEnum(str, PyEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TestRunModeEnum(str, PyEnum):
    LLM = "llm"
    SIMULATION = "simulation"
    WEB_CALL = "web_call"
    PHONE_CALL = "phone_call"
    CHAT = "chat"


class TestRunStatusEnum(str, PyEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    CANCELLED = "cancelled"
    NOT_RUN = "not_run"


class EvaluationRuleTypeEnum(str, PyEnum):
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    REGEX = "regex"
    JSON_PATH_EQUALS = "json_path_equals"
    JSON_PATH_EXISTS = "json_path_exists"
    TOOL_CALLED = "tool_called"
    TOOL_NOT_CALLED = "tool_not_called"
    TRANSFER_OCCURRED = "transfer_occurred"
    VARIABLE_EQUALS = "variable_equals"
    TURN_COUNT_MAX = "turn_count_max"
    TURN_COUNT_MIN = "turn_count_min"
    LATENCY_MS_MAX = "latency_ms_max"
    FINAL_STATE_EQUALS = "final_state_equals"
    LLM_JUDGE = "llm_judge"


class EvaluationResultStatusEnum(str, PyEnum):
    PASSED = "passed"
    FAILED_ASSERTION = "failed_assertion"
    EVALUATION_ERROR = "evaluation_error"
    SKIPPED = "skipped"
    NO_ASSERTIONS = "no_assertions"


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "phone", name="uq_contacts_tenant_phone"),
        Index("ix_contacts_tenant_id", "tenant_id"),
        Index("ix_contacts_tenant_lifecycle", "tenant_id", "lifecycle"),
        Index("ix_contacts_tenant_name", "tenant_id", "name"),
        Index("ix_contacts_tenant_created", "tenant_id", "created_at"),
        Index("ix_contacts_crm_ref", "tenant_id", "crm_provider", "crm_external_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("environments.id", ondelete="SET NULL"),
        nullable=True,
    )
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    phone_raw: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    custom_fields: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ContactSourceEnum.MANUAL.value
    )
    lifecycle: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ContactLifecycleEnum.ACTIVE.value
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    crm_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    crm_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class ContactMemoryEntry(Base):
    __tablename__ = "contact_memory_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "contact_id", "key", name="uq_contact_memory_key"),
        Index("ix_contact_memory_entries_contact_id", "contact_id"),
        Index(
            "ix_contact_memory_contact",
            "tenant_id",
            "contact_id",
            "importance",
            "updated_at",
        ),
        Index("ix_contact_memory_expiry", "tenant_id", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    value_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MemoryValueTypeEnum.STRING.value
    )
    source: Mapped[str] = mapped_column(
        String(24), nullable=False, default=MemorySourceEnum.MANUAL.value
    )
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    importance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class ChatAgent(Base):
    __tablename__ = "chat_agents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_chat_agents_tenant_name"),
        Index("ix_chat_agents_tenant_id", "tenant_id"),
        Index("ix_chat_agents_tenant_status", "tenant_id", "status"),
        Index("ix_chat_agents_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ChatAgentStatusEnum.DRAFT.value
    )
    draft_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    published_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    draft_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class ChatAgentVersion(Base):
    __tablename__ = "chat_agent_versions"
    __table_args__ = (
        UniqueConstraint("chat_agent_id", "version", name="uq_chat_agent_version"),
        Index("ix_chat_agent_versions_chat_agent_id", "chat_agent_id"),
        Index("ix_chat_agent_versions_tenant", "tenant_id", "chat_agent_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ChatAgentVersionStatusEnum.PUBLISHED.value
    )
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    change_summary: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChatSession(Base):
    """Durable tenant-isolated chat conversation session with a ChatAgent."""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index("ix_chat_sessions_tenant_id", "tenant_id"),
        Index("ix_chat_sessions_agent_id", "chat_agent_id"),
        Index("ix_chat_sessions_contact_id", "contact_id"),
        Index("ix_chat_sessions_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("environments.id", ondelete="SET NULL"),
        nullable=True,
    )
    chat_agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_agent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    channel: Mapped[str] = mapped_column(String(24), nullable=False, default="web")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ChatSessionStatusEnum.ACTIVE.value
    )
    dynamic_variables: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class ChatMessage(Base):
    """Immutable ordered message row within a durable ChatSession."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_chat_messages_session_seq"),
        Index("ix_chat_messages_tenant_id", "tenant_id"),
        Index("ix_chat_messages_session_id", "session_id"),
        Index("ix_chat_messages_session_seq", "tenant_id", "session_id", "sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ChatMessageRoleEnum.USER.value
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tool_calls: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class DynamicVariableDefinition(Base):
    __tablename__ = "dynamic_variable_definitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "chat_agent_id",
            "name",
            name="uq_dynamic_variable_scope_name",
        ),
        Index("ix_dynamic_variable_definitions_tenant_id", "tenant_id"),
        Index("ix_dynamic_variable_definitions_chat_agent_id", "chat_agent_id"),
        Index("ix_dynamic_variables_tenant", "tenant_id", "chat_agent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    chat_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_agents.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    var_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DynamicVariableTypeEnum.STRING.value
    )
    description: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    default_value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    constraints: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_nullable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class AgentTransfer(Base):
    __tablename__ = "agent_transfers"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_agent_transfer_idempotency",
        ),
        Index("ix_agent_transfers_tenant_id", "tenant_id"),
        Index("ix_agent_transfers_tenant_status", "tenant_id", "status"),
        Index("ix_agent_transfers_call", "tenant_id", "call_id"),
        Index("ix_agent_transfers_contact", "tenant_id", "contact_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calls.id", ondelete="SET NULL"),
        nullable=True,
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default=AgentTransferModeEnum.AGENT_TO_AGENT.value
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=AgentTransferStatusEnum.PENDING.value
    )
    from_agent_ref: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    to_agent_ref: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    to_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    destination: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    reason: Mapped[str] = mapped_column(String(400), nullable=False, default="")
    failure_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    context_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentTransferEvent(Base):
    __tablename__ = "agent_transfer_events"
    __table_args__ = (
        Index("ix_agent_transfer_events_transfer_id", "transfer_id"),
        Index("ix_agent_transfer_events_transfer", "transfer_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    transfer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("agent_transfers.id", ondelete="CASCADE"),
        nullable=False,
    )
    event: Mapped[str] = mapped_column(String(48), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


# ------------------------------------ Prompt 3: Testing, Simulation & Evaluation


class TestSuite(Base):
    """Durable tenant-scoped collection of version-pinned test cases."""

    __tablename__ = "test_suites"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_test_suites_tenant_name"),
        Index("ix_test_suites_tenant_id", "tenant_id"),
        Index("ix_test_suites_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("environments.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=TestSuiteStatusEnum.ACTIVE.value
    )
    pass_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="all_passed"
    )
    min_pass_score: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class TestCase(Base):
    """Version-pinned test case definition for voice or chat agents."""

    __tablename__ = "test_cases"
    __table_args__ = (
        Index("ix_test_cases_tenant_id", "tenant_id"),
        Index("ix_test_cases_suite_id", "suite_id"),
        Index(
            "ix_test_cases_agent_version",
            "tenant_id",
            "agent_id",
            "agent_version_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    suite_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_suites.id", ondelete="CASCADE"),
        nullable=True,
    )
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="voice")
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    agent_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default=TestRunModeEnum.SIMULATION.value
    )
    input_messages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    dynamic_variables: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    expected_rules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class TestRun(Base):
    """First-class persisted execution record bound to an exact Agent Version."""

    __tablename__ = "test_runs"
    __table_args__ = (
        Index("ix_test_runs_tenant_id", "tenant_id"),
        Index("ix_test_runs_suite_id", "suite_id"),
        Index("ix_test_runs_batch_id", "batch_id"),
        Index("ix_test_runs_tenant_created", "tenant_id", "created_at"),
        Index(
            "ix_test_runs_agent_version",
            "tenant_id",
            "agent_id",
            "agent_version_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("environments.id", ondelete="SET NULL"),
        nullable=True,
    )
    suite_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_suites.id", ondelete="SET NULL"),
        nullable=True,
    )
    batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    test_case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_cases.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="voice")
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    agent_version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_config_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    pinned_config_snapshot: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )
    mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default=TestRunModeEnum.SIMULATION.value
    )
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=TestRunStatusEnum.QUEUED.value
    )
    is_mock_provider: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calls.id", ondelete="SET NULL"),
        nullable=True,
    )
    chat_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    transcript_snapshot: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    events_snapshot: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    usage_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    latency_metadata: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    final_output: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    scorecard_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class EvaluationRule(Base):
    """Bounded deterministic or LLM-judge evaluation rule."""

    __tablename__ = "evaluation_rules"
    __table_args__ = (
        Index("ix_evaluation_rules_tenant_id", "tenant_id"),
        Index("ix_evaluation_rules_suite_id", "suite_id"),
        Index("ix_evaluation_rules_case_id", "test_case_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    suite_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_suites.id", ondelete="CASCADE"),
        nullable=True,
    )
    test_case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_cases.id", ondelete="CASCADE"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    rule_type: Mapped[str] = mapped_column(
        String(48), nullable=False, default=EvaluationRuleTypeEnum.CONTAINS.value
    )
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    evaluator_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="v1.0"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class EvaluationResult(Base):
    """Evidence-backed result of evaluating one EvaluationRule against one TestRun."""

    __tablename__ = "evaluation_results"
    __table_args__ = (
        Index("ix_evaluation_results_tenant_id", "tenant_id"),
        Index("ix_evaluation_results_run_id", "tenant_id", "test_run_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    test_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    evaluation_rule_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evaluation_rules.id", ondelete="SET NULL"),
        nullable=True,
    )
    rule_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    rule_type: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=EvaluationResultStatusEnum.PASSED.value
    )
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evaluator_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="v1.0"
    )
    formula_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default="weighted_v1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


# ------------------------------------------- Prompt 4: Conductor Control Plane


class ConductorSessionStatusEnum(str, PyEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    ARCHIVED = "ARCHIVED"


class ConductorProposalStatusEnum(str, PyEnum):
    DRAFT = "DRAFT"
    GENERATING = "GENERATING"
    PROPOSED = "PROPOSED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    SIMULATING = "SIMULATING"
    SIMULATED = "SIMULATED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    APPROVED = "APPROVED"
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    STALE = "STALE"


class ConductorOperationTypeEnum(str, PyEnum):
    SET = "set"
    REPLACE = "replace"
    ADD = "add"
    REMOVE = "remove"
    APPEND = "append"
    DELETE = "delete"
    MOVE = "move"


class ConductorApprovalStateEnum(str, PyEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ConductorApprovalActionEnum(str, PyEnum):
    APPROVE_CHANGE = "approve_change"
    REJECT_CHANGE = "reject_change"
    UNDO_CHANGE = "undo_change"
    APPROVE_PROPOSAL = "approve_proposal"
    REJECT_PROPOSAL = "reject_proposal"
    UNDO_PROPOSAL = "undo_proposal"
    APPLY_PROPOSAL = "apply_proposal"


class ConductorRiskLevelEnum(str, PyEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ConductorEvidenceSourceEnum(str, PyEnum):
    AGENT_VERSION = "agent_version"
    CALL = "call"
    QA_SCORECARD = "qa_scorecard"
    TEST_SUITE = "test_suite"
    TEST_CASE = "test_case"
    TEST_RUN = "test_run"
    EVALUATION_RESULT = "evaluation_result"
    VALIDATION = "validation"
    TOOL_REGISTRY = "tool_registry"
    KNOWLEDGE_BASE = "knowledge_base"
    WORKFLOW = "workflow"


class ConductorSession(Base):
    """Permission-scoped AI copilot session bound to a tenant, environment, and agent."""

    __tablename__ = "conductor_sessions"
    __table_args__ = (
        Index("ix_conductor_sessions_tenant_id", "tenant_id"),
        Index("ix_conductor_sessions_tenant_agent", "tenant_id", "agent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("environments.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_id: Mapped[str] = mapped_column(String(80), nullable=False)
    agent_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="voice")
    starting_agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    starting_version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    caller_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    origin_surface: Mapped[str] = mapped_column(
        String(64), nullable=False, default="agent_builder"
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ConductorSessionStatusEnum.ACTIVE.value
    )
    request_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    context_policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    context_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    correlation_id: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ConductorProposal(Base):
    """Durable, version-pinned proposed change set requiring human approval before apply."""

    __tablename__ = "conductor_proposals"
    __table_args__ = (
        Index("ix_conductor_proposals_tenant_id", "tenant_id"),
        Index("ix_conductor_proposals_session_id", "session_id"),
        Index("ix_conductor_proposals_tenant_agent", "tenant_id", "agent_id"),
        Index("ix_conductor_proposals_status", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conductor_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("environments.id", ondelete="SET NULL"),
        nullable=True,
    )
    agent_id: Mapped[str] = mapped_column(String(80), nullable=False)
    agent_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="voice")
    base_agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    base_version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    base_config_hash: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    base_draft_etag: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    base_config_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    request_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ConductorProposalStatusEnum.PROPOSED.value
    )
    validation_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_run"
    )
    validation_report: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    simulation_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_run"
    )
    simulation_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    risk_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    candidate_config_snapshot: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )
    final_candidate_hash: Mapped[str] = mapped_column(
        String(96), nullable=False, default=""
    )
    resulting_agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    resulting_version_number: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    apply_idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    approved_change_hash: Mapped[str | None] = mapped_column(
        String(96), nullable=True
    )
    is_mock_provider: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    provider: Mapped[str] = mapped_column(
        String(48), nullable=False, default="openai"
    )
    model: Mapped[str] = mapped_column(
        String(96), nullable=False, default="gpt-4o-mini"
    )
    correlation_id: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ConductorChange(Base):
    """Granular, individually reviewable configuration operation inside a ConductorProposal."""

    __tablename__ = "conductor_changes"
    __table_args__ = (
        Index("ix_conductor_changes_tenant_id", "tenant_id"),
        Index("ix_conductor_changes_proposal_seq", "proposal_id", "sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conductor_proposals.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    section: Mapped[str] = mapped_column(String(64), nullable=False, default="general")
    path: Mapped[str] = mapped_column(String(200), nullable=False)
    operation: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ConductorOperationTypeEnum.SET.value
    )
    old_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSON, nullable=True
    )
    new_value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSON, nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    risk_level: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ConductorRiskLevelEnum.LOW.value
    )
    validation_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    validation_messages: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list
    )
    simulation_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_run"
    )
    approval_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ConductorApprovalStateEnum.PENDING.value
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class ConductorApproval(Base):
    """Immutable human review / approval / rejection / undo ledger entry for a proposal or change."""

    __tablename__ = "conductor_approvals"
    __table_args__ = (
        Index("ix_conductor_approvals_tenant_id", "tenant_id"),
        Index("ix_conductor_approvals_proposal_id", "proposal_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conductor_proposals.id", ondelete="CASCADE"),
        nullable=False,
    )
    change_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conductor_changes.id", ondelete="CASCADE"),
        nullable=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    previous_state: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    new_state: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class ConductorEvidence(Base):
    """Evidence record linking a ConductorProposal to calls, test runs, evaluations, QA, or validation."""

    __tablename__ = "conductor_evidence"
    __table_args__ = (
        Index("ix_conductor_evidence_tenant_id", "tenant_id"),
        Index("ix_conductor_evidence_proposal_id", "proposal_id"),
        Index("ix_conductor_evidence_source", "tenant_id", "source_type", "source_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conductor_proposals.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(
        String(48), nullable=False, default=ConductorEvidenceSourceEnum.AGENT_VERSION.value
    )
    source_id: Mapped[str] = mapped_column(String(120), nullable=False)
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class ConductorWebhookReceipt(Base):
    """Durable tenant-scoped idempotency receipt for signed Conductor callbacks."""

    __tablename__ = "conductor_webhook_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "event_id", name="uq_conductor_webhook_receipt_tenant_event"
        ),
        Index("ix_conductor_webhook_receipts_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conductor_proposals.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[str] = mapped_column(String(120), nullable=False)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conductor_evidence.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class PublicWidgetKeyStatusEnum(str, PyEnum):
    ACTIVE = "active"
    ROTATED = "rotated"
    REVOKED = "revoked"
    EXPIRED = "expired"


class PublicWidgetSessionModeEnum(str, PyEnum):
    CHAT = "chat"
    VOICE = "voice"


class PublicWidgetSessionStatusEnum(str, PyEnum):
    READY = "ready"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    COMPLETED = "completed"
    EXPIRED = "expired"
    FAILED = "failed"
    NOT_CONFIGURED = "not_configured"
    FORBIDDEN_ORIGIN = "forbidden_origin"
    INVALID_PUBLIC_KEY = "invalid_public_key"


class PublicWidgetKey(Base):
    """Scoped public embeddable key bound to tenant + agent + environment + allowed origins.

    Stores only the SHA-256 hash of the key secret (`key_hash`) and a human-readable
    `key_prefix` (`vdpk_<8hex>`). Never grants private dashboard/admin/tool/connector
    permissions.
    """

    __tablename__ = "public_widget_keys"
    __table_args__ = (
        UniqueConstraint("key_hash", name="uq_public_widget_keys_hash"),
        Index("ix_public_widget_keys_tenant_id", "tenant_id"),
        Index("ix_public_widget_keys_tenant_agent", "tenant_id", "agent_id", "status"),
        Index("ix_public_widget_keys_prefix", "key_prefix"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("environments.id", ondelete="SET NULL"),
        nullable=True,
    )
    environment_name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="production"
    )
    agent_id: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="voice")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(40), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=PublicWidgetKeyStatusEnum.ACTIVE.value
    )
    allowed_origins: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    allowed_capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    session_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    require_published_agent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    widget_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    rotated_from_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    rotated_to_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    revoke_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_origin: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_used_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class PublicWidgetSession(Base):
    """Short-lived restricted public widget session pinned to an exact AgentVersion."""

    __tablename__ = "public_widget_sessions"
    __table_args__ = (
        UniqueConstraint(
            "session_token_hash", name="uq_public_widget_sessions_token_hash"
        ),
        Index("ix_public_widget_sessions_tenant_id", "tenant_id"),
        Index("ix_public_widget_sessions_key_id", "public_key_id", "created_at"),
        Index("ix_public_widget_sessions_agent", "tenant_id", "agent_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("environments.id", ondelete="SET NULL"),
        nullable=True,
    )
    public_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public_widget_keys.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="voice")
    agent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    agent_version_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default=PublicWidgetSessionModeEnum.CHAT.value
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PublicWidgetSessionStatusEnum.READY.value
    )
    session_token_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    origin: Mapped[str] = mapped_column(String(255), nullable=False)
    visitor_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    chat_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    test_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    transport: Mapped[str] = mapped_column(
        String(32), nullable=False, default="http_chat"
    )
    turns_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_turns: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    transcript: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class PublicContactSalesRequest(Base):
    """Durable public Contact Sales / Enterprise Architecture Review request."""

    __tablename__ = "public_contact_sales_requests"
    __table_args__ = (
        Index("ix_public_contact_sales_email", "work_email"),
        Index("ix_public_contact_sales_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    work_email: Mapped[str] = mapped_column(String(254), nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    job_title: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    phone_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    monthly_call_volume: Mapped[str] = mapped_column(
        String(64), nullable=False, default="10k-50k"
    )
    primary_use_case: Mapped[str] = mapped_column(
        String(120), nullable=False, default="voice_agents"
    )
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_path: Mapped[str] = mapped_column(
        String(255), nullable=False, default="/contact-sales"
    )
    origin: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


__all__ = [
    "AgentTransfer",
    "AgentTransferEvent",
    "AgentTransferModeEnum",
    "AgentTransferStatusEnum",
    "ChatAgent",
    "ChatAgentStatusEnum",
    "ChatAgentVersion",
    "ChatAgentVersionStatusEnum",
    "ChatMessage",
    "ChatMessageRoleEnum",
    "ChatSession",
    "ChatSessionStatusEnum",
    "ConductorApproval",
    "ConductorApprovalActionEnum",
    "ConductorApprovalStateEnum",
    "ConductorChange",
    "ConductorEvidence",
    "ConductorEvidenceSourceEnum",
    "ConductorOperationTypeEnum",
    "ConductorProposal",
    "ConductorProposalStatusEnum",
    "ConductorRiskLevelEnum",
    "ConductorSession",
    "ConductorSessionStatusEnum",
    "Contact",
    "ContactLifecycleEnum",
    "ContactMemoryEntry",
    "ContactSourceEnum",
    "DynamicVariableDefinition",
    "DynamicVariableTypeEnum",
    "EvaluationResult",
    "EvaluationResultStatusEnum",
    "EvaluationRule",
    "EvaluationRuleTypeEnum",
    "MemorySourceEnum",
    "MemoryValueTypeEnum",
    "PublicContactSalesRequest",
    "PublicWidgetKey",
    "PublicWidgetKeyStatusEnum",
    "PublicWidgetSession",
    "PublicWidgetSessionModeEnum",
    "PublicWidgetSessionStatusEnum",
    "TestCase",
    "TestRun",
    "TestRunModeEnum",
    "TestRunStatusEnum",
    "TestSuite",
    "TestSuiteStatusEnum",
]
