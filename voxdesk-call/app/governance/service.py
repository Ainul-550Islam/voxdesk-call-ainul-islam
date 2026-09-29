"""Application services for policy, risk, model, lineage, and retention state."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.auth.identity.events import scrub

from .context import GovernanceScope
from .enums import (
    DecisionType,
    GovernancePolicyStatus,
    ModelEvaluationStatus,
    ModelRegistryStatus,
    ModelVersionStatus,
    RiskAssessmentStatus,
    RiskTier,
    MODEL_REGISTRY_TRANSITIONS,
    MODEL_VERSION_TRANSITIONS,
)
from .evidence import append_event
from .exceptions import (
    GovernanceConflict,
    GovernanceNotFound,
    GovernanceValidation,
    InvalidTransition,
    MissingGovernanceConfiguration,
    PolicyDenied,
    VerificationRequired,
)
from .hashing import sha256_hex
from .risk import required_controls
from .models import (
    GovernancePolicy,
    GovernancePolicyDecision,
    LineageRecord,
    ModelRegistry,
    ModelVersion,
    RetentionRule,
    RiskAssessment,
)

_HEX_DIGEST = re.compile(r"^[0-9a-fA-F]{32,128}$")


def _enum_value(enum_type, value: str, field: str) -> str:
    try:
        return enum_type(value).value
    except (TypeError, ValueError) as exc:
        raise GovernanceValidation(f"Invalid {field}") from exc


def _ensure_nonempty(value: str, field: str) -> str:
    value = (value or "").strip()
    if not value:
        raise GovernanceValidation(f"{field} is required")
    return value


async def create_policy(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    name: str,
    policy_type: str,
    rules: dict[str, Any],
    rationale: str | None,
    actor_user_id: uuid.UUID,
    effective_from: dt.datetime | None = None,
    effective_to: dt.datetime | None = None,
) -> GovernancePolicy:
    policy_type = _ensure_nonempty(policy_type, "policy_type")
    name = _ensure_nonempty(name, "name")
    existing = await session.scalar(
        select(func.max(GovernancePolicy.version)).where(
            GovernancePolicy.tenant_id == scope.tenant_id,
            GovernancePolicy.name == name,
            GovernancePolicy.policy_type == policy_type,
        )
    )
    row = GovernancePolicy(
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        environment_id=scope.environment_id,
        name=name,
        policy_type=policy_type,
        status=GovernancePolicyStatus.DRAFT.value,
        version=(existing or 0) + 1,
        rules=rules,
        rationale=rationale,
        created_by=actor_user_id,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    session.add(row)
    await session.flush()
    await append_event(
        session,
        scope,
        event_type="policy_created",
        payload={"policy_id": str(row.id), "policy_type": policy_type, "version": row.version},
        actor_user_id=actor_user_id,
    )
    return row


async def list_policies(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    limit: int = 100,
) -> list[GovernancePolicy]:
    result = await session.execute(
        select(GovernancePolicy)
        .where(
            GovernancePolicy.tenant_id == scope.tenant_id,
            GovernancePolicy.organization_id == scope.organization_id,
        )
        .order_by(GovernancePolicy.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars())


async def publish_policy(
    session: AsyncSession,
    scope: GovernanceScope,
    policy_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
) -> GovernancePolicy:
    row = await session.scalar(
        select(GovernancePolicy).where(
            GovernancePolicy.id == policy_id,
            GovernancePolicy.tenant_id == scope.tenant_id,
            GovernancePolicy.organization_id == scope.organization_id,
        )
    )
    if row is None:
        raise GovernanceNotFound()
    if row.status != GovernancePolicyStatus.DRAFT.value:
        raise InvalidTransition("Only a draft policy can be published")
    row.status = GovernancePolicyStatus.PUBLISHED.value
    row.published_by = actor_user_id
    row.published_at = dt.datetime.now(dt.timezone.utc)
    await session.flush()
    await append_event(
        session,
        scope,
        event_type="policy_published",
        payload={"policy_id": str(row.id), "policy_type": row.policy_type, "version": row.version},
        actor_user_id=actor_user_id,
    )
    return row


async def retire_policy(
    session: AsyncSession,
    scope: GovernanceScope,
    policy_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
) -> GovernancePolicy:
    row = await session.scalar(
        select(GovernancePolicy).where(
            GovernancePolicy.id == policy_id,
            GovernancePolicy.tenant_id == scope.tenant_id,
            GovernancePolicy.organization_id == scope.organization_id,
        )
    )
    if row is None:
        raise GovernanceNotFound()
    if row.status != GovernancePolicyStatus.PUBLISHED.value:
        raise InvalidTransition("Only a published policy can be retired")
    row.status = GovernancePolicyStatus.RETIRED.value
    await session.flush()
    await append_event(
        session,
        scope,
        event_type="policy_retired",
        payload={"policy_id": str(row.id), "version": row.version},
        actor_user_id=actor_user_id,
    )
    return row


async def _published_policy(
    session: AsyncSession, scope: GovernanceScope, policy_type: str
) -> GovernancePolicy | None:
    candidates = (
        await session.execute(
            select(GovernancePolicy)
            .where(
                GovernancePolicy.policy_type == policy_type,
                GovernancePolicy.status == GovernancePolicyStatus.PUBLISHED.value,
                or_(
                    and_(
                        GovernancePolicy.tenant_id == scope.tenant_id,
                        GovernancePolicy.environment_id == scope.environment_id,
                    ),
                    and_(
                        GovernancePolicy.tenant_id == scope.tenant_id,
                        GovernancePolicy.environment_id.is_(None),
                    ),
                    and_(
                        GovernancePolicy.tenant_id.is_(None),
                        GovernancePolicy.organization_id == scope.organization_id,
                    ),
                ),
            )
            .order_by(
                GovernancePolicy.environment_id.is_not(None).desc(),
                GovernancePolicy.tenant_id.is_not(None).desc(),
                GovernancePolicy.version.desc(),
            )
            .limit(1)
        )
    ).scalars().first()
    return candidates


def _policy_result(policy: GovernancePolicy | None, input_payload: dict[str, Any]) -> tuple[str, str]:
    if policy is None:
        return DecisionType.DENY.value, "governance_configuration_missing"
    from .policy import evaluate_rules

    result = evaluate_rules(policy.rules or {}, input_payload)
    return result.decision, result.reason_code[:100]


async def decide(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    policy_type: str,
    input_payload: dict[str, Any],
    principal_id: uuid.UUID | None,
    correlation_id: str | None,
) -> GovernancePolicyDecision:
    policy = await _published_policy(session, scope, _ensure_nonempty(policy_type, "policy_type"))
    decision, reason = _policy_result(policy, input_payload)
    row = GovernancePolicyDecision(
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        environment_id=scope.environment_id,
        principal_id=principal_id,
        policy_id=policy.id if policy else None,
        policy_version=policy.version if policy else None,
        decision=decision,
        reason_code=reason,
        input_fingerprint=sha256_hex(input_payload),
        correlation_id=correlation_id,
        request_metadata={"policy_type": policy_type},
    )
    session.add(row)
    await session.flush()
    await append_event(
        session,
        scope,
        event_type="policy_decision",
        payload={
            "decision_id": str(row.id),
            "decision": decision,
            "reason_code": reason,
            "input_fingerprint": row.input_fingerprint,
            "policy_id": str(policy.id) if policy else None,
        },
        actor_user_id=principal_id,
        correlation_id=correlation_id,
    )
    return row


async def require_decision_allowed(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    policy_type: str,
    input_payload: dict[str, Any],
    principal_id: uuid.UUID | None,
    correlation_id: str | None,
) -> GovernancePolicyDecision:
    row = await decide(
        session,
        scope,
        policy_type=policy_type,
        input_payload=input_payload,
        principal_id=principal_id,
        correlation_id=correlation_id,
    )
    if row.decision == DecisionType.DENY.value:
        if row.reason_code == "governance_configuration_missing":
            raise MissingGovernanceConfiguration(row.reason_code)
        raise PolicyDenied(row.reason_code)
    if row.decision == DecisionType.CONDITION.value:
        raise PolicyDenied(row.reason_code)
    return row


async def create_risk_assessment(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    subject_type: str,
    subject_id: str,
    tier: str,
    factors: dict[str, Any],
    rationale: str,
    actor_user_id: uuid.UUID,
    explicit_required_controls: list[str] | None = None,
) -> RiskAssessment:
    tier = _enum_value(RiskTier, tier, "risk tier")
    current = await session.scalar(
        select(func.max(RiskAssessment.version)).where(
            RiskAssessment.tenant_id == scope.tenant_id,
            RiskAssessment.subject_type == subject_type,
            RiskAssessment.subject_id == subject_id,
        )
    )
    row = RiskAssessment(
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        environment_id=scope.environment_id,
        subject_type=_ensure_nonempty(subject_type, "subject_type"),
        subject_id=_ensure_nonempty(subject_id, "subject_id"),
        version=(current or 0) + 1,
        tier=tier,
        status=RiskAssessmentStatus.PENDING_APPROVAL.value,
        factors=factors,
        required_controls=required_controls(
            tier,
            extra_controls=explicit_required_controls or factors.get("extra_controls", []),
        ),
        rationale=_ensure_nonempty(rationale, "rationale"),
        assessed_by=actor_user_id,
    )
    session.add(row)
    await session.flush()
    await append_event(
        session,
        scope,
        event_type="risk_assessed",
        payload={"risk_id": str(row.id), "tier": tier, "version": row.version},
        actor_user_id=actor_user_id,
    )
    return row


async def approve_risk(
    session: AsyncSession,
    scope: GovernanceScope,
    risk_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
) -> RiskAssessment:
    row = await session.scalar(
        select(RiskAssessment).where(
            RiskAssessment.id == risk_id,
            RiskAssessment.tenant_id == scope.tenant_id,
            RiskAssessment.organization_id == scope.organization_id,
        )
    )
    if row is None:
        raise GovernanceNotFound()
    if row.status != RiskAssessmentStatus.PENDING_APPROVAL.value:
        raise InvalidTransition("Risk assessment is not awaiting approval")
    row.status = RiskAssessmentStatus.APPROVED.value
    row.approved_by = actor_user_id
    row.approved_at = dt.datetime.now(dt.timezone.utc)
    await session.flush()
    await append_event(
        session,
        scope,
        event_type="risk_approved",
        payload={"risk_id": str(row.id), "tier": row.tier, "version": row.version},
        actor_user_id=actor_user_id,
    )
    return row


async def create_registry(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    provider: str,
    model_name: str,
    display_name: str,
    risk_tier: str,
    intended_use: str,
    data_classes: list[str],
    capabilities: list[str],
    metadata: dict[str, Any],
    actor_user_id: uuid.UUID,
    family: str | None = None,
    purpose: str | None = None,
    approved_for_channels: list[str] | None = None,
    approved_for_environments: list[str] | None = None,
) -> ModelRegistry:
    risk_tier = _enum_value(RiskTier, risk_tier, "risk tier")
    row = ModelRegistry(
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        provider=_ensure_nonempty(provider, "provider"),
        model_name=_ensure_nonempty(model_name, "model_name"),
        display_name=_ensure_nonempty(display_name, "display_name"),
        family=family,
        purpose=purpose or intended_use,
        risk_tier=risk_tier,
        intended_use=_ensure_nonempty(intended_use, "intended_use"),
        data_classes=data_classes,
        capabilities=capabilities,
        approved_for_channels=approved_for_channels or [],
        approved_for_environments=approved_for_environments or [],
        metadata_json=metadata,
        owner_user_id=actor_user_id,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise GovernanceConflict("Model is already registered in this tenant") from exc
    await append_event(
        session,
        scope,
        event_type="model_registered",
        payload={"registry_id": str(row.id), "provider": row.provider, "model_name": row.model_name},
        actor_user_id=actor_user_id,
    )
    return row


async def list_registries(session: AsyncSession, scope: GovernanceScope) -> list[ModelRegistry]:
    return list(
        (
            await session.execute(
                select(ModelRegistry)
                .where(
                    or_(
                        ModelRegistry.tenant_id == scope.tenant_id,
                        and_(
                            ModelRegistry.tenant_id.is_(None),
                            ModelRegistry.organization_id == scope.organization_id,
                        ),
                    )
                )
                .order_by(ModelRegistry.created_at.desc())
            )
        ).scalars()
    )


async def add_model_version(
    session: AsyncSession,
    scope: GovernanceScope,
    registry_id: uuid.UUID,
    *,
    version: str,
    artifact_uri: str,
    artifact_digest: str,
    evaluation_summary: dict[str, Any],
    actor_user_id: uuid.UUID,
    fingerprint: str | None = None,
    capabilities: list[str] | None = None,
    input_modalities: list[str] | None = None,
    output_modalities: list[str] | None = None,
) -> ModelVersion:
    registry = await session.scalar(
        select(ModelRegistry).where(
            ModelRegistry.id == registry_id,
            ModelRegistry.tenant_id == scope.tenant_id,
            ModelRegistry.organization_id == scope.organization_id,
        )
    )
    if registry is None:
        raise GovernanceNotFound()
    if not _HEX_DIGEST.match(artifact_digest):
        raise GovernanceValidation("artifact_digest must be a hexadecimal digest")
    row = ModelVersion(
        registry_id=registry.id,
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        provider=registry.provider,
        model_name=registry.model_name,
        version=_ensure_nonempty(version, "version"),
        fingerprint=(fingerprint or artifact_digest).lower(),
        artifact_uri=_ensure_nonempty(artifact_uri, "artifact_uri"),
        artifact_digest=artifact_digest.lower(),
        capabilities=capabilities or registry.capabilities or [],
        input_modalities=input_modalities or [],
        output_modalities=output_modalities or [],
        evaluation_summary=evaluation_summary,
        created_by=actor_user_id,
    )
    session.add(row)
    await session.flush()
    await append_event(
        session,
        scope,
        event_type="model_version_registered",
        payload={"registry_id": str(registry.id), "version_id": str(row.id), "version": row.version},
        actor_user_id=actor_user_id,
    )
    return row


async def record_model_evaluation(
    session: AsyncSession,
    scope: GovernanceScope,
    version_id: uuid.UUID,
    *,
    verifier: str,
    verification_reference: str,
    passed: bool,
    actor_user_id: uuid.UUID,
) -> ModelVersion:
    row = await session.scalar(
        select(ModelVersion).where(
            ModelVersion.id == version_id,
            ModelVersion.tenant_id == scope.tenant_id,
            ModelVersion.organization_id == scope.organization_id,
        )
    )
    if row is None:
        raise GovernanceNotFound()
    if not _ensure_nonempty(verifier, "verifier") or not _ensure_nonempty(
        verification_reference, "verification_reference"
    ):
        raise VerificationRequired("An authoritative evaluation reference is required")
    row.evaluation_status = (
        ModelEvaluationStatus.PASSED.value if passed else ModelEvaluationStatus.FAILED.value
    )
    row.evaluation_summary = {
        **(row.evaluation_summary or {}),
        "authoritative_verifier": verifier,
        "verification_reference": verification_reference,
        "passed": passed,
    }
    if row.status == ModelVersionStatus.REGISTERED.value:
        row.status = ModelVersionStatus.SUBMITTED.value
    await session.flush()
    await append_event(
        session,
        scope,
        event_type="model_evaluation_recorded",
        payload={
            "version_id": str(row.id),
            "evaluation_status": row.evaluation_status,
            "authoritative_verifier": verifier,
            "verification_reference": verification_reference,
        },
        actor_user_id=actor_user_id,
    )
    return row


async def approve_model_version(
    session: AsyncSession,
    scope: GovernanceScope,
    version_id: uuid.UUID,
    *,
    approval_reference: str,
    actor_user_id: uuid.UUID,
) -> ModelVersion:
    row = await session.scalar(
        select(ModelVersion).where(
            ModelVersion.id == version_id,
            ModelVersion.tenant_id == scope.tenant_id,
            ModelVersion.organization_id == scope.organization_id,
        )
    )
    if row is None:
        raise GovernanceNotFound()
    if row.evaluation_status != ModelEvaluationStatus.PASSED.value or not (
        row.evaluation_summary or {}
    ).get("authoritative_verifier"):
        raise VerificationRequired("An authoritative passed evaluation is required")
    if row.status != ModelVersionStatus.SUBMITTED.value:
        raise InvalidTransition("Model version is not awaiting approval")
    row.status = ModelVersionStatus.APPROVED.value
    row.approval_reference = _ensure_nonempty(approval_reference, "approval_reference")
    row.approved_by = actor_user_id
    row.approved_at = dt.datetime.now(dt.timezone.utc)
    await session.flush()
    await append_event(
        session,
        scope,
        event_type="model_approved",
        payload={"version_id": str(row.id), "approval_reference": row.approval_reference},
        actor_user_id=actor_user_id,
    )
    return row


async def transition_registry(
    session: AsyncSession,
    scope: GovernanceScope,
    registry_id: uuid.UUID,
    target: str,
    *,
    actor_user_id: uuid.UUID,
) -> ModelRegistry:
    target = _enum_value(ModelRegistryStatus, target, "registry status")
    row = await session.scalar(
        select(ModelRegistry).where(
            ModelRegistry.id == registry_id,
            ModelRegistry.tenant_id == scope.tenant_id,
            ModelRegistry.organization_id == scope.organization_id,
        )
    )
    if row is None:
        raise GovernanceNotFound()
    current = ModelRegistryStatus(row.status)
    target_status = ModelRegistryStatus(target)
    if target_status not in MODEL_REGISTRY_TRANSITIONS[current]:
        raise InvalidTransition(f"Cannot transition model registry from {current.value} to {target}")
    if target_status is ModelRegistryStatus.ACTIVE:
        approved = await session.scalar(
            select(ModelVersion.id).where(
                ModelVersion.registry_id == row.id,
                ModelVersion.tenant_id == scope.tenant_id,
                ModelVersion.organization_id == scope.organization_id,
                ModelVersion.status == ModelVersionStatus.APPROVED.value,
            ).limit(1)
        )
        if approved is None:
            raise VerificationRequired("An approved model version is required before activation")
    row.status = target
    await session.flush()
    await append_event(
        session,
        scope,
        event_type=f"model_{target}",
        payload={"registry_id": str(row.id), "from": current.value, "to": target},
        actor_user_id=actor_user_id,
    )
    return row


async def transition_model_version(
    session: AsyncSession,
    scope: GovernanceScope,
    version_id: uuid.UUID,
    target: str,
    *,
    actor_user_id: uuid.UUID,
) -> ModelVersion:
    target = _enum_value(ModelVersionStatus, target, "model version status")
    row = await session.scalar(
        select(ModelVersion).where(
            ModelVersion.id == version_id,
            ModelVersion.tenant_id == scope.tenant_id,
            ModelVersion.organization_id == scope.organization_id,
        )
    )
    if row is None:
        raise GovernanceNotFound()
    current = ModelVersionStatus(row.status)
    if ModelVersionStatus(target) not in MODEL_VERSION_TRANSITIONS[current]:
        raise InvalidTransition(f"Cannot transition model version from {current.value} to {target}")
    row.status = target
    if target == ModelVersionStatus.RETIRED.value:
        row.retired_at = dt.datetime.now(dt.timezone.utc)
    await session.flush()
    await append_event(
        session,
        scope,
        event_type=f"model_version_{target}",
        payload={"version_id": str(row.id), "from": current.value, "to": target},
        actor_user_id=actor_user_id,
    )
    return row


async def capture_lineage(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    correlation_id: str,
    input_payload: Any,
    output_payload: Any | None,
    tool_fingerprints: list[str],
    source_references: list[str],
    trace_id: str | None = None,
    request_id: str | None = None,
    subject_type: str | None = None,
    subject_id: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    parent_lineage_id: uuid.UUID | None = None,
    tool_name: str | None = None,
    model_registry_id: uuid.UUID | None,
    model_version_id: uuid.UUID | None,
    decision_id: uuid.UUID | None,
    metadata: dict[str, Any],
    actor_user_id: uuid.UUID,
) -> LineageRecord:
    if model_version_id is not None:
        version = await session.scalar(
            select(ModelVersion).where(
                ModelVersion.id == model_version_id,
                ModelVersion.tenant_id == scope.tenant_id,
                ModelVersion.organization_id == scope.organization_id,
            )
        )
        if version is None:
            raise GovernanceNotFound()
        if model_registry_id is not None and version.registry_id != model_registry_id:
            raise GovernanceValidation("Model registry and version do not match")
        model_registry_id = version.registry_id
    row = LineageRecord(
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        environment_id=scope.environment_id,
        correlation_id=_ensure_nonempty(correlation_id, "correlation_id"),
        trace_id=trace_id,
        request_id=request_id,
        subject_type=subject_type,
        subject_id=subject_id,
        source_type=source_type,
        source_id=source_id,
        parent_lineage_id=parent_lineage_id,
        tool_name=tool_name,
        model_registry_id=model_registry_id,
        model_version_id=model_version_id,
        input_fingerprint=sha256_hex(input_payload),
        output_fingerprint=sha256_hex(output_payload) if output_payload is not None else None,
        tool_fingerprints=tool_fingerprints,
        source_references=source_references,
        decision_id=decision_id,
        metadata_json=scrub(metadata),
    )
    session.add(row)
    await session.flush()
    await append_event(
        session,
        scope,
        event_type="lineage_captured",
        payload={
            "lineage_id": str(row.id),
            "correlation_id": row.correlation_id,
            "input_fingerprint": row.input_fingerprint,
            "output_fingerprint": row.output_fingerprint,
        },
        actor_user_id=actor_user_id,
        correlation_id=correlation_id,
    )
    return row


async def create_retention_rule(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    evidence_type: str,
    retention_days: int,
    rationale: str,
    legal_hold: bool,
    actor_user_id: uuid.UUID,
) -> RetentionRule:
    current = await session.scalar(
        select(func.max(RetentionRule.version)).where(
            RetentionRule.tenant_id == scope.tenant_id,
            RetentionRule.evidence_type == evidence_type,
        )
    )
    await session.execute(
        RetentionRule.__table__.update()
        .where(
            RetentionRule.tenant_id == scope.tenant_id,
            RetentionRule.evidence_type == evidence_type,
            RetentionRule.active.is_(True),
        )
        .values(active=False)
    )
    row = RetentionRule(
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        evidence_type=_ensure_nonempty(evidence_type, "evidence_type"),
        retention_days=retention_days,
        version=(current or 0) + 1,
        legal_hold=legal_hold,
        rationale=_ensure_nonempty(rationale, "rationale"),
        created_by=actor_user_id,
    )
    session.add(row)
    await session.flush()
    await append_event(
        session,
        scope,
        event_type="retention_changed",
        payload={
            "retention_rule_id": str(row.id),
            "evidence_type": row.evidence_type,
            "retention_days": row.retention_days,
            "legal_hold": row.legal_hold,
            "version": row.version,
        },
        actor_user_id=actor_user_id,
    )
    return row
