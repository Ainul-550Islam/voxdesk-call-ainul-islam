from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.auth.jwt import create_access_token
from app.deployment.models import DeploymentArtifact
from app.deployment.service import DeploymentService
from app.governance.models import GovernancePolicy
from app.tenancy.isolation import BoundaryDenied, LifecycleDenied, ValidationFailed
from tests.specialized_agents.test_executor import _scope_and_user


@pytest.mark.asyncio
async def test_target_idempotency_preflight_revision_integrity_and_fail_closed_verification(db):
    tenant, organization, environment, user, scope = await _scope_and_user(
        db, "deploy-scope"
    )
    policy_types = (
        "deployment_target",
        "deployment_revision",
        "deployment",
        "deployment_preflight",
    )
    db.add_all(
        [
            GovernancePolicy(
                tenant_id=tenant.id,
                organization_id=organization.id,
                environment_id=environment.id,
                name=f"{policy_type}-policy",
                policy_type=policy_type,
                status="published",
                version=1,
                rules={"decision": "allow"},
                rationale="test",
                created_by=user.id,
            )
            for policy_type in policy_types
        ]
    )
    await db.flush()
    service = DeploymentService(db, scope, user.id)
    target_values = {
        "environment_id": str(environment.id),
        "idempotency_key": "deployment-target-01",
        "target_type": "saas",
        "provider": "internal",
        "region": "us-east",
        "cluster_reference": None,
        "network_mode": "restricted",
        "data_residency_intent": {},
        "governance_requirements": {},
    }

    target = await service.create_target(**target_values)
    assert target.status == "draft"
    assert await service.create_target(**target_values) is target
    with pytest.raises(ValidationFailed):
        await service.create_target(**{**target_values, "region": "eu-west"})

    readiness = await service.validate(target.id)
    assert readiness["readiness"] == "NOT_READY"
    assert readiness["runtime_verified"] is False

    revision = await service.create_revision(
        target.id,
        artifact_reference="registry://service/voxdesk",
        artifact_digest="sha256:" + "a" * 64,
        configuration={"replicas": 2},
        migration_revision="0034_review_specialized_persist",
        runtime_version="python-3.12",
    )
    assert revision.manifest_fingerprint
    assert revision.verification_state == "not_verified"
    artifact = await db.scalar(
        select(DeploymentArtifact).where(
            DeploymentArtifact.revision_id == revision.id
        )
    )
    assert artifact is not None
    assert artifact.artifact_digest == revision.artifact_digest

    with pytest.raises(ValidationFailed):
        await service.create_revision(
            target.id,
            artifact_reference="registry://service/voxdesk",
            artifact_digest="sha256:" + "b" * 64,
            configuration={"api_key": "must-not-persist"},
            migration_revision="0034",
            runtime_version="python",
        )
    with pytest.raises(LifecycleDenied):
        await service.verify(
            revision.id,
            state="runtime_verified",
            authoritative_verifier="client",
            evidence_reference="fake",
            observed_fingerprint="f" * 64,
        )

    revision.state = "validated"
    await db.flush()
    review_case = await service.request_approval(revision.id)
    assert review_case.case_type == "deployment"
    assert review_case.agent_type == "deployment"
    with pytest.raises(LifecycleDenied):
        await service.transition(revision.id, "queued")
    with pytest.raises(ValidationFailed):
        await service.create_revision(
            target.id,
            artifact_reference="registry://service/voxdesk",
            artifact_digest="fake",
            configuration={},
            migration_revision="0034",
            runtime_version="python",
        )

    _other_tenant, _other_org, _other_env, _other_user, other_scope = (
        await _scope_and_user(db, "deploy-other")
    )
    with pytest.raises(BoundaryDenied):
        await DeploymentService(db, other_scope, user.id).get_target(target.id)


@pytest.mark.asyncio
async def test_unsupported_target_fails_before_persistence(db):
    _tenant, _organization, environment, user, scope = await _scope_and_user(
        db, "deploy-bad"
    )
    service = DeploymentService(db, scope, user.id)
    with pytest.raises(ValidationFailed):
        await service.create_target(
            environment_id=str(environment.id),
            idempotency_key="deploy-invalid-001",
            target_type="unknown",
            provider=None,
            region=None,
            cluster_reference=None,
            network_mode="restricted",
            data_residency_intent={},
            governance_requirements={},
        )


@pytest.mark.asyncio
async def test_public_verify_route_dispatches_to_authoritative_runtime_verifier(
    client, db
):
    tenant, organization, environment, user, scope = await _scope_and_user(
        db, "deploy-route-dispatch"
    )
    policy_types = (
        "deployment_target",
        "deployment_revision",
        "deployment",
        "deployment_preflight",
    )
    for policy_type in policy_types:
        db.add(
            GovernancePolicy(
                tenant_id=tenant.id,
                organization_id=organization.id,
                environment_id=environment.id,
                name=f"route-{policy_type}-{uuid.uuid4().hex[:8]}",
                policy_type=policy_type,
                status="published",
                version=1,
                rules={"decision": "allow"},
                rationale="route dispatch regression test",
                created_by=user.id,
            )
        )
    await db.commit()

    service = DeploymentService(db, scope, user.id)
    target = await service.create_target(
        environment_id=str(environment.id),
        idempotency_key="deployment-route-01",
        target_type="saas",
        provider="internal",
        region="us-east",
        cluster_reference=None,
        network_mode="restricted",
        data_residency_intent={},
        governance_requirements={},
    )
    revision = await service.create_revision(
        target.id,
        artifact_reference="registry://service/voxdesk",
        artifact_digest="sha256:" + "a" * 64,
        configuration={"replicas": 1},
        migration_revision="head",
        runtime_version="test",
    )
    await db.commit()

    token, _claims = create_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        role=user.role.value,
        token_version=user.token_version,
    )
    response = await client.post(
        f"/api/deployment/revisions/{revision.id}/verify",
        params={"environment_id": str(environment.id)},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == (
        "persisted approval and governance decision are required"
    )
