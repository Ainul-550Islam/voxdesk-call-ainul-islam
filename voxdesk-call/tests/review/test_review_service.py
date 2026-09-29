"""Behavioral coverage for review state transitions and tenant authorization."""
from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, Environment, Organization, UserRole
from app.governance.context import GovernanceScope
from app.governance.models import EvidenceEvent, GovernancePolicy
from app.review import repository, service
from app.review.enums import ReviewCaseStatus
from app.tenancy.isolation import BoundaryDenied, Forbidden, LifecycleDenied
from tests.conftest import make_tenant, make_user


async def _scope(db, name):
    tenant = await make_tenant(db, name)
    actor = await make_user(db, tenant, UserRole.OWNER)
    reviewer = await make_user(db, tenant, UserRole.OWNER)
    organization = await db.get(Organization, tenant.organization_id)
    environment = await db.scalar(select(Environment).where(Environment.tenant_id == tenant.id, Environment.kind == "production"))
    assert environment is not None
    return tenant, actor, reviewer, GovernanceScope(tenant, organization, environment)


async def _allow_review_decision(db, scope, actor):
    db.add(GovernancePolicy(tenant_id=scope.tenant_id, organization_id=scope.organization_id, environment_id=scope.environment_id, name=f"review-{uuid.uuid4()}", policy_type="review_decision", status="published", version=1, rules={"decision": "allow", "required_human_approval": True}, rationale="review test policy", created_by=actor.id))
    await db.flush()


@pytest.mark.asyncio
async def test_create_filter_assign_start_approve_and_audit_evidence(db):
    tenant, actor, reviewer, scope = await _scope(db, "review-approved")
    case = await service.create_case(db, scope, actor_user_id=actor.id, case_type="legal", agent_type="legal", reason="Findings require a human decision", requested_controls=["human_review"], metadata={"safe_reference": "doc-1"}, subject_id="document:doc-1")
    await db.flush()
    queued = await repository.list_cases(db, tenant_id=tenant.id, organization_id=scope.organization_id, environment_id=scope.environment_id, status="pending", case_type="legal")
    assert [row.id for row in queued] == [case.id]
    _, assignment = await service.assign_case(db, scope, case_id=case.id, reviewer_id=reviewer.id, assigned_by=actor.id)
    assert case.status == "assigned"
    assert assignment.reviewer_id == reviewer.id
    await service.start_case(db, scope, case_id=case.id, reviewer_id=reviewer.id)
    assert case.status == "in_review"
    await _allow_review_decision(db, scope, actor)
    case, decision = await service.decide_case(db, scope, case_id=case.id, reviewer_id=reviewer.id, decision="approve", rationale="Reviewed the cited material; no conclusion of legal validity.")
    await db.commit()
    assert case.status == ReviewCaseStatus.APPROVED.value
    assert decision.policy_decision_id is not None
    assert decision.evidence_event_id is not None
    assert await db.scalar(select(EvidenceEvent).where(EvidenceEvent.id == decision.evidence_event_id)) is not None
    audit = await db.scalar(select(AuditLog).where(AuditLog.tenant_id == tenant.id))
    assert audit is not None


@pytest.mark.asyncio
async def test_reject_request_changes_cancel_expire_and_stale_transition(db):
    _tenant, actor, reviewer, scope = await _scope(db, "review-decisions")
    await _allow_review_decision(db, scope, actor)
    for expected, action in (("rejected", "reject"), ("changes_requested", "request_changes")):
        case = await service.create_case(db, scope, actor_user_id=actor.id, case_type="insight", agent_type="insight", reason="Human review requested", subject_id=f"insight:{uuid.uuid4()}")
        await service.assign_case(db, scope, case_id=case.id, reviewer_id=reviewer.id, assigned_by=actor.id)
        await service.start_case(db, scope, case_id=case.id, reviewer_id=reviewer.id)
        result, decision = await service.decide_case(db, scope, case_id=case.id, reviewer_id=reviewer.id, decision=action, rationale="Needs further review.")
        assert result.status == expected
        assert decision.decision == action
        with pytest.raises(LifecycleDenied):
            await service.decide_case(db, scope, case_id=case.id, reviewer_id=reviewer.id, decision="approve", rationale="stale")
    cancellable = await service.create_case(db, scope, actor_user_id=actor.id, case_type="anomaly", agent_type="anomaly", reason="Cancel path", subject_id="anomaly:cancel")
    assert (await service.cancel_case(db, scope, case_id=cancellable.id, actor_user_id=actor.id, reason="operator cancelled" )).status == "cancelled"
    expiring = await service.create_case(db, scope, actor_user_id=actor.id, case_type="forecast", agent_type="forecasting", reason="Expiry path", subject_id="forecast:expire")
    _, assignment = await service.assign_case(db, scope, case_id=expiring.id, reviewer_id=reviewer.id, assigned_by=actor.id, expires_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1))
    expired = await service.expire_case(db, scope, case_id=expiring.id, actor_user_id=actor.id)
    assert expired.status == "expired"
    assert assignment.status == "expired"


@pytest.mark.asyncio
async def test_tenant_and_environment_boundaries_and_self_review_policy(db):
    tenant_a, actor_a, reviewer_a, scope_a = await _scope(db, "review-scope-a")
    tenant_b, actor_b, _reviewer_b, scope_b = await _scope(db, "review-scope-b")
    case = await service.create_case(db, scope_a, actor_user_id=actor_a.id, case_type="translation", agent_type="translation", reason="Scope check", subject_id="translation:scope")
    with pytest.raises(BoundaryDenied):
        await service.assign_case(db, scope_a, case_id=case.id, reviewer_id=actor_b.id, assigned_by=actor_a.id)
    assert await repository.get_case(db, tenant_id=tenant_b.id, organization_id=scope_b.organization_id, case_id=case.id) is None
    second_environment = Environment(tenant_id=tenant_a.id, name="Review Test", slug=f"review-test-{uuid.uuid4().hex[:6]}", kind="staging", is_default=False)
    db.add(second_environment)
    await db.flush()
    other_scope = GovernanceScope(scope_a.tenant, scope_a.organization, second_environment)
    assert await repository.get_case(db, tenant_id=tenant_a.id, organization_id=scope_a.organization_id, environment_id=other_scope.environment_id, case_id=case.id) is None
    await service.assign_case(db, scope_a, case_id=case.id, reviewer_id=actor_a.id, assigned_by=actor_a.id)
    await service.start_case(db, scope_a, case_id=case.id, reviewer_id=actor_a.id)
    await _allow_review_decision(db, scope_a, actor_a)
    with pytest.raises(Forbidden):
        await service.decide_case(db, scope_a, case_id=case.id, reviewer_id=actor_a.id, decision="approve", rationale="self review")
    assert tenant_a.id != tenant_b.id
    assert reviewer_a.id != actor_a.id


@pytest.mark.asyncio
async def test_review_routes_use_existing_auth_and_permission_dependencies(client, db):
    from tests.conftest import auth_headers

    tenant, owner, _reviewer, scope = await _scope(db, "review-routes")
    case = await service.create_case(db, scope, actor_user_id=owner.id, case_type="legal", agent_type="legal", reason="Route queue test", subject_id="document:route")
    await db.commit()
    unauthenticated = await client.get("/api/reviews")
    assert unauthenticated.status_code == 401
    viewer = await make_user(db, tenant, UserRole.VIEWER)
    forbidden = await client.get("/api/reviews", headers=await auth_headers(client, viewer))
    assert forbidden.status_code == 403
    response = await client.get(f"/api/reviews?environment_id={scope.environment_id}&agent_type=legal", headers=await auth_headers(client, owner))
    assert response.status_code == 200, response.text
    assert any(item["id"] == str(case.id) for item in response.json())
