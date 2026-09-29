from __future__ import annotations
import pytest
from app.governance.models import GovernancePolicy
from app.roi.service import ROIService
from app.roi.cost_engine import calculate_cost,calculate_capacity
from app.roi.kpi_engine import evaluate_kpi
from app.tenancy.isolation import BoundaryDenied
from tests.specialized_agents.test_executor import _scope_and_user

@pytest.mark.asyncio
async def test_baseline_outcome_result_persistence_missing_costs_and_scope(db):
 tenant,org,env,user,scope=await _scope_and_user(db,"roi-scope")
 db.add(GovernancePolicy(tenant_id=tenant.id,organization_id=org.id,environment_id=env.id,name="roi-policy",policy_type="roi_measurement",status="published",version=1,rules={"decision":"allow"},rationale="test",created_by=user.id))
 await db.flush()
 svc=ROIService(db,scope,user.id)
 baseline=await svc.create_baseline({"volume":100,"labor_hours":40,"labor_cost":None},"2026-09","assumptions-v1",["ops://baseline/1"])
 outcome=await svc.record_outcome({"interactions_handled":20,"tasks_completed":15,"human_hours":12},"2026-09","roi-outcome-idem-01",["ops://outcome/1"])
 assert await svc.record_outcome(outcome.metrics,outcome.period,"roi-outcome-idem-01",outcome.source_references) is outcome
 result=await svc.calculate(baseline.id,outcome.id)
 assert result.data_quality=="NOT_AVAILABLE" and result.result["source_provenance"]=="UNVERIFIED_REFERENCE_RESOLVER_UNAVAILABLE" and result.result["cost"]["total_cost"]["status"]=="NOT_AVAILABLE"
 assert result.result["capacity"]["observed_human_hours_delta"]["value"]==28.0
 assert result.result["capacity"]["capacity_unlocked"]["data_quality"]=="NOT_AVAILABLE"
 assert result.evidence_event_id is not None and result.calculation_version and result.assumptions_version=="assumptions-v1"
 other_tenant,_o,_e,_u,other_scope=await _scope_and_user(db,"roi-other")
 with pytest.raises(BoundaryDenied):
  await ROIService(db,other_scope,user.id).get_result(result.id)

@pytest.mark.asyncio
async def test_kpi_definition_and_measurement_are_data_quality_gated(db):
 tenant,org,env,user,scope=await _scope_and_user(db,"roi-kpi")
 db.add(GovernancePolicy(tenant_id=tenant.id,organization_id=org.id,environment_id=env.id,name="roi-policy",policy_type="roi_measurement",status="published",version=1,rules={"decision":"allow"},rationale="test",created_by=user.id))
 await db.flush()
 svc=ROIService(db,scope,user.id)
 kpi=await svc.create_kpi(key="task_completion_rate",name="Task completion rate",source_definition={"event_type":"task_completed"},formula={"numerator_field":"quantity","denominator_field":"attempts"},unit="ratio",aggregation="ratio")
 evaluated=await svc.evaluate_kpi(kpi.id,"2026-09")
 assert evaluated.data_quality=="NOT_AVAILABLE" and evaluated.result["value"] is None
 assert evaluated.evidence_event_id is not None


def test_cost_capacity_and_kpi_pure_deterministic_unavailable_behavior():
 missing=calculate_cost({},interactions=4,completed_outcomes=2,successful_actions=1)
 assert missing["total_cost"]["status"]=="NOT_AVAILABLE"
 capacity=calculate_capacity(None,3)
 assert capacity["observed_human_hours_delta"]["data_quality"]=="NOT_AVAILABLE"
 assert capacity["capacity_unlocked"]["data_quality"]=="NOT_AVAILABLE"
 observed=calculate_capacity(40,12)
 assert observed["observed_human_hours_delta"]["value"]==28
 assert observed["capacity_unlocked"]["value"] is None
 result=evaluate_kpi({"source_definition":{"event_type":"missing"},"formula":{"numerator_field":"n","denominator_field":"d"}},[],period="2026-09")
 assert result["data_quality"]=="NOT_AVAILABLE"
