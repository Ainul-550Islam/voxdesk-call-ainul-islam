"""Durable anomaly run/result projection scoped to execution hierarchy."""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.governance.context import GovernanceScope
from app.governance.hashing import sha256_hex
from app.specialized_agents.executor import SpecializedExecutionRecord
from app.tenancy.isolation import BoundaryDenied


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class AnomalyRunRecord(Base):
    __tablename__ = "specialized_agent_anomaly_runs"
    __table_args__ = {"extend_existing": True}
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    metric: Mapped[str] = mapped_column(String(200), nullable=False)
    configuration: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    quality_state: Mapped[str] = mapped_column(String(32), nullable=False)
    detector: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    configuration_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    observation_count: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="completed")
    results: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class AnomalyResultRecord(Base):
    __tablename__ = "specialized_agent_anomaly_results"
    __table_args__ = (UniqueConstraint("execution_id", "result_index", name="uq_anomaly_result_index"), Index("ix_anomaly_results_scope", "tenant_id", "organization_id", "environment_id"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    result_index: Mapped[int] = mapped_column(nullable=False)
    metric: Mapped[str] = mapped_column(String(200), nullable=False)
    observation_time: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_value: Mapped[float] = mapped_column(nullable=False)
    baseline: Mapped[float | None] = mapped_column(nullable=True)
    deviation: Mapped[float | None] = mapped_column(nullable=True)
    detector: Mapped[str] = mapped_column(String(64), nullable=False)
    threshold: Mapped[float] = mapped_column(nullable=False)
    severity: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    quality_state: Mapped[str] = mapped_column(String(32), nullable=False)
    anomalous: Mapped[bool] = mapped_column(Boolean, nullable=False)
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)


class AnomalyAlertRecord(Base):
    __tablename__ = "specialized_agent_anomaly_alerts"
    __table_args__ = (UniqueConstraint("tenant_id", "environment_id", "dedupe_key", name="uq_anomaly_alert_dedupe"), Index("ix_anomaly_alert_scope_state", "tenant_id", "organization_id", "environment_id", "delivery_state"), CheckConstraint("delivery_state IN ('queued','sent','failed','suppressed')", name="ck_anomaly_alert_delivery_state"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("specialized_agent_executions.id", ondelete="CASCADE"), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False)
    result_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("specialized_agent_anomaly_results.id", ondelete="SET NULL"), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(24), nullable=False)
    delivery_state: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    outbox_event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("outbox_events.id", ondelete="SET NULL"), nullable=True)
    delivery_detail: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


async def persist_anomaly(session: AsyncSession, scope: GovernanceScope, *, execution_id: uuid.UUID, output: dict) -> AnomalyRunRecord:
    execution = await session.scalar(select(SpecializedExecutionRecord).where(SpecializedExecutionRecord.id == execution_id, SpecializedExecutionRecord.tenant_id == scope.tenant_id, SpecializedExecutionRecord.organization_id == scope.organization_id, SpecializedExecutionRecord.environment_id == scope.environment_id, SpecializedExecutionRecord.agent_type == "anomaly"))
    if execution is None:
        raise BoundaryDenied()
    existing = await session.get(AnomalyRunRecord, execution_id)
    if existing is not None:
        return existing
    results = list(output.get("results", []))
    for index, item in enumerate(results):
        observation_time = item.get("observation_time")
        if isinstance(observation_time, str):
            observation_time = dt.datetime.fromisoformat(observation_time.replace("Z", "+00:00"))
        session.add(AnomalyResultRecord(execution_id=execution_id, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, result_index=index, metric=str(item.get("metric", output.get("metric", ""))), observation_time=observation_time, observed_value=float(item.get("observed_value", 0)), baseline=item.get("baseline"), deviation=item.get("deviation"), detector=str(item.get("detector", output.get("detector", ""))), threshold=float(item.get("threshold", 0)), severity=str(item.get("severity", "info")), confidence=float(item.get("confidence", 0)), quality_state=str(item.get("quality_state", output.get("quality_state", "not_available"))), anomalous=bool(item.get("anomalous", False)), review_required=bool(item.get("review_required", False)), explanation=str(item.get("explanation", ""))))
    configuration = dict(output.get("configuration") or {})
    record = AnomalyRunRecord(execution_id=execution_id, tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, metric=str(output.get("metric", "")), configuration=configuration, quality_state=str(output.get("quality_state", "not_available")), detector=str(output.get("detector", configuration.get("detector", ""))), configuration_fingerprint=sha256_hex(configuration), observation_count=len(results), status="completed", results=results)
    session.add(record)
    await session.flush()
    return record
