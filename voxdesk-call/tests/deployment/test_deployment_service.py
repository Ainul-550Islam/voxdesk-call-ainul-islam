from __future__ import annotations
import pytest
from app.deployment.service import DeploymentService
from app.deployment.models import DeploymentArtifact,DeploymentRevision
from app.governance.models import GovernancePolicy
from app.tenancy.isolation import BoundaryDenied,LifecycleDenied,ValidationFailed
from tests.specialized_agents.test_executor import _scope_and_user

@pytest.mark.asyncio
async def test_target_idempotency_preflight_revision_integrity_and_fail_closed_verification(db):
 tenant,org,env,user,scope=await _scope_and_user(db,"deploy-scope")
 db.add_all([GovernancePolicy(tenant_id=tenant.id,organization_id=org.id,environment_id=env.id,name="target-policy",policy_type="deployment_target",status="published",version=1,rules={"decision":"allow"},rationale="test",created_by=user.id),GovernancePolicy(tenant_id=tenant.id,organization_id=org.id,environment_id=env.id,name="revision-policy",policy_type="deployment_revision",status="published",version=1,rules={"decision":"allow"},rationale="test",created_by=user.id),GovernancePolicy(tenant_id=tenant.id,organization_id=org.id,environment_id=env.id,name="deploy-policy",policy_type="deployment",status="published",version=1,rules={"decision":"allow"},rationale="test",created_by=user.id),GovernancePolicy(tenant_id=tenant.id,organization_id=org.id,environment_id=env.id,name="preflight-policy",policy_type="deployment_preflight",status="published",version=1,rules={"decision":"allow"},rationale="test",created_by=user.id)])
 await db.flush();svc=DeploymentService(db,scope,user.id)
 values={"environment_id":str(env.id),"idempotency_key":"deployment-target-01","target_type":"saas","provider":"internal","region":"us-east","cluster_reference":None,"network_mode":"restricted","data_residency_intent":{},"governance_requirements":{}}
 target=await svc.create_target(**values);assert target.status=="draft"
 assert await svc.create_target(**values) is target
 with pytest.raises(ValidationFailed):
  await svc.create_target(**{**values,"region":"eu-west"})
 readiness=await svc.validate(target.id)
 assert readiness["readiness"]=="NOT_READY" and readiness["runtime_verified"] is False
 rev=await svc.create_revision(target.id,artifact_reference="registry://service/voxdesk",artifact_digest="sha256:"+"a"*64,configuration={"replicas":2},migration_revision="0034_review_and_specialized_persistence",runtime_version="python-3.12")
 assert rev.manifest_fingerprint and rev.verification_state=="not_verified"
 artifact=await db.scalar(__import__("sqlalchemy").select(DeploymentArtifact).where(DeploymentArtifact.revision_id==rev.id));assert artifact and artifact.artifact_digest==rev.artifact_digest
 with pytest.raises(ValidationFailed):
  await svc.create_revision(target.id,artifact_reference="registry://service/voxdesk",artifact_digest="sha256:"+"b"*64,configuration={"api_key":"must-not-persist"},migration_revision="0034",runtime_version="python")
 with pytest.raises(LifecycleDenied):await svc.verify(rev.id,state="runtime_verified",authoritative_verifier="client",evidence_reference="fake",observed_fingerprint="f"*64)
 rev.state="validated"
 await db.flush()
 review_case=await svc.request_approval(rev.id)
 assert review_case.case_type=="deployment" and review_case.agent_type=="deployment"
 with pytest.raises(LifecycleDenied):await svc.transition(rev.id,"queued")
 with pytest.raises(ValidationFailed):await svc.create_revision(target.id,artifact_reference="registry://service/voxdesk",artifact_digest="fake",configuration={},migration_revision="0034",runtime_version="python")
 other_tenant,_other_org,_other_env,_other_user,other_scope=await _scope_and_user(db,"deploy-other")
 with pytest.raises(BoundaryDenied):await DeploymentService(db,other_scope,user.id).get_target(target.id)

@pytest.mark.asyncio
async def test_unsupported_target_fails_before_persistence(db):
 _tenant,org,env,user,scope=await _scope_and_user(db,"deploy-bad")
 svc=DeploymentService(db,scope,user.id)
 with pytest.raises(ValidationFailed):
  await svc.create_target(environment_id=str(env.id),idempotency_key="deploy-invalid-001",target_type="unknown",provider=None,region=None,cluster_reference=None,network_mode="restricted",data_residency_intent={},governance_requirements={})
