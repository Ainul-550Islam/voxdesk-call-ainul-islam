"""Flow components, agent component bindings and agent folders (PART 12, P12-20/P12-25).

Five tables, all tenant-scoped:

``flow_components``          a reusable block of flow nodes, owned by a tenant
``flow_component_versions``  immutable versions of that block, one of them published
``agent_component_bindings`` which agent placed which version, and how it follows it
``agent_folders``            the folder tree agents are organised in
``agent_drafts``             named tenant-scoped draft configurations for agents

The binding row records ``sync_mode`` explicitly. That is the whole design: an agent either froze a
version (``snapshot``) or follows the published one (``live-sync``), and a publish of the component
creates a *draft* on the dependents rather than editing a live flow.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    func,
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


class ComponentSyncMode(str, PyEnum):
    SNAPSHOT = "snapshot"
    LIVE_SYNC = "live-sync"


class FlowComponent(Base):
    """A reusable block of flow nodes."""

    __tablename__ = "flow_components"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_flow_components_tenant_name"),
        Index("ix_flow_components_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class FlowComponentVersion(Base):
    """One immutable version of a component. Exactly one per component may be published."""

    __tablename__ = "flow_component_versions"
    __table_args__ = (
        UniqueConstraint(
            "component_id", "version", name="uq_flow_component_versions_component_version"
        ),
        Index("ix_flow_component_versions_component", "component_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flow_components.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    graph: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    changelog: Mapped[str] = mapped_column(Text, nullable=False, default="")
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class AgentComponentBinding(Base):
    """An agent's use of one component version."""

    __tablename__ = "agent_component_bindings"
    __table_args__ = (
        UniqueConstraint(
            "agent_id", "component_id", name="uq_agent_component_bindings_agent_component"
        ),
        Index("ix_agent_component_bindings_component", "component_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    environment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("environments.id", ondelete="SET NULL"), nullable=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    component_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flow_components.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sync_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ComponentSyncMode.SNAPSHOT.value
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )


class AgentFolder(Base):
    """A folder in the agent list. `parent_id` makes it a tree; depth is capped in the service."""

    __tablename__ = "agent_folders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "parent_id", "name", name="uq_agent_folders_tenant_parent_name"),
        Index("ix_agent_folders_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_folders.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class AgentDraft(Base):
    """Named, tenant-scoped draft configuration for an agent."""

    __tablename__ = "agent_drafts"
    __table_args__ = (
        UniqueConstraint("agent_id", "label", name="uq_agent_drafts_agent_label"),
        Index("ix_agent_drafts_agent", "agent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(
        String(120), nullable=False, default="main", server_default="main"
    )
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(
        String(120), nullable=False, default="", server_default=""
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now, server_default=func.now()
    )


__all__ = [
    "AgentDraft",
    "AgentComponentBinding",
    "AgentFolder",
    "ComponentSyncMode",
    "FlowComponent",
    "FlowComponentVersion",
]
