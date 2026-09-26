"""AI governance contracts and persistence.

Provider credentials stay in ``app.core.config``. These rows store policy,
prompt versions and evaluation ownership. They do not store API keys, and
they do not replace ``Tenant.llm_preset`` or the billing ledger.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.tenancy.isolation import HierarchyError


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class GovernanceError(HierarchyError):
    """A governance rule failed. Routes translate this; they do not parse it."""

    def __init__(
        self, message: str, *, code: str = "governance_error", status_code: int = 409
    ) -> None:
        super().__init__(message, code=code, status_code=status_code)


class PolicyDenied(GovernanceError):
    def __init__(self, message: str = "AI policy denied the request") -> None:
        super().__init__(message, code="policy_denied", status_code=403)


class BudgetDenied(GovernanceError):
    def __init__(self, message: str = "AI budget denied the request") -> None:
        super().__init__(message, code="budget_denied", status_code=409)


class DeadlineExceeded(GovernanceError):
    def __init__(self, message: str = "AI deadline exceeded") -> None:
        super().__init__(message, code="deadline_exceeded", status_code=409)


@dataclass(frozen=True)
class ModelDescriptor:
    """Selection metadata. Never an API key."""

    provider: str
    model: str
    preset: str | None
    est_latency_ms: int
    supports_tools: bool
    supports_streaming: bool
    status: str
    development_only: bool = False

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "preset": self.preset,
            "est_latency_ms": self.est_latency_ms,
            "supports_tools": self.supports_tools,
            "supports_streaming": self.supports_streaming,
            "status": self.status,
            "development_only": self.development_only,
        }


@dataclass(frozen=True)
class PolicyView:
    tenant_id: uuid.UUID
    status: str
    allowed_presets: tuple[str, ...]
    disabled_providers: tuple[str, ...]
    development_only_presets: tuple[str, ...]
    token_ceiling: int | None
    persisted: bool

    def as_dict(self) -> dict:
        return {
            "tenant_id": str(self.tenant_id),
            "status": self.status,
            "allowed_presets": list(self.allowed_presets),
            "disabled_providers": list(self.disabled_providers),
            "development_only_presets": list(self.development_only_presets),
            "token_ceiling": self.token_ceiling,
            "persisted": self.persisted,
            "pricing": "unknown" if self.token_ceiling is None else "configured",
        }


@dataclass
class TelemetryEvent:
    fields: dict = field(default_factory=dict)


class AIModelPolicy(Base):
    """Per-tenant allowlist. Absence of a row means the existing presets stay allowed."""

    __tablename__ = "ai_model_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_ai_model_policies_tenant"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_ai_model_policies_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    allowed_presets: Mapped[list] = mapped_column(JSON, default=list)
    disabled_providers: Mapped[list] = mapped_column(JSON, default=list)
    development_only_presets: Mapped[list] = mapped_column(JSON, default=list)
    token_ceiling: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class AIPrompt(Base):
    __tablename__ = "ai_prompts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "environment_scope", "prompt_key", name="uq_ai_prompts_scope_key"
        ),
        CheckConstraint("status IN ('active', 'retired')", name="ck_ai_prompts_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    environment_scope: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    prompt_key: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    current_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class AIPromptVersion(Base):
    """A published body is immutable. The service refuses in-place edits."""

    __tablename__ = "ai_prompt_versions"
    __table_args__ = (
        UniqueConstraint("prompt_id", "version_number", name="uq_ai_prompt_versions_number"),
        CheckConstraint(
            "status IN ('draft', 'published', 'retired')", name="ck_ai_prompt_versions_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    prompt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("ai_prompts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIPromptRollout(Base):
    __tablename__ = "ai_prompt_rollouts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "prompt_id", "environment_scope", name="uq_ai_prompt_rollouts_scope"
        ),
        CheckConstraint("percent >= 0 AND percent <= 100", name="ck_ai_prompt_rollouts_percent"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prompt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_prompts.id", ondelete="CASCADE"), nullable=False
    )
    environment_scope: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    stable_version: Mapped[int] = mapped_column(Integer, nullable=False)
    canary_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    salt: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class AIEvalDataset(Base):
    __tablename__ = "ai_eval_datasets"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_ai_eval_datasets_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    cases: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )


class AIEvalRun(Base):
    __tablename__ = "ai_eval_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('completed', 'failed', 'cancelled')", name="ck_ai_eval_runs_status"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ai_eval_datasets.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    case_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AIAdmissionCounter(Base):
    """Admission lock for a configured token ceiling. Not a billing meter."""

    __tablename__ = "ai_admission_counters"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    tokens_reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
