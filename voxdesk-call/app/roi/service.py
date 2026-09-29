from __future__ import annotations
import datetime as dt, uuid
from typing import Any
from sqlalchemy import JSON, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column
from app.db.models import Base, UsageEvent
from app.roi.cost_engine import calculate_capacity
from app.roi.kpi_engine import evaluate_kpi,validate_kpi_definition
from app.governance.context import GovernanceScope
from app.governance.audit import record_governance_audit
from app.governance.evidence import append_event
from app.governance.hashing import sha256_hex
from app.governance.policy import require_policy
from app.tenancy.isolation import BoundaryDenied, ValidationFailed

def now():return dt.datetime.now(dt.timezone.utc)
def _validate_period(period:str)->None:
 if not isinstance(period,str) or len(period)!=7 or period[4]!="-" or not period[:4].isdigit() or not period[5:].isdigit() or not 1<=int(period[5:])<=12:raise ValidationFailed("measurement period must use YYYY-MM format")
def _validate_metrics(metrics:dict,allowed:set[str])->None:
 import math
 if not isinstance(metrics,dict) or not metrics.keys()<=allowed:raise ValidationFailed("unsupported measurement metric")
 for key,value in metrics.items():
  if value is not None and (not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(float(value)) or value<0):raise ValidationFailed(f"invalid value for measurement metric {key}")
def _validate_references(refs:list[str])->None:
 if not isinstance(refs,list) or len(refs)>100 or any(not isinstance(r,str) or not r.strip() or len(r)>500 or "\n" in r or "\r" in r for r in refs):raise ValidationFailed("invalid measurement source reference")
class ROIBaseline(Base):
 __tablename__="roi_baselines"
 __table_args__=(UniqueConstraint("tenant_id","environment_id","period","assumptions_version",name="uq_roi_baseline_period"),Index("ix_roi_baseline_scope","tenant_id","organization_id","environment_id","period"))
 id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
 tenant_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("tenants.id",ondelete="CASCADE"),nullable=False,index=True); organization_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id",ondelete="CASCADE"),nullable=False,index=True); environment_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("environments.id",ondelete="RESTRICT"),nullable=False,index=True)
 period:Mapped[str]=mapped_column(String(32),nullable=False); metrics:Mapped[dict]=mapped_column(JSON,nullable=False); assumptions_version:Mapped[str]=mapped_column(String(100),nullable=False); source_references:Mapped[list]=mapped_column(JSON,nullable=False,default=list); created_by:Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("users.id",ondelete="SET NULL")); created_at:Mapped[dt.datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)
class ROIOutcome(Base):
 __tablename__="roi_outcomes"
 __table_args__=(UniqueConstraint("tenant_id","environment_id","idempotency_key",name="uq_roi_outcome_idempotency"),Index("ix_roi_outcome_scope_period","tenant_id","organization_id","environment_id","period"))
 id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); tenant_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("tenants.id",ondelete="CASCADE"),nullable=False,index=True); organization_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id",ondelete="CASCADE"),nullable=False,index=True); environment_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("environments.id",ondelete="RESTRICT"),nullable=False,index=True)
 period:Mapped[str]=mapped_column(String(32),nullable=False); metrics:Mapped[dict]=mapped_column(JSON,nullable=False); source_references:Mapped[list]=mapped_column(JSON,nullable=False); idempotency_key:Mapped[str]=mapped_column(String(200),nullable=False); created_at:Mapped[dt.datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)
class ROICost(Base):
 __tablename__="roi_costs"
 __table_args__=(UniqueConstraint("tenant_id","environment_id","source_fingerprint",name="uq_roi_cost_source"),Index("ix_roi_cost_scope_period","tenant_id","organization_id","environment_id","period"))
 id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); tenant_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("tenants.id",ondelete="CASCADE"),nullable=False,index=True); organization_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id",ondelete="CASCADE"),nullable=False,index=True); environment_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("environments.id",ondelete="RESTRICT"),nullable=False,index=True)
 period:Mapped[str]=mapped_column(String(32),nullable=False); component:Mapped[str]=mapped_column(String(40),nullable=False); amount:Mapped[float|None]=mapped_column(Numeric(18,6)); currency:Mapped[str]=mapped_column(String(8),nullable=False); source_reference:Mapped[str]=mapped_column(String(500),nullable=False); source_fingerprint:Mapped[str]=mapped_column(String(64),nullable=False); created_at:Mapped[dt.datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)
class ROIKPI(Base):
 __tablename__="roi_kpis"
 __table_args__=(UniqueConstraint("tenant_id","environment_id","key","version",name="uq_roi_kpi_version"),Index("ix_roi_kpi_scope_active","tenant_id","organization_id","environment_id","active"))
 id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); tenant_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("tenants.id",ondelete="CASCADE"),nullable=False,index=True); organization_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id",ondelete="CASCADE"),nullable=False,index=True); environment_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("environments.id",ondelete="RESTRICT"),nullable=False,index=True)
 key:Mapped[str]=mapped_column(String(100),nullable=False); name:Mapped[str]=mapped_column(String(200),nullable=False); source_definition:Mapped[dict]=mapped_column(JSON,nullable=False); formula:Mapped[dict]=mapped_column(JSON,nullable=False); unit:Mapped[str]=mapped_column(String(40),nullable=False); aggregation:Mapped[str]=mapped_column(String(40),nullable=False); version:Mapped[int]=mapped_column(nullable=False); active:Mapped[bool]=mapped_column(nullable=False,default=True); created_at:Mapped[dt.datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)
class ROIResult(Base):
 __tablename__="roi_results"
 __table_args__=(UniqueConstraint("tenant_id","environment_id","baseline_id","outcome_id",name="uq_roi_result_inputs"),Index("ix_roi_result_scope_period","tenant_id","organization_id","environment_id","period"))
 id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); tenant_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("tenants.id",ondelete="CASCADE"),nullable=False,index=True); organization_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id",ondelete="CASCADE"),nullable=False,index=True); environment_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("environments.id",ondelete="RESTRICT"),nullable=False,index=True)
 period:Mapped[str]=mapped_column(String(32),nullable=False); baseline_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("roi_baselines.id",ondelete="RESTRICT"),nullable=False); outcome_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("roi_outcomes.id",ondelete="RESTRICT"),nullable=False); result:Mapped[dict]=mapped_column(JSON,nullable=False); data_quality:Mapped[str]=mapped_column(String(32),nullable=False); calculation_version:Mapped[str]=mapped_column(String(100),nullable=False); assumptions_version:Mapped[str]=mapped_column(String(100),nullable=False); evidence_event_id:Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("governance_evidence_events.id")); created_at:Mapped[dt.datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)
class ROIKPIEvaluation(Base):
 __tablename__="roi_kpi_evaluations"
 id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4); tenant_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("tenants.id",ondelete="CASCADE"),nullable=False,index=True); organization_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id",ondelete="CASCADE"),nullable=False,index=True); environment_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("environments.id",ondelete="RESTRICT"),nullable=False,index=True); kpi_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("roi_kpis.id"),nullable=False); period:Mapped[str]=mapped_column(String(32),nullable=False); result:Mapped[dict]=mapped_column(JSON,nullable=False); data_quality:Mapped[str]=mapped_column(String(32),nullable=False); evidence_event_id:Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("governance_evidence_events.id")); created_at:Mapped[dt.datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)

class ROIService:
 calculation_version="1"
 def __init__(self,session:AsyncSession,scope:GovernanceScope,actor_id:uuid.UUID):
  if scope.environment_id is None:raise ValidationFailed("environment scope required")
  self.session,self.scope,self.actor_id=session,scope,actor_id
 async def _policy(self,operation,context):return await require_policy(self.session,self.scope,policy_type="roi_measurement",context={"operation":operation,**context},principal_id=self.actor_id)
 async def create_baseline(self,metrics:dict,period:str,assumptions_version:str,source_references:list[str]):
  _validate_period(period);_validate_metrics(metrics,{"volume","labor_hours","labor_cost","handle_time","escalation_rate","system_cost"});_validate_references(source_references)
  if metrics.get("escalation_rate") is not None and metrics["escalation_rate"]>1:raise ValidationFailed("escalation_rate must be between 0 and 1")
  await self._policy("create_baseline",{"period":period,"source_count":len(source_references)})
  existing=await self.session.scalar(select(ROIBaseline).where(ROIBaseline.tenant_id==self.scope.tenant_id,ROIBaseline.organization_id==self.scope.organization_id,ROIBaseline.environment_id==self.scope.environment_id,ROIBaseline.period==period,ROIBaseline.assumptions_version==assumptions_version))
  if existing:
   if existing.metrics!=metrics or existing.source_references!=source_references:raise ValidationFailed("baseline period/version already has different inputs")
   return existing
  row=ROIBaseline(tenant_id=self.scope.tenant_id,organization_id=self.scope.organization_id,environment_id=self.scope.environment_id,period=period,metrics=metrics,assumptions_version=assumptions_version,source_references=source_references,created_by=self.actor_id);self.session.add(row);await self.session.flush();await record_governance_audit(self.session,self.scope,event="roi_baseline_created",actor_user_id=self.actor_id,detail={"baseline_id":str(row.id),"period":period});await append_event(self.session,self.scope,event_type="roi_baseline_created",payload={"baseline_id":str(row.id),"period":period,"assumptions_version":assumptions_version,"metrics_fingerprint":sha256_hex(metrics),"source_references":source_references},actor_user_id=self.actor_id,subject_type="roi_baseline",subject_id=str(row.id));return row
 async def record_outcome(self,metrics:dict,period:str,idempotency_key:str,source_references:list[str]):
  _validate_period(period);_validate_metrics(metrics,{"interactions_handled","tasks_completed","records_updated","escalations","successful_actions","minutes_handled","human_hours"});_validate_references(source_references)
  if not isinstance(idempotency_key,str) or len(idempotency_key)<8 or len(idempotency_key)>200:raise ValidationFailed("valid outcome idempotency key is required")
  await self._policy("record_outcome",{"period":period,"source_count":len(source_references)})
  old=await self.session.scalar(select(ROIOutcome).where(ROIOutcome.tenant_id==self.scope.tenant_id,ROIOutcome.organization_id==self.scope.organization_id,ROIOutcome.environment_id==self.scope.environment_id,ROIOutcome.idempotency_key==idempotency_key))
  if old:
   if old.metrics!=metrics or old.period!=period or old.source_references!=source_references:raise ValidationFailed("idempotency key input conflict")
   return old
  row=ROIOutcome(tenant_id=self.scope.tenant_id,organization_id=self.scope.organization_id,environment_id=self.scope.environment_id,period=period,metrics=metrics,source_references=source_references,idempotency_key=idempotency_key);self.session.add(row);await self.session.flush();await record_governance_audit(self.session,self.scope,event="roi_outcome_recorded",actor_user_id=self.actor_id,detail={"outcome_id":str(row.id),"period":period});await append_event(self.session,self.scope,event_type="roi_outcome_recorded",payload={"outcome_id":str(row.id),"period":period,"metrics_fingerprint":sha256_hex(metrics),"source_references":source_references},actor_user_id=self.actor_id,subject_type="roi_outcome",subject_id=str(row.id));return row
 async def record_cost(self,*,period:str,component:str,amount:float|None,currency:str,source_reference:str):
  _validate_period(period)
  if component not in {"llm_cost","stt_cost","tts_cost","telephony_cost","connector_cost","infrastructure_cost","labor_review_cost"}:
   raise ValidationFailed("unsupported cost component")
  if amount is not None and (not isinstance(amount,(int,float)) or isinstance(amount,bool) or amount<0):raise ValidationFailed("cost must be a non-negative numeric amount or unavailable")
  if not isinstance(currency,str) or not 3<=len(currency)<=8 or not currency.isalpha():raise ValidationFailed("currency must be a 3-8 character alphabetic code")
  currency=currency.upper()
  if not isinstance(source_reference,str) or len(source_reference)>500:raise ValidationFailed("cost source reference is invalid")
  try: event_id=uuid.UUID(source_reference)
  except ValueError as exc:raise ValidationFailed("cost must reference an existing usage event UUID") from exc
  await self._policy("record_cost",{"period":period,"component":component,"usage_event_id":str(event_id)})
  event=await self.session.scalar(select(UsageEvent).where(UsageEvent.id==event_id,UsageEvent.tenant_id==self.scope.tenant_id,UsageEvent.environment_id==self.scope.environment_id))
  if event is None:raise BoundaryDenied()
  if period!=event.billing_period:
   raise ValidationFailed("cost period must match the persisted usage event billing period")
  recorded=(event.event_metadata or {}).get("recorded_costs",{}).get(component)
  recorded_amount=recorded.get("amount") if isinstance(recorded,dict) else None
  recorded_currency=recorded.get("currency") if isinstance(recorded,dict) else None
  if amount is not None and (recorded_amount is None or float(recorded_amount)!=float(amount) or recorded_currency!=currency):
   raise ValidationFailed("cost amount and currency must match the persisted usage-event cost ledger")
  if amount is None and recorded_amount is not None:
   amount=float(recorded_amount)
   if recorded_currency:currency=str(recorded_currency)
  fp=sha256_hex({"usage_event_id":str(event.id),"component":component})
  old=await self.session.scalar(select(ROICost).where(ROICost.tenant_id==self.scope.tenant_id,ROICost.organization_id==self.scope.organization_id,ROICost.environment_id==self.scope.environment_id,ROICost.source_fingerprint==fp))
  if old:
   old_amount=float(old.amount) if old.amount is not None else None
   if old.period!=period or old_amount!=amount or old.currency!=currency:raise ValidationFailed("cost source already recorded with different values")
   return old
  row=ROICost(tenant_id=self.scope.tenant_id,organization_id=self.scope.organization_id,environment_id=self.scope.environment_id,period=period,component=component,amount=amount,currency=currency,source_reference=source_reference,source_fingerprint=fp);self.session.add(row);await self.session.flush();await record_governance_audit(self.session,self.scope,event="roi_cost_recorded",actor_user_id=self.actor_id,detail={"cost_id":str(row.id),"component":component,"period":period,"amount_status":"recorded" if amount is not None else "NOT_AVAILABLE"});await append_event(self.session,self.scope,event_type="roi_cost_recorded",payload={"cost_id":str(row.id),"component":component,"period":period,"amount_fingerprint":sha256_hex(str(amount)) if amount is not None else None,"source_reference":source_reference},actor_user_id=self.actor_id,subject_type="roi_cost",subject_id=str(row.id));return row
 async def calculate(self,baseline_id:uuid.UUID,outcome_id:uuid.UUID):
  await self._policy("calculate",{"baseline_id":str(baseline_id),"outcome_id":str(outcome_id)})
  baseline=await self.session.scalar(select(ROIBaseline).where(ROIBaseline.id==baseline_id,ROIBaseline.tenant_id==self.scope.tenant_id,ROIBaseline.organization_id==self.scope.organization_id,ROIBaseline.environment_id==self.scope.environment_id))
  outcome=await self.session.scalar(select(ROIOutcome).where(ROIOutcome.id==outcome_id,ROIOutcome.tenant_id==self.scope.tenant_id,ROIOutcome.organization_id==self.scope.organization_id,ROIOutcome.environment_id==self.scope.environment_id))
  if baseline is None or outcome is None:raise BoundaryDenied()
  existing_result=await self.session.scalar(select(ROIResult).where(ROIResult.tenant_id==self.scope.tenant_id,ROIResult.organization_id==self.scope.organization_id,ROIResult.environment_id==self.scope.environment_id,ROIResult.baseline_id==baseline.id,ROIResult.outcome_id==outcome.id))
  if existing_result:return existing_result
  costs=list((await self.session.scalars(select(ROICost).where(ROICost.tenant_id==self.scope.tenant_id,ROICost.organization_id==self.scope.organization_id,ROICost.environment_id==self.scope.environment_id,ROICost.period==baseline.period))).all())
  components={c:None for c in ("llm_cost","stt_cost","tts_cost","telephony_cost","connector_cost","infrastructure_cost","labor_review_cost")}
  for cost in costs:
   if cost.component in components and cost.amount is not None:components[cost.component]=(components[cost.component] or 0)+float(cost.amount)
  # Runtime-measured usage-event prices are part of the same source ledger.
  # Use them directly when no explicit ROICost projection exists, and never
  # double-count an event/component already projected by record_cost().
  usage_events=list((await self.session.scalars(select(UsageEvent).where(UsageEvent.tenant_id==self.scope.tenant_id,UsageEvent.environment_id==self.scope.environment_id,UsageEvent.billing_period==baseline.period))).all())
  cost_fingerprints={cost.source_fingerprint for cost in costs}
  unknown_components=set()
  derived_cost_references=[]
  currencies={cost.currency for cost in costs}
  import math
  for event in usage_events:
   ledger=(event.event_metadata or {}).get("recorded_costs",{})
   if not isinstance(ledger,dict):continue
   for component,entry in ledger.items():
    if component not in components or not isinstance(entry,dict):continue
    fingerprint=sha256_hex({"usage_event_id":str(event.id),"component":component})
    if fingerprint in cost_fingerprints:continue
    amount=entry.get("amount")
    currency=entry.get("currency")
    if isinstance(currency,str) and currency.isalpha() and 3<=len(currency)<=8:currencies.add(currency.upper())
    else:unknown_components.add(component);continue
    if amount is None or isinstance(amount,bool) or not isinstance(amount,(int,float)) or not math.isfinite(float(amount)) or amount<0:
     unknown_components.add(component)
    else:
     components[component]=(components[component] or 0)+float(amount)
    derived_cost_references.append(str(event.id))
  for component in unknown_components:components[component]=None
  missing=[key for key,value in components.items() if value is None]
  currency_mismatch=len(currencies)>1
  reason=("missing persisted cost components: "+", ".join(missing)) if missing else ("persisted cost currencies differ; no conversion source is configured" if currency_mismatch else None)
  total=None if missing or currency_mismatch else sum(components.values())
  def _unit_cost(count):return {"value":total/count,"data_quality":"recorded_costs"} if total is not None and isinstance(count,(int,float)) and count>0 else {"value":None,"data_quality":"NOT_AVAILABLE","reason":reason or "required outcome count unavailable or zero"}
  cost_result={"total_cost":{"value":total,"status":"NOT_AVAILABLE" if total is None else "recorded_costs","reason":reason},"cost_per_interaction":_unit_cost(outcome.metrics.get("interactions_handled")),"cost_per_outcome":_unit_cost(outcome.metrics.get("tasks_completed")),"cost_per_successful_action":_unit_cost(outcome.metrics.get("successful_actions")),"human_review_cost":{"value":components.get("labor_review_cost"),"data_quality":"recorded_costs" if components.get("labor_review_cost") is not None else "NOT_AVAILABLE"},"blended_cost":{"value":total,"data_quality":"recorded_costs" if total is not None else "NOT_AVAILABLE"},"components":components,"currency":next(iter(currencies)) if len(currencies)==1 else None}
  capacity=calculate_capacity(baseline.metrics.get("labor_hours"),outcome.metrics.get("human_hours"))
  data_quality="NOT_AVAILABLE"
  result={"period":baseline.period,"source_references":{"baseline":baseline.source_references,"outcome":outcome.source_references,"costs":sorted({c.source_reference for c in costs}|set(derived_cost_references))},"source_provenance":"UNVERIFIED_REFERENCE_RESOLVER_UNAVAILABLE","calculation_version":self.calculation_version,"assumptions_version":baseline.assumptions_version,"data_quality":data_quality,"cost":cost_result,"capacity":capacity,"roi":{"value":None,"status":"NOT_AVAILABLE","reason":"monetary benefit evidence is not present in the source ledger"},"generated_at":now().isoformat(),"reason":"caller-provided baseline/outcome source references cannot be verified against an authoritative source registry"}
  if baseline.period!=outcome.period:result["reason"]="baseline/outcome periods differ"
  row=ROIResult(tenant_id=self.scope.tenant_id,organization_id=self.scope.organization_id,environment_id=self.scope.environment_id,period=baseline.period,baseline_id=baseline.id,outcome_id=outcome.id,result=result,data_quality=data_quality,calculation_version=self.calculation_version,assumptions_version=baseline.assumptions_version);self.session.add(row);await self.session.flush()
  ev=await append_event(self.session,self.scope,event_type="roi_result_calculated",payload={"result_id":str(row.id),"period":row.period,"data_quality":data_quality,"calculation_version":self.calculation_version,"assumptions_version":baseline.assumptions_version,"input_fingerprints":[sha256_hex(baseline.metrics),sha256_hex(outcome.metrics)]},actor_user_id=self.actor_id,subject_type="roi_result",subject_id=str(row.id));row.evidence_event_id=ev.id;await record_governance_audit(self.session,self.scope,event="roi_result_calculated",actor_user_id=self.actor_id,detail={"result_id":str(row.id),"period":row.period,"data_quality":data_quality});return row
 async def create_kpi(self,*,key,name,source_definition,formula,unit,aggregation):
  validate_kpi_definition({"key":key,"name":name,"source_definition":source_definition,"formula":formula,"unit":unit,"aggregation":aggregation})
  await self._policy("create_kpi",{"key":key})
  latest=await self.session.scalar(select(ROIKPI).where(ROIKPI.tenant_id==self.scope.tenant_id,ROIKPI.environment_id==self.scope.environment_id,ROIKPI.key==key).order_by(ROIKPI.version.desc()).limit(1))
  row=ROIKPI(tenant_id=self.scope.tenant_id,organization_id=self.scope.organization_id,environment_id=self.scope.environment_id,key=key,name=name,source_definition=source_definition,formula=formula,unit=unit,aggregation=aggregation,version=latest.version+1 if latest else 1,active=True);self.session.add(row);await self.session.flush();await record_governance_audit(self.session,self.scope,event="roi_kpi_created",actor_user_id=self.actor_id,detail={"kpi_id":str(row.id),"key":key,"version":row.version});await append_event(self.session,self.scope,event_type="roi_kpi_created",payload={"kpi_id":str(row.id),"key":key,"version":row.version,"source_definition_fingerprint":sha256_hex(source_definition),"formula_fingerprint":sha256_hex(formula)},actor_user_id=self.actor_id,subject_type="roi_kpi",subject_id=str(row.id));return row
 async def evaluate_kpi(self,kpi_id:uuid.UUID,period:str):
  _validate_period(period)
  await self._policy("evaluate_kpi",{"kpi_id":str(kpi_id),"period":period})
  kpi=await self.session.scalar(select(ROIKPI).where(ROIKPI.id==kpi_id,ROIKPI.tenant_id==self.scope.tenant_id,ROIKPI.organization_id==self.scope.organization_id,ROIKPI.environment_id==self.scope.environment_id,ROIKPI.active.is_(True)))
  if kpi is None:raise BoundaryDenied()
  # The supported event sources are durable usage rows only; unsupported event kinds fail closed.
  events=list((await self.session.scalars(select(UsageEvent).where(UsageEvent.tenant_id==self.scope.tenant_id,UsageEvent.environment_id==self.scope.environment_id,UsageEvent.billing_period==period))).all())
  norm=[{"event_type":str(e.event_type.value if hasattr(e.event_type,"value") else e.event_type),"quantity":e.quantity,"metric":str(e.metric.value if hasattr(e.metric,"value") else e.metric),"source_reference":e.source_reference} for e in events]
  result=evaluate_kpi({"period":period,"source_definition":kpi.source_definition,"formula":kpi.formula,"unit":kpi.unit,"aggregation":kpi.aggregation},norm,period=period)
  row=ROIKPIEvaluation(tenant_id=self.scope.tenant_id,organization_id=self.scope.organization_id,environment_id=self.scope.environment_id,kpi_id=kpi.id,period=period,result=result,data_quality=result.get("data_quality","NOT_AVAILABLE"));self.session.add(row);await self.session.flush()
  evidence=await append_event(self.session,self.scope,event_type="roi_kpi_evaluated",payload={"evaluation_id":str(row.id),"kpi_id":str(kpi.id),"period":period,"data_quality":row.data_quality,"source_event_count":len(norm)},actor_user_id=self.actor_id,subject_type="roi_kpi",subject_id=str(kpi.id));row.evidence_event_id=evidence.id;await record_governance_audit(self.session,self.scope,event="roi_kpi_evaluated",actor_user_id=self.actor_id,detail={"evaluation_id":str(row.id),"kpi_id":str(kpi.id),"period":period,"data_quality":row.data_quality});return row
 async def list_baselines(self):return list((await self.session.scalars(select(ROIBaseline).where(ROIBaseline.tenant_id==self.scope.tenant_id,ROIBaseline.organization_id==self.scope.organization_id,ROIBaseline.environment_id==self.scope.environment_id).order_by(ROIBaseline.created_at.desc()))).all())
 async def list_kpis(self):return list((await self.session.scalars(select(ROIKPI).where(ROIKPI.tenant_id==self.scope.tenant_id,ROIKPI.organization_id==self.scope.organization_id,ROIKPI.environment_id==self.scope.environment_id,ROIKPI.active.is_(True)))).all())
 async def get_result(self,result_id):
  row=await self.session.scalar(select(ROIResult).where(ROIResult.id==result_id,ROIResult.tenant_id==self.scope.tenant_id,ROIResult.organization_id==self.scope.organization_id,ROIResult.environment_id==self.scope.environment_id))
  if row is None:raise BoundaryDenied()
  return row
 async def measurement_period(self,period):
  _validate_period(period)
  baselines=list((await self.session.scalars(select(ROIBaseline).where(ROIBaseline.tenant_id==self.scope.tenant_id,ROIBaseline.organization_id==self.scope.organization_id,ROIBaseline.environment_id==self.scope.environment_id,ROIBaseline.period==period))).all())
  outcomes=list((await self.session.scalars(select(ROIOutcome).where(ROIOutcome.tenant_id==self.scope.tenant_id,ROIOutcome.organization_id==self.scope.organization_id,ROIOutcome.environment_id==self.scope.environment_id,ROIOutcome.period==period))).all())
  result_rows=list((await self.session.scalars(select(ROIResult).where(ROIResult.tenant_id==self.scope.tenant_id,ROIResult.organization_id==self.scope.organization_id,ROIResult.environment_id==self.scope.environment_id,ROIResult.period==period).order_by(ROIResult.created_at.desc()))).all())
  return {"period":period,"baseline_count":len(baselines),"outcome_count":len(outcomes),"result_count":len(result_rows),"results":[{"id":str(r.id),"data_quality":r.data_quality,"result":r.result} for r in result_rows],"data_quality":"NOT_AVAILABLE","record_presence":"present" if baselines and outcomes else "missing","reason":"source provenance cannot be independently validated; source registry integration is unavailable" if baselines and outcomes else "baseline or outcome records missing"}
 async def compare_periods(self,first_period:str,second_period:str):
  _validate_period(first_period);_validate_period(second_period)
  if first_period==second_period:raise ValidationFailed("comparison periods must be distinct")
  first=await self.measurement_period(first_period);second=await self.measurement_period(second_period)
  if first["data_quality"]!="available" or second["data_quality"]!="available":return {"periods":[first_period,second_period],"data_quality":"NOT_AVAILABLE","reason":"source records required for both periods are unavailable"}
  first_outcomes=list((await self.session.scalars(select(ROIOutcome).where(ROIOutcome.tenant_id==self.scope.tenant_id,ROIOutcome.organization_id==self.scope.organization_id,ROIOutcome.environment_id==self.scope.environment_id,ROIOutcome.period==first_period))).all())
  second_outcomes=list((await self.session.scalars(select(ROIOutcome).where(ROIOutcome.tenant_id==self.scope.tenant_id,ROIOutcome.organization_id==self.scope.organization_id,ROIOutcome.environment_id==self.scope.environment_id,ROIOutcome.period==second_period))).all())
  def means(rows):
   keys=set.intersection(*(set((r.metrics or {}).keys()) for r in rows)) if rows else set();out={}
   for key in keys:
    vals=[r.metrics[key] for r in rows]
    if all(isinstance(v,(int,float)) and not isinstance(v,bool) for v in vals):out[key]=sum(vals)/len(vals)
   return out
  left,right=means(first_outcomes),means(second_outcomes);deltas={key:{"first_period_mean":left[key],"second_period_mean":right[key],"observed_delta":right[key]-left[key],"data_quality":"recorded_outcomes"} for key in left.keys()&right.keys()}
  return {"periods":[first_period,second_period],"data_quality":"available","first_result_ids":[r["id"] for r in first["results"]],"second_result_ids":[r["id"] for r in second["results"]],"outcome_metric_comparisons":deltas,"monetary_savings":"NOT_AVAILABLE","guaranteed_savings":False,"reason":"Observed period differences are descriptive, not causal; no monetary benefit is inferred"}
