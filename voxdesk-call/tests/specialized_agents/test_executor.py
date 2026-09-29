"""Behavioral tests for the governed specialized-agent executor."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import Environment, Organization, Tenant, UserRole
from app.governance.context import GovernanceScope
from app.governance.models import (
    GovernancePolicy,
    GovernancePolicyDecision,
    LineageRecord,
    ModelRegistry,
    ModelVersion,
    RiskAssessment,
    EvidenceEvent,
)
from app.specialized_agents.exceptions import ModelAdmissionError, SpecializedPolicyDenied
from app.specialized_agents.executor import SpecializedExecutionRecord
from app.specialized_agents.service import SpecializedAgentService
from tests.conftest import make_user, make_tenant


async def _scope_and_user(db, name: str):
    tenant = await make_tenant(db, name)
    user = await make_user(db, tenant, UserRole.OWNER)
    organization = await db.get(Organization, tenant.organization_id)
    environment = await db.scalar(
        select(Environment).where(
            Environment.tenant_id == tenant.id,
            Environment.kind == "production",
        )
    )
    assert environment is not None
    await db.commit()
    return tenant, organization, environment, user, GovernanceScope(tenant, organization, environment)


async def _admission_rows(db, scope, user, *, status="approved", policy_decision="allow"):
    registry = ModelRegistry(
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        provider=f"test-provider-{uuid.uuid4().hex[:8]}",
        model_name=f"test-model-{uuid.uuid4().hex[:8]}",
        display_name="Test Model",
        status="active",
        risk_tier="moderate",
        intended_use="specialized tests",
        data_classes=[],
        capabilities=["anomaly"],
        approved_for_channels=["anomaly"],
        approved_for_environments=[str(scope.environment_id)],
        metadata_json={},
    )
    db.add(registry)
    await db.flush()
    version = ModelVersion(
        registry_id=registry.id,
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        provider=registry.provider,
        model_name=registry.model_name,
        version="1.0.0",
        fingerprint="a" * 64,
        artifact_uri="registry://test-model/1.0.0",
        artifact_digest="a" * 64,
        capabilities=["anomaly"],
        input_modalities=["structured"],
        output_modalities=["structured"],
        status=status,
        evaluation_status="passed",
        evaluation_summary={"authoritative_verifier": "test", "passed": True},
    )
    db.add(version)
    policy = GovernancePolicy(
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        environment_id=scope.environment_id,
        name=f"Specialized policy {uuid.uuid4().hex[:8]}",
        policy_type="specialized_agent",
        status="published",
        version=1,
        rules={"decision": policy_decision},
        rationale="test policy",
        created_by=user.id,
    )
    risk = RiskAssessment(
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        environment_id=scope.environment_id,
        subject_type="specialized_agent",
        subject_id="anomaly",
        version=uuid.uuid4().int % 1000000 + 1,
        tier="moderate",
        status="approved",
        factors={"source": "test"},
        required_controls=["approved_model"],
        rationale="test risk approval",
        assessed_by=user.id,
        approved_by=user.id,
    )
    db.add_all([policy, risk])
    await db.commit()
    await db.refresh(version)
    return version


def _context(service, user, version, *, request_id=None, trace_id=None):
    return service.context(
        actor_id=user.id,
        request_id=request_id or uuid.uuid4().hex,
        trace_id=trace_id or uuid.uuid4().hex,
        agent_type="anomaly",
        agent_version="1.0.0",
        model_version_id=version.id,
        risk_tier="moderate",
    )


@pytest.mark.asyncio
async def test_valid_execution_creates_policy_lineage_evidence_and_is_idempotent(db):
    tenant, _organization, _environment, user, scope = await _scope_and_user(db, "executor-valid")
    version = await _admission_rows(db, scope, user)
    service = SpecializedAgentService(db, scope)
    calls = 0

    def handler(_context, payload, _sources):
        nonlocal calls
        calls += 1
        return {"quality_state": "verified", "value": payload["value"], "review_required": False}

    context = _context(service, user, version, request_id="request-valid", trace_id="trace-valid")
    first = await service.execute(
        context,
        idempotency_key="idem-valid-1",
        payload={"value": 10},
        handler=handler,
    )
    second = await service.execute(
        context,
        idempotency_key="idem-valid-1",
        payload={"value": 10},
        handler=handler,
    )
    assert first.record.status == "succeeded"
    assert first.record.evidence_root_hash
    assert first.record.lineage_root_id
    assert second.record.id == first.record.id
    assert calls == 1

    assert await db.scalar(select(GovernancePolicyDecision).where(GovernancePolicyDecision.tenant_id == tenant.id))
    assert await db.scalar(select(LineageRecord).where(LineageRecord.id == first.record.lineage_root_id))
    assert await db.scalar(select(EvidenceEvent).where(EvidenceEvent.event_hash == first.record.evidence_root_hash))


@pytest.mark.asyncio
async def test_governance_denial_and_unapproved_model_fail_closed(db):
    _tenant, _organization, _environment, user, scope = await _scope_and_user(db, "executor-denied")
    denied_version = await _admission_rows(db, scope, user, policy_decision="deny")
    service = SpecializedAgentService(db, scope)
    context = _context(service, user, denied_version)
    tenant_id, organization_id, environment_id = context.tenant_id, context.organization_id, context.environment_id
    with pytest.raises(SpecializedPolicyDenied):
        await service.execute(
            context,
            idempotency_key="idem-denied-1",
            payload={"value": 10},
            handler=lambda *_: {"ok": True},
        )
    denied = await db.scalar(
        select(SpecializedExecutionRecord).where(
            SpecializedExecutionRecord.tenant_id == tenant_id,
            SpecializedExecutionRecord.idempotency_key == "idem-denied-1",
        )
    )
    assert denied is not None
    assert denied.status == "denied"
    assert denied.policy_decision_id is not None
    assert denied.lineage_root_id is not None
    assert denied.evidence_root_hash is not None
    tenant = await db.get(Tenant, tenant_id)
    organization = await db.get(Organization, organization_id)
    environment = await db.get(Environment, environment_id)
    scope = GovernanceScope(tenant, organization, environment)

    unapproved_version = await _admission_rows(db, scope, user, status="registered")
    unapproved_context = _context(service, user, unapproved_version)
    with pytest.raises(ModelAdmissionError):
        await service.execute(
            unapproved_context,
            idempotency_key="idem-unapproved-1",
            payload={"value": 11},
            handler=lambda *_: {"ok": True},
        )


@pytest.mark.asyncio
async def test_review_required_is_a_real_terminal_state_and_scope_isolated(db):
    tenant_a, _organization_a, _environment_a, user_a, scope_a = await _scope_and_user(db, "executor-review-a")
    version_a = await _admission_rows(db, scope_a, user_a)
    service_a = SpecializedAgentService(db, scope_a)
    context = _context(service_a, user_a, version_a)
    outcome = await service_a.execute(
        context,
        idempotency_key="idem-review-1",
        payload={"value": 20},
        handler=lambda *_: {"finding": "needs human", "review_required": True, "review_state": "required"},
    )
    assert outcome.record.status == "review_required"
    assert outcome.record.review_required is True

    tenant_b, _organization_b, _environment_b, user_b, scope_b = await _scope_and_user(db, "executor-review-b")
    assert tenant_a.id != tenant_b.id
    assert await db.scalar(
        select(type(outcome.record)).where(
            type(outcome.record).tenant_id == tenant_b.id,
            type(outcome.record).organization_id == scope_b.organization_id,
        )
    ) is None
    assert user_a.id != user_b.id
