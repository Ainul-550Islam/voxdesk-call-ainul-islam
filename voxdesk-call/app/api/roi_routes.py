from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth.dependencies import TenantContext,require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.governance.context import resolve_scope
from app.roi.schemas import BaselineInput,OutcomeInput,CostInput,KPIInput,CalculateInput
from app.roi.service import ROIService
from app.tenancy.isolation import BoundaryDenied,HierarchyError,to_http

router=APIRouter(prefix="/api/roi",tags=["roi"])

@router.post("/baselines",status_code=201)
async def create_baseline(body:BaselineInput,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_WRITE)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,body.environment_id);data=body.model_dump();env=data.pop("environment_id");period=data.pop("period");refs=data.pop("source_references");assumptions=data.pop("assumptions_version");row=await ROIService(session,scope,ctx.user_id).create_baseline(data,period,assumptions,refs);await session.commit();return {"id":str(row.id),"period":row.period,"assumptions_version":row.assumptions_version,"data_quality":"recorded_inputs"}
 except HierarchyError as exc:raise to_http(exc) from None
@router.get("/baselines")
async def list_baselines(environment_id:uuid.UUID,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_READ)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);rows=await ROIService(session,scope,ctx.user_id).list_baselines();return [{"id":str(r.id),"period":r.period,"metrics":r.metrics,"assumptions_version":r.assumptions_version,"source_references":r.source_references} for r in rows]
 except HierarchyError as exc:raise to_http(exc) from None
@router.post("/outcomes",status_code=201)
async def record_outcome(body:OutcomeInput,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_WRITE)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,body.environment_id);data=body.model_dump();data.pop("environment_id");period=data.pop("period");key=data.pop("idempotency_key");refs=data.pop("source_references");row=await ROIService(session,scope,ctx.user_id).record_outcome(data,period,key,refs);await session.commit();return {"id":str(row.id),"period":row.period,"idempotency":"tenant-and-environment-scoped","data_quality":"recorded_inputs"}
 except HierarchyError as exc:raise to_http(exc) from None
@router.post("/costs",status_code=201)
async def record_cost(body:CostInput,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_WRITE)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,body.environment_id);row=await ROIService(session,scope,ctx.user_id).record_cost(period=body.period,component=body.component,amount=body.amount,currency=body.currency,source_reference=body.source_reference);await session.commit();return {"id":str(row.id),"period":row.period,"component":row.component,"source_reference":row.source_reference,"data_quality":"source_usage_event_linked" if row.amount is not None else "NOT_AVAILABLE"}
 except HierarchyError as exc:raise to_http(exc) from None
@router.post("/calculate",status_code=201)
async def calculate(body:CalculateInput,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_WRITE)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,body.environment_id);row=await ROIService(session,scope,ctx.user_id).calculate(body.baseline_id,body.outcome_id);await session.commit();return {"id":str(row.id),"period":row.period,"result":row.result,"data_quality":row.data_quality,"calculation_version":row.calculation_version,"assumptions_version":row.assumptions_version}
 except HierarchyError as exc:raise to_http(exc) from None
@router.get("/results/{result_id}")
async def get_result(result_id:uuid.UUID,environment_id:uuid.UUID,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_READ)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);r=await ROIService(session,scope,ctx.user_id).get_result(result_id);return {"id":str(r.id),"period":r.period,"result":r.result,"data_quality":r.data_quality,"calculation_version":r.calculation_version,"assumptions_version":r.assumptions_version,"evidence_event_id":str(r.evidence_event_id) if r.evidence_event_id else None}
 except HierarchyError as exc:raise to_http(exc) from None
@router.post("/kpis",status_code=201)
async def create_kpi(body:KPIInput,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_WRITE)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,body.environment_id);data=body.model_dump();data.pop("environment_id");row=await ROIService(session,scope,ctx.user_id).create_kpi(**data);await session.commit();return {"id":str(row.id),"key":row.key,"version":row.version,"active":row.active}
 except HierarchyError as exc:raise to_http(exc) from None
@router.get("/kpis")
async def list_kpis(environment_id:uuid.UUID,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_READ)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);return [{"id":str(r.id),"key":r.key,"name":r.name,"version":r.version,"source_definition":r.source_definition,"formula":r.formula,"unit":r.unit,"aggregation":r.aggregation} for r in await ROIService(session,scope,ctx.user_id).list_kpis()]
 except HierarchyError as exc:raise to_http(exc) from None
@router.post("/kpis/{kpi_id}/evaluate")
async def evaluate(kpi_id:uuid.UUID,environment_id:uuid.UUID,period:str=Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_WRITE)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);row=await ROIService(session,scope,ctx.user_id).evaluate_kpi(kpi_id,period);await session.commit();return {"id":str(row.id),"kpi_id":str(row.kpi_id),"period":row.period,"result":row.result,"data_quality":row.data_quality,"evidence_event_id":str(row.evidence_event_id) if row.evidence_event_id else None}
 except HierarchyError as exc:raise to_http(exc) from None
@router.get("/measurement/{period}")
async def measurement(period:str,environment_id:uuid.UUID,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_READ)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);return await ROIService(session,scope,ctx.user_id).measurement_period(period)
 except HierarchyError as exc:raise to_http(exc) from None
@router.get("/compare")
async def compare_periods(first_period:str,second_period:str,environment_id:uuid.UUID,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_READ)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);return await ROIService(session,scope,ctx.user_id).compare_periods(first_period,second_period)
 except HierarchyError as exc:raise to_http(exc) from None
