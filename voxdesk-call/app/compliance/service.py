from __future__ import annotations
import datetime as dt
import uuid
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.compliance.enums import FrameworkType
from app.compliance.repository import transition_remediation as transition_remediation_row
from app.compliance.models import ComplianceFramework, ComplianceControl, ComplianceFinding, Remediation
from app.compliance import repository
from app.compliance.qms import evaluate_control
from app.compliance.ocg import evaluate_item
from app.compliance.industry import INDUSTRY_TYPES,evaluate_industry_control
from app.governance.context import GovernanceScope
from app.governance.audit import record_governance_audit
from app.governance.evidence import append_event
from app.governance.hashing import sha256_hex
from app.governance.policy import require_policy
from app.governance.enums import DecisionType
from app.review.service import create_case
from app.db.models import User
from app.tenancy.isolation import BoundaryDenied, HierarchyError, ValidationFailed

class ComplianceService:
    def __init__(self, session: AsyncSession, scope: GovernanceScope, actor_id: uuid.UUID):
        if scope.environment_id is None: raise HierarchyError("Environment is required", code="environment_required")
        self.session, self.scope, self.actor_id = session, scope, actor_id

    async def create_framework(self, *, framework_type: str, name: str, version: str, configuration: dict, controls: list[dict]):
        if framework_type not in {x.value for x in FrameworkType}: raise HierarchyError("Unsupported framework type", code="invalid_framework")
        from app.jobs.types import ensure_payload_safe
        ensure_payload_safe({"configuration":configuration,"controls":controls})
        await require_policy(self.session, self.scope, policy_type="compliance_framework", context={"framework_type":framework_type,"control_count":len(controls)}, principal_id=self.actor_id)
        row=ComplianceFramework(tenant_id=self.scope.tenant_id,organization_id=self.scope.organization_id,environment_id=self.scope.environment_id,framework_type=framework_type,name=name,version=version,status="active",configuration=configuration)
        self.session.add(row); await self.session.flush()
        for c in controls: self.session.add(ComplianceControl(framework_id=row.id,**c))
        await self.session.flush()
        await record_governance_audit(self.session,self.scope,event="compliance_framework_created",actor_user_id=self.actor_id,detail={"framework_id":str(row.id),"framework_type":framework_type,"version":version})
        await append_event(self.session,self.scope,event_type="compliance_framework_created",payload={"framework_id":str(row.id),"framework_type":framework_type,"version":version},actor_user_id=self.actor_id,subject_type="compliance_framework",subject_id=str(row.id))
        return row

    async def evaluate(self, *, framework_id: uuid.UUID, subject_type: str, subject_id: str, observed: dict[str,Any], evidence_references: list[str]):
        framework=await repository.get_framework(self.session,self.scope,framework_id,lock=True)
        now=dt.datetime.now(dt.timezone.utc)
        starts=framework.effective_from.replace(tzinfo=dt.timezone.utc) if framework.effective_from and framework.effective_from.tzinfo is None else framework.effective_from
        ends=framework.effective_to.replace(tzinfo=dt.timezone.utc) if framework.effective_to and framework.effective_to.tzinfo is None else framework.effective_to
        if framework.status!="active" or starts is not None and starts>now or ends is not None and ends<=now:
            raise HierarchyError("Compliance framework is inactive or outside its effective period",code="framework_inactive")
        admission=await require_policy(self.session,self.scope,policy_type="compliance_check",context={"framework_type":framework.framework_type,"subject_type":subject_type,"evidence_count":len(evidence_references)},principal_id=self.actor_id,correlation_id=str(framework.id))
        controls=await repository.controls_for(self.session,framework.id)
        results=[]
        for control in controls:
            if framework.framework_type=="qms":
                result=evaluate_control(control,observed,evidence_references)
            elif framework.framework_type=="ocg":
                result=[evaluate_item(control,item,evidence_references) for item in observed.get("items",[]) or [observed]]
            elif framework.framework_type in INDUSTRY_TYPES:
                result=evaluate_industry_control(control,observed,evidence_references,framework.framework_type)
            else:
                raise HierarchyError("Unsupported compliance framework type",code="invalid_framework")
            result_list=result if isinstance(result,list) else [result]
            for item in result_list:
                status=item["result"]
                fingerprint=item.get("evidence_fingerprint",item.get("observed_fingerprint",sha256_hex(item)))
                existing=await self.session.scalar(select(ComplianceFinding).where(ComplianceFinding.tenant_id==self.scope.tenant_id,ComplianceFinding.organization_id==self.scope.organization_id,ComplianceFinding.environment_id==self.scope.environment_id,ComplianceFinding.framework_id==framework.id,ComplianceFinding.control_id==control.id,ComplianceFinding.subject_type==subject_type,ComplianceFinding.subject_id==subject_id,ComplianceFinding.evidence_fingerprint==fingerprint))
                if existing is not None:
                    results.append({"finding_id":str(existing.id),**(existing.result or item)})
                    continue
                from app.auth.identity.events import scrub
                safe_item=scrub(item)
                finding=ComplianceFinding(framework_id=framework.id,control_id=control.id,tenant_id=self.scope.tenant_id,organization_id=self.scope.organization_id,environment_id=self.scope.environment_id,subject_type=subject_type,subject_id=subject_id,status=status,severity=control.severity,result=safe_item,rationale=str(item.get("rationale",item.get("explanation","")))[:2000],source_reference=str(item.get("source_reference") or control.source_reference or "")[:1000] or None,evidence_fingerprint=fingerprint,review_required=status in {"needs_review","review_required"} or control.severity in {"high","critical"})
                self.session.add(finding); await self.session.flush()
                if finding.review_required:
                    await create_case(self.session,self.scope,actor_user_id=self.actor_id,case_type="compliance",agent_type="compliance",reason="Compliance finding requires human review",requested_controls=[control.control_key],subject_type="compliance_finding",subject_id=str(finding.id),metadata={"finding_id":str(finding.id)})
                results.append({"finding_id":str(finding.id),**item})
        await append_event(self.session,self.scope,event_type="compliance_check_completed",payload={"framework_id":str(framework.id),"finding_ids":[r["finding_id"] for r in results],"policy_decision_id":str(admission.id)},actor_user_id=self.actor_id,subject_type="compliance_framework",subject_id=str(framework.id))
        await record_governance_audit(self.session,self.scope,event="compliance_check_completed",actor_user_id=self.actor_id,detail={"framework_id":str(framework.id),"finding_count":len(results)})
        return results

    async def _validate_owner(self,owner_id):
        if owner_id is None:return None
        user=await self.session.scalar(select(User).where(User.id==owner_id,User.tenant_id==self.scope.tenant_id))
        if user is None or not user.is_active:raise BoundaryDenied()
        return user

    async def create_remediation(self, finding_id: uuid.UUID, *, owner_id=None, due_at=None):
        await self._validate_owner(owner_id)
        finding=await repository.finding_for_update(self.session,self.scope,finding_id)
        await require_policy(self.session,self.scope,policy_type="compliance_remediation",context={"finding_id":str(finding.id),"severity":finding.severity,"operation":"create"},principal_id=self.actor_id,correlation_id=str(finding.id))
        existing=await self.session.scalar(select(Remediation).where(Remediation.finding_id==finding.id,Remediation.tenant_id==self.scope.tenant_id,Remediation.organization_id==self.scope.organization_id,Remediation.environment_id==self.scope.environment_id).with_for_update())
        if existing is not None:return existing
        row=Remediation(finding_id=finding.id,tenant_id=self.scope.tenant_id,organization_id=self.scope.organization_id,environment_id=self.scope.environment_id,status="open",owner_id=owner_id,due_at=due_at)
        self.session.add(row); await self.session.flush()
        await record_governance_audit(self.session,self.scope,event="compliance_remediation_created",actor_user_id=self.actor_id,detail={"finding_id":str(finding.id),"remediation_id":str(row.id)})
        await append_event(self.session,self.scope,event_type="compliance_remediation_created",payload={"finding_id":str(finding.id),"remediation_id":str(row.id)},actor_user_id=self.actor_id,subject_type="compliance_remediation",subject_id=str(row.id))
        return row

    async def transition_remediation(self,remediation_id:uuid.UUID,*,status:str,owner_id=None,due_at=None,resolution_reference=None):
        await self._validate_owner(owner_id)
        if resolution_reference is not None:
            if not isinstance(resolution_reference,str) or not resolution_reference.strip() or len(resolution_reference)>1000:
                raise ValidationFailed("resolution_reference must be a non-empty opaque reference of at most 1000 characters")
            from app.jobs.types import ensure_payload_safe
            ensure_payload_safe({"resolution_reference":resolution_reference})
        row=await repository.remediation_for_update(self.session,self.scope,remediation_id)
        await require_policy(self.session,self.scope,policy_type="compliance_remediation",context={"remediation_id":str(row.id),"from_status":row.status,"to_status":status},principal_id=self.actor_id,correlation_id=str(row.id))
        result=await transition_remediation_row(self.session,self.scope,remediation_id,status,owner_id=owner_id,due_at=due_at,resolution_reference=resolution_reference)
        await record_governance_audit(self.session,self.scope,event="compliance_remediation_transitioned",actor_user_id=self.actor_id,detail={"remediation_id":str(row.id),"status":status})
        await append_event(self.session,self.scope,event_type="compliance_remediation_transitioned",payload={"remediation_id":str(row.id),"status":status,"resolution_reference":resolution_reference},actor_user_id=self.actor_id,subject_type="compliance_remediation",subject_id=str(row.id))
        return result
