"""Shared durable, governed execution pipeline for specialized agents."""

from __future__ import annotations

import datetime as dt
import inspect
import json
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, JSON, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.governance.context import GovernanceScope
from app.governance.enums import RiskAssessmentStatus
from app.governance.hashing import sha256_hex
from app.governance.model_registry import admit_model_version
from app.governance.service import capture_lineage
from app.governance.models import Base, RiskAssessment
from app.governance.policy import evaluate_policy

from .context import SpecializedAgentContext
from .enums import ExecutionStatus, ReviewState
from .evidence import emit_execution_evidence
from .exceptions import (
    AgentExecutionError,
    IdempotencyConflictError,
    InputLimitError,
    ModelAdmissionError,
    SpecializedAgentError,
    SpecializedPolicyDenied,
)
from .registry import AgentDefinition, resolve_agent
from .sources import SafeSourceContext, SourceReference, normalize_sources


MAX_EXECUTION_PAYLOAD_BYTES = 160_000
MAX_SOURCE_COUNT = 100


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class SpecializedExecutionRecord(Base):
    """Durable metadata row; raw prompts and full provider responses are omitted."""

    __tablename__ = "specialized_agent_executions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_specialized_execution_idempotency"),
        CheckConstraint(
            "status IN ('pending','admitted','running','review_required','succeeded','failed','denied','cancelled')",
            name="ck_specialized_execution_status",
        ),
        Index("ix_specialized_execution_tenant_status", "tenant_id", "organization_id", "status"),
        Index("ix_specialized_execution_request", "tenant_id", "request_id"),
        Index("ix_specialized_execution_trace", "tenant_id", "trace_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    environment_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    agent_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("governance_model_versions.id"), nullable=False, index=True
    )
    risk_tier: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=ExecutionStatus.PENDING.value, index=True)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    output_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("governance_policy_decisions.id"), nullable=True, index=True
    )
    lineage_root_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("governance_lineage_records.id"), nullable=True, index=True
    )
    evidence_root_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    review_state: Mapped[str] = mapped_column(String(24), nullable=False, default=ReviewState.NOT_REQUIRED.value)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


@dataclass(frozen=True)
class ExecutionOutcome:
    record: SpecializedExecutionRecord
    output: dict[str, Any]
    evidence: Any | None


Handler = Callable[[SpecializedAgentContext, dict[str, Any], SafeSourceContext], Any | Awaitable[Any]]
SourceResolver = Callable[[SpecializedAgentContext, dict[str, Any]], Any | Awaitable[Any]]


def _safe_storage_result(output: dict[str, Any]) -> dict[str, Any]:
    """Keep durable metadata useful without storing raw prompts or responses."""
    sensitive_keys = {
        "prompt",
        "response",
        "source_text",
        "translated_text",
        "text",
        "content",
        "raw_output",
        "statement",
    }

    def scrub(value: Any, key: str | None = None) -> Any:
        if key in sensitive_keys:
            return "[redacted]"
        if isinstance(value, dict):
            return {str(k): scrub(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, tuple):
            return [scrub(item) for item in value]
        return value

    return scrub(output)


class SpecializedExecutor:
    def __init__(self, session: AsyncSession, scope: GovernanceScope):
        self.session = session
        self.scope = scope

    async def _risk_admission(self, context: SpecializedAgentContext) -> RiskAssessment:
        row = await self.session.scalar(
            select(RiskAssessment)
            .where(
                RiskAssessment.tenant_id == context.tenant_id,
                RiskAssessment.organization_id == context.organization_id,
                RiskAssessment.environment_id == context.environment_id,
                RiskAssessment.subject_type == "specialized_agent",
                RiskAssessment.subject_id == context.agent_type,
                RiskAssessment.status == RiskAssessmentStatus.APPROVED.value,
                RiskAssessment.tier == context.risk_tier,
            )
            .order_by(RiskAssessment.version.desc())
            .limit(1)
        )
        if row is None:
            raise SpecializedPolicyDenied("Approved specialized-agent risk assessment is required")
        return row

    async def _persist_terminal_evidence(
        self,
        row: SpecializedExecutionRecord,
        context: SpecializedAgentContext,
        *,
        status: str,
        output: dict[str, Any] | None,
        source_context: SafeSourceContext,
        policy_decision_id: uuid.UUID | None,
        lineage_root_id: uuid.UUID | None,
        review_required: bool,
        review_state: str,
    ) -> Any:
        output_fingerprint = sha256_hex(output) if output is not None else None
        row.output_fingerprint = output_fingerprint
        row.policy_decision_id = policy_decision_id
        row.lineage_root_id = lineage_root_id
        row.status = status
        row.review_required = review_required
        row.review_state = review_state
        row.evidence_root_hash = None
        event_reference = await emit_execution_evidence(
            self.session,
            self.scope,
            context={
                "execution_id": str(row.id),
                "agent_type": row.agent_type,
                "agent_version": row.agent_version,
                "model_version_id": str(row.model_version_id),
                "request_id": row.request_id,
                "trace_id": row.trace_id,
            },
            status=status,
            input_fingerprint=row.input_fingerprint,
            output_fingerprint=output_fingerprint,
            policy_decision_id=policy_decision_id,
            lineage_root_id=lineage_root_id,
            review_required=review_required,
            review_state=review_state,
            source_fingerprints=list(source_context.fingerprints),
            actor_user_id=context.actor_id,
        )
        row.evidence_root_hash = event_reference.event_hash
        return event_reference

    async def _failure_lineage(
        self,
        context: SpecializedAgentContext,
        *,
        input_fingerprint: str,
        source_context: SafeSourceContext,
        policy_decision_id: uuid.UUID | None,
        status: str,
    ) -> uuid.UUID | None:
        try:
            lineage = await capture_lineage(
                self.session,
                self.scope,
                correlation_id=context.trace_id,
                trace_id=context.trace_id,
                request_id=context.request_id,
                input_payload={"fingerprint": input_fingerprint, "agent_type": context.agent_type},
                output_payload=None,
                tool_fingerprints=[],
                source_references=[reference.key() for reference in source_context.references],
                model_registry_id=None,
                model_version_id=context.model_version_id,
                decision_id=policy_decision_id,
                source_type="specialized_agent",
                source_id=context.agent_type,
                metadata={"terminal_status": status, "failure_lineage": True},
                actor_user_id=context.actor_id,
            )
            return lineage.id
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            return None

    async def execute(
        self,
        context: SpecializedAgentContext,
        *,
        idempotency_key: str,
        payload: dict[str, Any],
        source_references: list[SourceReference | dict[str, Any]] | tuple[SourceReference, ...] = (),
        handler: Handler,
        policy_context: dict[str, Any] | None = None,
        source_resolver: SourceResolver | None = None,
    ) -> ExecutionOutcome:
        definition: AgentDefinition = resolve_agent(context.agent_type)
        if (
            context.tenant_id != self.scope.tenant_id
            or context.organization_id != self.scope.organization_id
            or context.environment_id != self.scope.environment_id
        ):
            raise SpecializedPolicyDenied("Execution context does not match the authorized governance scope")
        if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 200:
            raise IdempotencyConflictError("A valid 8-200 character idempotency key is required")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        if len(serialized.encode("utf-8")) > MAX_EXECUTION_PAYLOAD_BYTES:
            raise InputLimitError("specialized-agent payload exceeds the configured limit")
        source_context = normalize_sources(source_references)
        if len(source_context.references) > MAX_SOURCE_COUNT:
            raise InputLimitError("source count exceeds the configured limit")
        input_fingerprint = sha256_hex(
            {"payload": payload, "sources": source_context.as_dicts(), "agent": context.agent_type}
        )
        existing = await self.session.scalar(
            select(SpecializedExecutionRecord).where(
                SpecializedExecutionRecord.tenant_id == context.tenant_id,
                SpecializedExecutionRecord.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if (
                existing.organization_id != context.organization_id
                or existing.environment_id != context.environment_id
                or existing.input_fingerprint != input_fingerprint
                or existing.agent_type != definition.type
            ):
                raise IdempotencyConflictError("Idempotency key was already used for another scope or input")
            return ExecutionOutcome(existing, existing.result or {}, None)

        row = SpecializedExecutionRecord(
            tenant_id=context.tenant_id,
            organization_id=context.organization_id,
            environment_id=context.environment_id,
            request_id=context.request_id,
            trace_id=context.trace_id,
            idempotency_key=idempotency_key,
            agent_type=definition.type,
            agent_version=context.agent_version,
            model_version_id=context.model_version_id,
            risk_tier=context.risk_tier,
            status=ExecutionStatus.PENDING.value,
            input_fingerprint=input_fingerprint,
            result={},
        )
        self.session.add(row)
        await self.session.flush()
        policy_decision_id: uuid.UUID | None = None
        lineage_root_id: uuid.UUID | None = None
        try:
            try:
                await admit_model_version(self.session, self.scope, context.model_version_id, channel=definition.type)
            except Exception as exc:
                raise ModelAdmissionError("Approved active model version is required") from exc
            await self._risk_admission(context)
            row.status = ExecutionStatus.ADMITTED.value
            decision = await evaluate_policy(
                self.session,
                self.scope,
                policy_type="specialized_agent",
                context={
                    **(policy_context or {}),
                    **context.governance_payload(input_fingerprint=input_fingerprint, source_count=len(source_context.references)),
                    "agent_type": context.agent_type,
                    "risk_tier": context.risk_tier,
                    "approved_model_only": True,
                },
                principal_id=context.actor_id,
                correlation_id=context.trace_id,
            )
            policy_decision_id = decision.id
            row.policy_decision_id = decision.id
            if decision.decision != "allow":
                raise SpecializedPolicyDenied(decision.reason_code)
            if source_resolver is not None:
                resolved = source_resolver(context, payload)
                resolved = await resolved if inspect.isawaitable(resolved) else resolved
                resolved_context = normalize_sources(resolved or ())
                source_context = normalize_sources((*source_context.references, *resolved_context.references))
                if len(source_context.references) > MAX_SOURCE_COUNT:
                    raise InputLimitError("resolved source count exceeds the configured limit")
            row.status = ExecutionStatus.RUNNING.value
            row.started_at = _utcnow()
            await self.session.flush()
            produced = handler(context, payload, source_context)
            if inspect.isawaitable(produced):
                produced = await produced
            if not isinstance(produced, dict):
                raise AgentExecutionError("Specialized agent returned an invalid output")
            output = dict(produced)
            proposed_review_state = str(output.get("review_state", ReviewState.NOT_REQUIRED.value))
            if proposed_review_state in {
                ReviewState.COMPLETED.value,
                ReviewState.APPROVED.value,
                ReviewState.REJECTED.value,
            }:
                raise AgentExecutionError("Agent output cannot assert a human review decision")
            review_required = (
                bool(output.get("review_required", False))
                or proposed_review_state in {ReviewState.REQUIRED.value, ReviewState.PENDING.value}
                or any(control in definition.required_controls for control in ("human_review", "human_approval"))
            )
            review_state = ReviewState.REQUIRED.value if review_required else ReviewState.NOT_REQUIRED.value
            output["review_required"] = review_required
            output["review_state"] = review_state
            if review_required:
                status = ExecutionStatus.REVIEW_REQUIRED.value
            else:
                status = ExecutionStatus.SUCCEEDED.value
            lineage = await capture_lineage(
                self.session,
                self.scope,
                correlation_id=context.trace_id,
                trace_id=context.trace_id,
                request_id=context.request_id,
                input_payload={"fingerprint": input_fingerprint, "agent_type": context.agent_type},
                output_payload={"fingerprint": sha256_hex(output)},
                tool_fingerprints=[],
                source_references=[reference.key() for reference in source_context.references],
                model_registry_id=None,
                model_version_id=context.model_version_id,
                decision_id=policy_decision_id,
                source_type="specialized_agent",
                source_id=definition.id,
                metadata={
                    "agent_type": context.agent_type,
                    "agent_version": context.agent_version,
                    "policy_decision_id": str(policy_decision_id),
                    "review_required": review_required,
                },
                actor_user_id=context.actor_id,
            )
            lineage_root_id = lineage.id
            evidence = await self._persist_terminal_evidence(
                row,
                context,
                status=status,
                output=output,
                source_context=source_context,
                policy_decision_id=policy_decision_id,
                lineage_root_id=lineage_root_id,
                review_required=review_required,
                review_state=review_state,
            )
            row.result = _safe_storage_result(output)
            if review_required and not output.get("review_cases_persisted"):
                from app.review.service import create_case

                review_case = await create_case(
                    self.session,
                    self.scope,
                    actor_user_id=context.actor_id,
                    case_type="legal" if context.agent_type in {"legal", "intake_flow", "virtual_paralegal"} else context.agent_type if context.agent_type in {"translation", "insight", "forecast", "anomaly"} else "specialized_execution",
                    agent_type=context.agent_type if context.agent_type in {"legal", "translation", "insight", "forecast", "forecasting", "anomaly", "intake_flow", "virtual_paralegal", "billing_guard", "billing_ops", "ocg_compliance", "qms_compliance", "healthcare", "manufacturing", "retail", "metrics_insights"} else "specialized_execution",
                    execution_id=row.id,
                    reason=str(output.get("review_reason") or "Governed specialized-agent output requires human review")[:2000],
                    requested_controls=list(output.get("requested_controls") or definition.required_controls),
                    metadata={"review_state": review_state, "agent_type": context.agent_type},
                )
                row.result = {**row.result, "review_case_id": str(review_case.id)}
            row.completed_at = _utcnow()
            await self.session.commit()
            await self.session.refresh(row)
            return ExecutionOutcome(row, output, evidence)
        except SpecializedAgentError as exc:
            if not self.session.is_active:
                await self.session.rollback()
                row = SpecializedExecutionRecord(
                    id=row.id,
                    tenant_id=context.tenant_id,
                    organization_id=context.organization_id,
                    environment_id=context.environment_id,
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    idempotency_key=idempotency_key,
                    agent_type=definition.type,
                    agent_version=context.agent_version,
                    model_version_id=context.model_version_id,
                    risk_tier=context.risk_tier,
                    status=ExecutionStatus.PENDING.value,
                    input_fingerprint=input_fingerprint,
                    result={},
                )
                self.session.add(row)
            row.failure_code = exc.code
            row.result = {"error_code": exc.code, "message": str(exc)}
            row.completed_at = _utcnow()
            terminal_status = ExecutionStatus.DENIED.value if exc.code in {
                "policy_denied",
                "model_admission_denied",
                "unknown_agent",
            } else ExecutionStatus.FAILED.value
            try:
                if lineage_root_id is None:
                    lineage_root_id = await self._failure_lineage(
                        context,
                        input_fingerprint=input_fingerprint,
                        source_context=source_context,
                        policy_decision_id=policy_decision_id,
                        status=terminal_status,
                    )
                evidence = await self._persist_terminal_evidence(
                    row,
                    context,
                    status=terminal_status,
                    output=None,
                    source_context=source_context,
                    policy_decision_id=policy_decision_id,
                    lineage_root_id=lineage_root_id,
                    review_required=False,
                    review_state=ReviewState.NOT_REQUIRED.value,
                )
                row.failure_code = exc.code
                row.result = {"error_code": exc.code}
                await self.session.commit()
            except Exception:
                await self.session.rollback()
                raise exc
            raise
        except Exception as exc:
            row_id = row.id
            await self.session.rollback()
            row = SpecializedExecutionRecord(
                id=row_id,
                tenant_id=context.tenant_id,
                organization_id=context.organization_id,
                environment_id=context.environment_id,
                request_id=context.request_id,
                trace_id=context.trace_id,
                idempotency_key=idempotency_key,
                agent_type=definition.type,
                agent_version=context.agent_version,
                model_version_id=context.model_version_id,
                risk_tier=context.risk_tier,
                status=ExecutionStatus.PENDING.value,
                input_fingerprint=input_fingerprint,
                result={},
            )
            self.session.add(row)
            row.failure_code = "provider_failure" if not isinstance(exc, AgentExecutionError) else exc.code
            row.result = {"error_code": row.failure_code}
            row.completed_at = _utcnow()
            try:
                lineage_root_id = await self._failure_lineage(
                    context,
                    input_fingerprint=input_fingerprint,
                    source_context=source_context,
                    policy_decision_id=policy_decision_id,
                    status=ExecutionStatus.FAILED.value,
                )
                await self._persist_terminal_evidence(
                    row,
                    context,
                    status=ExecutionStatus.FAILED.value,
                    output=None,
                    source_context=source_context,
                    policy_decision_id=policy_decision_id,
                    lineage_root_id=lineage_root_id,
                    review_required=False,
                    review_state=ReviewState.NOT_REQUIRED.value,
                )
                await self.session.commit()
            except Exception:
                await self.session.rollback()
            raise AgentExecutionError("Specialized agent execution failed") from exc
