"""ACD persistence and decision records.

Queue, skill, presence and routing rows live here. ``User``, ``Call`` and
``InboxThreadState`` stay in ``app.db.models``. A decision never stores a
transcript, a credential, or a recording URL.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base

STATES = (
    "offline",
    "available",
    "ringing",
    "busy",
    "wrap_up",
    "away",
    "paused",
    "disabled",
)
ENTRY_STATUSES = ("waiting", "assigned", "overflowed", "abandoned", "completed", "failed")
STRATEGIES = ("skill_first", "priority_first", "least_loaded", "round_robin", "longest_idle")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Queue(Base):
    __tablename__ = "cc_queues"
    __table_args__ = (
        UniqueConstraint("tenant_id", "environment_id", "name", name="uq_cc_queues_name"),
        CheckConstraint("priority >= 0", name="ck_cc_queues_priority"),
        CheckConstraint("max_concurrency >= 1", name="ck_cc_queues_concurrency"),
        Index("ix_cc_queues_tenant", "tenant_id", "environment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    strategy: Mapped[str] = mapped_column(String(32), default="least_loaded", nullable=False)
    required_skills: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    overflow_policy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "environment_id": str(self.environment_id),
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "priority": self.priority,
            "max_concurrency": self.max_concurrency,
            "strategy": self.strategy,
            "required_skills": list(self.required_skills or []),
            "overflow_policy": dict(self.overflow_policy or {}),
        }


class QueueMember(Base):
    __tablename__ = "cc_queue_members"
    __table_args__ = (
        UniqueConstraint("queue_id", "user_id", name="uq_cc_queue_members"),
        Index("ix_cc_queue_members_tenant", "tenant_id", "queue_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cc_queues.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "queue_id": str(self.queue_id),
            "user_id": str(self.user_id),
            "priority": self.priority,
            "enabled": self.enabled,
        }


class Skill(Base):
    __tablename__ = "cc_skills"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_cc_skills_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "name": self.name,
            "enabled": self.enabled,
        }


class AgentSkill(Base):
    __tablename__ = "cc_agent_skills"
    __table_args__ = (
        UniqueConstraint("skill_id", "user_id", name="uq_cc_agent_skills"),
        CheckConstraint("proficiency >= 1 AND proficiency <= 5", name="ck_cc_agent_skills_proficiency"),
        Index("ix_cc_agent_skills_user", "tenant_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cc_skills.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    proficiency: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "skill_id": str(self.skill_id),
            "user_id": str(self.user_id),
            "proficiency": self.proficiency,
            "weight": self.weight,
            "enabled": self.enabled,
        }


class AgentPresence(Base):
    __tablename__ = "cc_agent_presence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_cc_agent_presence"),
        CheckConstraint(
            "state IN ('offline', 'available', 'ringing', 'busy', 'wrap_up', 'away', 'paused', 'disabled')",
            name="ck_cc_agent_presence_state",
        ),
        CheckConstraint("capacity >= 1", name="ck_cc_agent_presence_capacity"),
        CheckConstraint("active_count >= 0", name="ck_cc_agent_presence_active"),
        Index("ix_cc_agent_presence_state", "tenant_id", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(16), default="offline", nullable=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    last_assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    capacity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    def as_dict(self) -> dict:
        return {
            "user_id": str(self.user_id),
            "tenant_id": str(self.tenant_id),
            "state": self.state,
            "capacity": self.capacity,
            "active_count": self.active_count,
            "version": self.version,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "state_changed_at": self.state_changed_at.isoformat() if self.state_changed_at else None,
        }


class QueueEntry(Base):
    __tablename__ = "cc_queue_entries"
    __table_args__ = (
        UniqueConstraint("active_lock", name="uq_cc_queue_entries_active"),
        CheckConstraint(
            "status IN ('waiting', 'assigned', 'overflowed', 'abandoned', 'completed', 'failed')",
            name="ck_cc_queue_entries_status",
        ),
        Index("ix_cc_queue_entries_waiting", "tenant_id", "queue_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    queue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cc_queues.id", ondelete="CASCADE"), nullable=False
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="waiting", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    active_lock: Mapped[str | None] = mapped_column(String(80), nullable=True)
    outcome: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "environment_id": str(self.environment_id),
            "queue_id": str(self.queue_id),
            "call_id": str(self.call_id),
            "status": self.status,
            "priority": self.priority,
            "outcome": self.outcome,
            "version": self.version,
        }


class RoutingAssignment(Base):
    __tablename__ = "cc_routing_assignments"
    __table_args__ = (
        UniqueConstraint("entry_lock", name="uq_cc_routing_entry_lock"),
        UniqueConstraint("agent_lock", name="uq_cc_routing_agent_lock"),
        Index("ix_cc_routing_assignments_tenant", "tenant_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cc_queue_entries.id", ondelete="CASCADE"), nullable=False
    )
    queue_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    entry_lock: Mapped[str | None] = mapped_column(String(40), nullable=True)
    agent_lock: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "entry_id": str(self.entry_id),
            "queue_id": str(self.queue_id),
            "user_id": str(self.user_id),
            "status": self.status,
        }


class RoutingDecision(Base):
    __tablename__ = "cc_routing_decisions"
    __table_args__ = (Index("ix_cc_routing_decisions_tenant", "tenant_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    entry_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    queue_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    strategy: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    matched_skills: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    rejected: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    applied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "environment_id": str(self.environment_id) if self.environment_id else None,
            "entry_id": str(self.entry_id) if self.entry_id else None,
            "queue_id": str(self.queue_id) if self.queue_id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "strategy": self.strategy,
            "matched_skills": list(self.matched_skills or []),
            "rejected": list(self.rejected or []),
            "applied": self.applied,
            "outcome": self.outcome,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class RoutingCursor(Base):
    __tablename__ = "cc_routing_cursors"

    queue_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cc_queues.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    last_user_id: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


@dataclass(frozen=True)
class Candidate:
    user_id: uuid.UUID
    member_priority: int
    proficiency: int
    skill_count: int
    active_count: int
    last_assigned_at: datetime | None
    eligible: bool
    reason: str = ""

    def as_rejected(self) -> dict:
        return {"user_id": str(self.user_id), "reason": self.reason}


@dataclass
class RouteChoice:
    selected: Candidate | None
    rejected: list[dict] = field(default_factory=list)
    strategy: str = ""
    matched_skills: list[str] = field(default_factory=list)
