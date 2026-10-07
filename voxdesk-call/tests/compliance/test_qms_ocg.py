from __future__ import annotations
import uuid
import pytest
from sqlalchemy import select
from app.compliance.models import ComplianceFinding,Remediation
from app.compliance.qms import evaluate_control
from app.compliance.ocg import evaluate_item
from app.compliance.industry import evaluate_industry_control
from app.specialized_agents.registry import resolve_agent
from app.compliance.service import ComplianceService
from app.governance.models import GovernancePolicy
from app.tenancy.isolation import BoundaryDenied
from tests.specialized_agents.test_executor import _scope_and_user

class Control:
 def __init__(self,key,rules,source="policy://controlled/v1",required=None,severity="medium"):
  self.id=uuid.uuid4()
  self.control_key=key
  self.rules=rules
  self.source_reference=source
  self.required_evidence=required or []
  self.severity=severity

def test_qms_document_status_revision_evidence_and_source_are_explainable():
 control=Control("document_release",{"controlled_document":True,"document_requirements":{"revision":"3","owner":"records-team","effective_date":"2026-01-01"},"approved_states":["approved"],"require_revision":True,"require_owner":True,"require_effective_date":True},required=["evidence://approval/42"])
 result=evaluate_control(control,{"document":{"revision":"2","owner":"records-team","effective_date":"2026-01-01","approval_state":"approved","body":"must not persist"}},["evidence://approval/42"])
 assert result["result"]=="needs_review" and result["review_required"]
 assert result["source_reference"]=="policy://controlled/v1" and result["gaps"]["revision"]["expected"]=="3"
 assert "body" not in str(result)
 missing=evaluate_control(control,{"document":{"revision":"3","owner":"records-team","effective_date":"2026-01-01","approval_state":"approved"}},[])
 assert missing["result"]=="insufficient_evidence"
 good=evaluate_control(control,{"document":{"revision":"3","owner":"records-team","effective_date":"2026-01-01","approval_state":"approved"}},["evidence://approval/42"])
 assert good["result"]=="compliant" and good["evidence_fingerprint"]

def test_ocg_rule_states_are_deterministic_and_source_bound():
 control=Control("line_limit",{"source_reference":"guideline://billing/v2","field":"amount","operator":"less_than","expected":100})
 assert evaluate_item(control,{"amount":50},["guideline://billing/v2"])["result"]=="pass"
 assert evaluate_item(control,{"amount":120},["guideline://billing/v2"])["result"]=="fail"
 assert evaluate_item(control,{"amount":50},[])["result"]=="insufficient_evidence"
 exception=Control("exception",{"source_reference":"guideline://billing/v2","field":"amount","expected":100,"exception_field":"exception_code"})
 assert evaluate_item(exception,{"amount":20,"exception_code":"X"},["guideline://billing/v2"])["result"]=="review_required"
 assert evaluate_item(control,{"amount":50},["guideline://billing/v2"])==evaluate_item(control,{"amount":50},["guideline://billing/v2"])

def test_requested_agent_catalog_and_industry_checks_are_registered_and_non_clinical():
 requested={"legal","translation","anomaly","ocg_compliance","qms_compliance","healthcare","manufacturing","retail"}
 assert requested <= {definition.type for definition in __import__("app.specialized_agents.registry",fromlist=["definitions"]).definitions()}
 for name in ("qms_compliance","healthcare","manufacturing","retail"):
  assert resolve_agent(name).status=="active"
 for industry in ("healthcare","manufacturing","retail"):
  result=evaluate_industry_control(Control("configured_gate",{"field":"state","expected":"approved"},severity="high"),{"state":"rejected","patient_name":"not persisted"},[],industry)
  assert result["result"]=="review_required" and result["review_required"]
  assert "patient_name" not in str(result) and "certification" in result["disclaimer"]

@pytest.mark.asyncio
async def test_compliance_findings_remediation_review_and_scope_isolation(db):
 tenant,org,env,user,scope=await _scope_and_user(db,"compliance-scope")
 db.add_all([GovernancePolicy(tenant_id=tenant.id,organization_id=org.id,environment_id=env.id,name="compliance-framework-policy",policy_type="compliance_framework",status="published",version=1,rules={"decision":"allow"},rationale="test",created_by=user.id),GovernancePolicy(tenant_id=tenant.id,organization_id=org.id,environment_id=env.id,name="compliance-check-policy",policy_type="compliance_check",status="published",version=1,rules={"decision":"allow"},rationale="test",created_by=user.id),GovernancePolicy(tenant_id=tenant.id,organization_id=org.id,environment_id=env.id,name="compliance-remediation-policy",policy_type="compliance_remediation",status="published",version=1,rules={"decision":"allow"},rationale="test",created_by=user.id)])
 await db.flush()
 svc=ComplianceService(db,scope,user.id)
 framework=await svc.create_framework(framework_type="qms",name="controlled-records",version="1",configuration={},controls=[{"control_key":"approved_doc","name":"Approved document","description":"Configured document gate","severity":"high","source_reference":"policy://qms/1","required_evidence":["evidence://doc/1"],"rules":{"controlled_document":True,"document_requirements":{"revision":"4","owner":"ops"},"approved_states":["approved"]},"active":True}])
 output=await svc.evaluate(framework_id=framework.id,subject_type="document",subject_id="doc-1",observed={"document":{"revision":"3","owner":"ops","approval_state":"approved"}},evidence_references=["evidence://doc/1"])
 assert output[0]["finding_id"] and output[0]["result"]=="needs_review"
 finding=await db.get(ComplianceFinding,uuid.UUID(output[0]["finding_id"]))
 assert finding and finding.tenant_id==tenant.id and finding.environment_id==env.id and finding.review_required
 remediation=await svc.create_remediation(finding.id,owner_id=user.id)
 await db.flush()
 assert remediation.status=="open"
 tenant_b,_org_b,_env_b,_user_b,scope_b=await _scope_and_user(db,"compliance-other")
 with pytest.raises(BoundaryDenied):
  from app.compliance.repository import get_finding
  await get_finding(db,scope_b,finding.id)
 assert await db.scalar(select(Remediation).where(Remediation.tenant_id==tenant_b.id)) is None
