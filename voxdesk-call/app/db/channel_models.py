"""Channel, workflow and memory tables (PART 14).

Six tables, each owning one thing the platform has to be able to explain later:

* `workflow_installs` -- which integration a tenant installed, at which version,
  with which configuration, and whether the permission review was signed off;
* `workflow_step_runs` -- one row per attempted step, with an idempotency key
  and an attempt counter, because a durable executor that cannot prove it did
  not double-post to Slack is not durable;
* `contact_briefs` -- the running brief shown to the agent, with the counts it
  was built from so a stale brief is visible rather than plausible;
* `share_links` -- an expiring, usage-capped link to talk to one agent. Only a
  hash of the passcode and of the token are stored;
* `public_abuse_budgets` -- per public key and per day: requests, billable
  seconds, and how many were refused, so a budget cap has a counter behind it;
* `memory_settings` -- per tenant memory switches and the workspace prompt.

Nothing here duplicates an existing table. `contact_memory_entries` (the key/value
facts) stays where it is; the brief is a *derived* view with its own row so it
can be edited and cleared independently of the facts under it.
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
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowInstall(Base):
    """One installed library workflow for one tenant."""

    __tablename__ = "workflow_installs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    workflow_key: Mapped[str] = mapped_column(String(80), nullable=False)
    #: The catalogue version this install was reviewed against. An install keeps
    #: working when the catalogue moves on; the dashboard shows the drift.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    allowed_actions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: Who approved which permissions, and when. A workflow that can post to
    #: Slack must be signed off explicitly, not inherited from "the tenant
    #: enabled it".
    permission_review: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    installed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "workflow_key", name="uq_workflow_install_key"),
        Index("ix_workflow_installs_tenant_enabled", "tenant_id", "enabled"),
    )


class WorkflowStepRun(Base):
    """A durable step execution attempt."""

    __tablename__ = "workflow_step_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    install_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workflow_installs.id", ondelete="SET NULL"), nullable=True
    )
    call_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    step: Mapped[str] = mapped_column(String(80), nullable=False)
    phase: Mapped[str] = mapped_column(String(16), nullable=False, default="post_call")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Stable per (call, step, install) so a retry or a worker restart cannot
    #: post the same Slack message twice.
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_preview: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_workflow_step_idempotency"),
        Index("ix_workflow_step_runs_tenant_status", "tenant_id", "status"),
    )


class ContactBrief(Base):
    """The running brief for one contact, built from calls and SMS."""

    __tablename__ = "contact_briefs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    brief: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: What the brief was built from. A brief with `calls_behind=2` when the
    #: contact has five calls is visibly stale instead of quietly wrong.
    calls_behind: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_behind: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    open_items: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    pii_redacted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    edited_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "contact_id", name="uq_contact_brief_contact"),
        Index("ix_contact_briefs_tenant", "tenant_id"),
    )


class ShareLink(Base):
    """An expiring, usage-capped link to talk to one agent."""

    __tablename__ = "share_links"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[str] = mapped_column(String(120), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    #: Only a hash is stored. A leaked database dump must not yield working links.
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    passcode_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    max_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    used_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_share_links_tenant_agent", "tenant_id", "agent_id"),
        CheckConstraint("max_uses >= 1", name="ck_share_links_max_uses"),
        CheckConstraint("used_count >= 0", name="ck_share_links_used"),
    )


class PublicAbuseBudget(Base):
    """The counter behind a public key's daily budget."""

    __tablename__ = "public_abuse_budgets"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    public_key_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("public_widget_keys.id", ondelete="CASCADE"), nullable=False
    )
    #: UTC day, stored as a string so a timezone bug cannot silently reset a
    #: budget in the middle of the customer's day.
    day: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    seconds_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refused: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("public_key_id", "day", name="uq_abuse_budget_key_day"),
    )


class MemorySettings(Base):
    """Per-tenant memory switches and the workspace memory prompt."""

    __tablename__ = "memory_settings"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    save_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    recall_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    brief_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Added to every agent's system prompt when memory is on. Empty by default:
    #: a workspace prompt is a commercial decision, not a default.
    workspace_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)
    updated_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=_now, onupdate=_now
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now)
