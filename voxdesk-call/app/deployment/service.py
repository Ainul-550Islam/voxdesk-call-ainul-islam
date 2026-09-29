from __future__ import annotations
import hashlib, json, uuid, re
from urllib.parse import urlsplit
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.deployment.models import DeploymentTarget, DeploymentRevision, DeploymentArtifact, DeploymentReadiness, DeploymentVerification
from app.deployment.schemas import TARGET_TYPES, LIFECYCLE
from app.deployment.validator import validate_target
from app.governance.context import GovernanceScope
from app.governance.audit import record_governance_audit
from app.governance.evidence import append_event
from app.governance.policy import require_policy
from app.tenancy.isolation import BoundaryDenied, LifecycleDenied, ValidationFailed

_ALLOWED={"draft":{"validated","suspended"},"validated":{"approved","draft","suspended"},"approved":{"queued","validated","suspended"},"queued":{"deploying","failed","suspended"},"deploying":{"deployed","degraded","failed"},"deployed":{"degraded","suspended","retired"},"degraded":{"deploying","suspended","retired"},"failed":{"draft","suspended","retired"},"suspended":{"draft","retired"},"retired":set()}
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest()
class DeploymentService:
 def __init__(self,session:AsyncSession,scope:GovernanceScope,actor_id:uuid.UUID): self.session,self.scope,self.actor_id=session,scope,actor_id
 async def create_target(self,**data):
  try: requested_environment=uuid.UUID(str(data.pop("environment_id")))
  except (ValueError,TypeError,AttributeError) as exc: raise ValidationFailed("environment_id must be a UUID") from exc
  if self.scope.environment_id is None or requested_environment!=self.scope.environment_id: raise BoundaryDenied()
  if data.get("target_type") not in TARGET_TYPES: raise ValidationFailed("unsupported target type")
  key=data.get("idempotency_key")
  if not isinstance(key,str) or not 8<=len(key)<=200: raise ValidationFailed("valid deployment idempotency key is required")
  from app.jobs.types import ensure_payload_safe
  ensure_payload_safe({"data_residency_intent":data.get("data_residency_intent",{}),"governance_requirements":data.get("governance_requirements",{}),"prerequisites":data.get("prerequisites",{})})
  existing=await self.session.scalar(select(DeploymentTarget).where(DeploymentTarget.tenant_id==self.scope.tenant_id,DeploymentTarget.organization_id==self.scope.organization_id,DeploymentTarget.environment_id==self.scope.environment_id,DeploymentTarget.idempotency_key==data["idempotency_key"]))
  if existing is not None:
   comparable={k:getattr(existing,k) for k in ("target_type","provider","region","cluster_reference","network_mode","data_residency_intent","governance_requirements")}
   incoming={k:data.get(k) for k in comparable}
   if comparable!=incoming:raise ValidationFailed("deployment target idempotency input conflict")
   return existing
  await require_policy(self.session,self.scope,policy_type="deployment_target",context={"target_type":data["target_type"],"region":data.get("region"),"environment_id":str(self.scope.environment_id)},principal_id=self.actor_id)
  row=DeploymentTarget(tenant_id=self.scope.tenant_id,organization_id=self.scope.organization_id,environment_id=self.scope.environment_id,status="draft",**data); self.session.add(row); await self.session.flush(); await record_governance_audit(self.session,self.scope,event="deployment_target_created",actor_user_id=self.actor_id,detail={"target_id":str(row.id),"target_type":row.target_type}); await append_event(self.session,self.scope,event_type="deployment_target_created",payload={"target_id":str(row.id),"target_type":row.target_type,"idempotency_key":row.idempotency_key},actor_user_id=self.actor_id,subject_type="deployment_target",subject_id=str(row.id)); return row
 async def list_targets(self):
  return list((await self.session.scalars(select(DeploymentTarget).where(DeploymentTarget.tenant_id==self.scope.tenant_id,DeploymentTarget.organization_id==self.scope.organization_id,DeploymentTarget.environment_id==self.scope.environment_id).order_by(DeploymentTarget.created_at.desc()))).all())
 async def get_target(self,target_id,lock=False):
  stmt=select(DeploymentTarget).where(DeploymentTarget.id==target_id,DeploymentTarget.tenant_id==self.scope.tenant_id,DeploymentTarget.organization_id==self.scope.organization_id,DeploymentTarget.environment_id==self.scope.environment_id)
  if lock: stmt=stmt.with_for_update()
  row=await self.session.scalar(stmt)
  if row is None: raise BoundaryDenied()
  return row
 async def validate(self,target_id):
  target=await self.get_target(target_id,True)
  revision=await self.session.scalar(select(DeploymentRevision).where(DeploymentRevision.target_id==target.id,DeploymentRevision.tenant_id==self.scope.tenant_id,DeploymentRevision.organization_id==self.scope.organization_id,DeploymentRevision.environment_id==self.scope.environment_id).order_by(DeploymentRevision.revision_number.desc()).limit(1))
  await require_policy(self.session,self.scope,policy_type="deployment_preflight",context={"target_id":str(target.id),"revision_id":str(revision.id) if revision else None,"target_type":target.target_type},principal_id=self.actor_id)
  result=await validate_target(self.session,self.scope,target,revision=revision)
  self.session.add(DeploymentReadiness(target_id=target.id,tenant_id=self.scope.tenant_id,organization_id=self.scope.organization_id,environment_id=self.scope.environment_id,readiness=result["readiness"],checks=result));
  if result["readiness"]=="READY":
   if target.status=="draft": target.status="validated"
   if revision is not None and revision.state=="draft": revision.state="validated"
  await self.session.flush(); await record_governance_audit(self.session,self.scope,event="deployment_preflight_recorded",actor_user_id=self.actor_id,detail={"target_id":str(target.id),"readiness":result["readiness"]}); await append_event(self.session,self.scope,event_type="deployment_preflight_recorded",payload={"target_id":str(target.id),"revision_id":str(revision.id) if revision else None,"readiness":result["readiness"],"checks":result.get("checks",{}),"runtime_verified":False},actor_user_id=self.actor_id,subject_type="deployment_target",subject_id=str(target.id)); return result
 async def create_revision(self,target_id,*,artifact_reference,artifact_digest,configuration,migration_revision,runtime_version):
  target=await self.get_target(target_id,True)
  if not isinstance(configuration,dict): raise ValidationFailed("deployment configuration must be an object")
  from app.jobs.types import ensure_payload_safe
  ensure_payload_safe(configuration)
  if not artifact_reference or len(artifact_reference)>1000 or not re.fullmatch(r"sha256:[0-9a-f]{64}",artifact_digest or "") or not migration_revision or len(migration_revision)>100 or not runtime_version or len(runtime_version)>100: raise ValidationFailed("artifact reference, SHA-256 digest, migration revision, and runtime version are required")
  parsed_reference=urlsplit(artifact_reference)
  if parsed_reference.scheme in {"http","https"} and (parsed_reference.username or parsed_reference.password or parsed_reference.query or parsed_reference.fragment): raise ValidationFailed("remote artifact references must not contain credentials, query strings, or fragments")
  if parsed_reference.scheme in {"http","https"} and not target.governance_requirements.get("allow_remote_artifact_uri",False): raise ValidationFailed("remote artifact URL requires an explicit governed target allowance")
  manifest={"artifact_reference":artifact_reference,"artifact_digest":artifact_digest,"configuration_fingerprint":digest(configuration),"migration_revision":migration_revision,"runtime_version":runtime_version,"target_type":target.target_type}
  manifest_hash=digest(manifest)
  existing=await self.session.scalar(select(DeploymentRevision).where(DeploymentRevision.target_id==target.id,DeploymentRevision.tenant_id==self.scope.tenant_id,DeploymentRevision.organization_id==self.scope.organization_id,DeploymentRevision.environment_id==self.scope.environment_id,DeploymentRevision.manifest_fingerprint==manifest_hash))
  if existing is not None:return existing
  await require_policy(self.session,self.scope,policy_type="deployment_revision",context={"target_id":str(target.id),"artifact_digest":artifact_digest,"manifest_fingerprint":manifest_hash},principal_id=self.actor_id)
  latest=await self.session.scalar(select(DeploymentRevision).where(DeploymentRevision.target_id==target.id).order_by(DeploymentRevision.revision_number.desc()).limit(1).with_for_update())
  row=DeploymentRevision(target_id=target.id,tenant_id=self.scope.tenant_id,organization_id=self.scope.organization_id,environment_id=self.scope.environment_id,revision_number=(latest.revision_number+1 if latest else 1),state="draft",artifact_reference=artifact_reference,artifact_digest=artifact_digest,configuration_fingerprint=manifest["configuration_fingerprint"],migration_revision=migration_revision,runtime_version=runtime_version,manifest_fingerprint=manifest_hash,verification_state="not_verified")
  self.session.add(row); await self.session.flush()
  self.session.add(DeploymentArtifact(revision_id=row.id,tenant_id=self.scope.tenant_id,organization_id=self.scope.organization_id,environment_id=self.scope.environment_id,artifact_reference=artifact_reference,artifact_digest=artifact_digest,manifest_fingerprint=manifest_hash,metadata_json={"migration_revision":migration_revision,"runtime_version":runtime_version}))
  await append_event(self.session,self.scope,event_type="deployment_revision_created",payload={"revision_id":str(row.id),"target_id":str(target.id),"revision_number":row.revision_number,"artifact_digest":artifact_digest,"manifest_fingerprint":manifest_hash},actor_user_id=self.actor_id,subject_type="deployment_revision",subject_id=str(row.id))
  await record_governance_audit(self.session,self.scope,event="deployment_revision_created",actor_user_id=self.actor_id,detail={"revision_id":str(row.id),"target_id":str(target.id),"artifact_digest":artifact_digest})
  return row
 async def list_revisions(self,target_id):
  await self.get_target(target_id)
  return list((await self.session.scalars(select(DeploymentRevision).where(DeploymentRevision.target_id==target_id,DeploymentRevision.tenant_id==self.scope.tenant_id,DeploymentRevision.organization_id==self.scope.organization_id,DeploymentRevision.environment_id==self.scope.environment_id).order_by(DeploymentRevision.revision_number.desc()))).all())
 async def get_revision(self,revision_id):
  row=await self.session.scalar(select(DeploymentRevision).where(DeploymentRevision.id==revision_id,DeploymentRevision.tenant_id==self.scope.tenant_id,DeploymentRevision.organization_id==self.scope.organization_id,DeploymentRevision.environment_id==self.scope.environment_id))
  if row is None:raise BoundaryDenied()
  return row
 async def request_approval(self,revision_id):
  row=await self.session.scalar(select(DeploymentRevision).where(DeploymentRevision.id==revision_id,DeploymentRevision.tenant_id==self.scope.tenant_id,DeploymentRevision.organization_id==self.scope.organization_id,DeploymentRevision.environment_id==self.scope.environment_id).with_for_update())
  if row is None:raise BoundaryDenied()
  if row.state!="validated":raise LifecycleDenied("only a validated revision may enter human review")
  from app.review.models import ReviewCase
  from app.review.enums import TERMINAL_CASE_STATES
  case=await self.session.scalar(select(ReviewCase).where(ReviewCase.tenant_id==self.scope.tenant_id,ReviewCase.organization_id==self.scope.organization_id,ReviewCase.environment_id==self.scope.environment_id,ReviewCase.case_type=="deployment",ReviewCase.subject_type=="deployment_revision",ReviewCase.subject_id==str(row.id),ReviewCase.status.not_in(tuple(TERMINAL_CASE_STATES))).with_for_update())
  if case is None:
   from app.review.service import create_case
   case=await create_case(self.session,self.scope,actor_user_id=self.actor_id,case_type="deployment",agent_type="deployment",subject_type="deployment_revision",subject_id=str(row.id),reason="Deployment revision requires human approval",requested_controls=["deployment_approval","artifact_integrity","residency_policy"],metadata={"manifest_fingerprint":row.manifest_fingerprint,"artifact_digest":row.artifact_digest})
  await record_governance_audit(self.session,self.scope,event="deployment_approval_requested",actor_user_id=self.actor_id,detail={"revision_id":str(row.id),"review_case_id":str(case.id)})
  await append_event(self.session,self.scope,event_type="deployment_approval_requested",payload={"revision_id":str(row.id),"review_case_id":str(case.id),"manifest_fingerprint":row.manifest_fingerprint},actor_user_id=self.actor_id,subject_type="deployment_revision",subject_id=str(row.id))
  return case
 async def transition(self,revision_id,state):
  row=await self.session.scalar(select(DeploymentRevision).where(DeploymentRevision.id==revision_id,DeploymentRevision.tenant_id==self.scope.tenant_id,DeploymentRevision.organization_id==self.scope.organization_id,DeploymentRevision.environment_id==self.scope.environment_id).with_for_update())
  if row is None: raise BoundaryDenied()
  if state not in _ALLOWED.get(row.state,set()): raise LifecycleDenied("invalid deployment transition")
  if state == "approved": raise LifecycleDenied("approval must be recorded through the Prompt-3 human review system")
  if state=="queued": raise LifecycleDenied("durable deployment job and approved execution adapter are not integrated")
  if state in {"deploying","deployed","degraded","failed"}: raise LifecycleDenied("deployment outcomes require an integrated authoritative runtime observer")
  if state in {"queued","deploying"}: await require_policy(self.session,self.scope,policy_type="deployment",context={"state":state,"revision_id":str(row.id),"artifact_digest":row.artifact_digest},principal_id=self.actor_id,correlation_id=str(row.id))
  row.state=state; await self.session.flush(); await append_event(self.session,self.scope,event_type="deployment_state_changed",payload={"revision_id":str(row.id),"state":state,"manifest_fingerprint":row.manifest_fingerprint},actor_user_id=self.actor_id,subject_type="deployment_revision",subject_id=str(row.id)); await record_governance_audit(self.session,self.scope,event="deployment_state_changed",actor_user_id=self.actor_id,detail={"revision_id":str(row.id),"state":state}); return row
 async def verify(self,revision_id,*,state,authoritative_verifier=None,evidence_reference=None,observed_fingerprint=None):
  allowed={"not_verified","configured","preflight_passed","deployment_observed","runtime_verified","failed"}
  if state not in allowed: raise ValidationFailed("unknown verification state")
  if state!="not_verified" or any((authoritative_verifier,evidence_reference,observed_fingerprint)):
   raise LifecycleDenied("verification evidence may only be written by an integrated authoritative verifier")
  row=await self.session.scalar(select(DeploymentRevision).where(DeploymentRevision.id==revision_id,DeploymentRevision.tenant_id==self.scope.tenant_id,DeploymentRevision.organization_id==self.scope.organization_id,DeploymentRevision.environment_id==self.scope.environment_id).with_for_update())
  if row is None: raise BoundaryDenied()
  if state in {"deployment_observed","runtime_verified"}: raise LifecycleDenied("authoritative runtime observation is not integrated; caller-supplied fields cannot establish deployment verification")
  if state=="runtime_verified" and row.state!="deployed": raise LifecycleDenied("runtime verification requires deployed lifecycle state")
  row.verification_state=state; proof=DeploymentVerification(revision_id=row.id,tenant_id=self.scope.tenant_id,organization_id=self.scope.organization_id,environment_id=self.scope.environment_id,state=state,authoritative_verifier=None,evidence_reference=None,observed_fingerprint=None); self.session.add(proof); await self.session.flush(); await record_governance_audit(self.session,self.scope,event="deployment_verification_unavailable",actor_user_id=self.actor_id,detail={"revision_id":str(row.id),"state":"not_verified"}); await append_event(self.session,self.scope,event_type="deployment_verification_unavailable",payload={"revision_id":str(row.id),"state":"not_verified","runtime_verified":False},actor_user_id=self.actor_id,subject_type="deployment_revision",subject_id=str(row.id)); return proof
