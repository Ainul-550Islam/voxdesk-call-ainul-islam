from __future__ import annotations
import uuid
from fastapi import APIRouter,Depends,Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth.dependencies import TenantContext,require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.deployment.models import DeploymentReadiness,DeploymentRevision
from app.deployment.schemas import TargetInput,RevisionInput,VerificationInput
from app.deployment.service import DeploymentService
from app.deployment.runtime import enqueue_deployment
from app.governance.context import resolve_scope
from app.tenancy.isolation import HierarchyError,to_http

router=APIRouter(prefix="/api/deployment",tags=["deployment"])
def _target(row):return {"id":str(row.id),"target_type":row.target_type,"provider":row.provider,"region":row.region,"cluster_reference":row.cluster_reference,"network_mode":row.network_mode,"status":row.status,"environment_id":str(row.environment_id)}
def _revision(row):return {"id":str(row.id),"target_id":str(row.target_id),"revision_number":row.revision_number,"state":row.state,"artifact_reference":row.artifact_reference,"artifact_digest":row.artifact_digest,"configuration_fingerprint":row.configuration_fingerprint,"manifest_fingerprint":row.manifest_fingerprint,"migration_revision":row.migration_revision,"runtime_version":row.runtime_version,"verification_state":row.verification_state}
@router.get("/targets")
async def list_targets(environment_id:uuid.UUID,limit:int=Query(100,ge=1,le=200),ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_READ)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);rows=(await DeploymentService(session,scope,ctx.user_id).list_targets())[:limit];return [_target(r) for r in rows]
 except HierarchyError as exc:raise to_http(exc) from None
@router.post("/targets",status_code=201)
async def create_target(body:TargetInput,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_WRITE)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,body.environment_id);row=await DeploymentService(session,scope,ctx.user_id).create_target(**body.model_dump());await session.commit();return _target(row)
 except HierarchyError as exc:raise to_http(exc) from None
@router.get("/targets/{target_id}")
async def get_target(target_id:uuid.UUID,environment_id:uuid.UUID,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_READ)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);return _target(await DeploymentService(session,scope,ctx.user_id).get_target(target_id))
 except HierarchyError as exc:raise to_http(exc) from None
@router.post("/targets/{target_id}/validate")
async def validate_target(target_id:uuid.UUID,environment_id:uuid.UUID,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_WRITE)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);result=await DeploymentService(session,scope,ctx.user_id).validate(target_id);await session.commit();return result
 except HierarchyError as exc:raise to_http(exc) from None
@router.get("/{target_id}/readiness")
async def get_readiness(target_id:uuid.UUID,environment_id:uuid.UUID,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_READ)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);svc=DeploymentService(session,scope,ctx.user_id);await svc.get_target(target_id);row=await session.scalar(select(DeploymentReadiness).where(DeploymentReadiness.target_id==target_id,DeploymentReadiness.tenant_id==scope.tenant_id,DeploymentReadiness.organization_id==scope.organization_id,DeploymentReadiness.environment_id==scope.environment_id).order_by(DeploymentReadiness.created_at.desc()).limit(1));return {"target_id":str(target_id),"readiness":row.readiness if row else "NOT_READY","checks":row.checks if row else {},"reason":None if row else "no persisted readiness evaluation"}
 except HierarchyError as exc:raise to_http(exc) from None
@router.get("/targets/{target_id}/revisions")
async def list_revisions(target_id:uuid.UUID,environment_id:uuid.UUID,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_READ)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);return [_revision(r) for r in await DeploymentService(session,scope,ctx.user_id).list_revisions(target_id)]
 except HierarchyError as exc:raise to_http(exc) from None
@router.get("/revisions/{revision_id}")
async def get_revision(revision_id:uuid.UUID,environment_id:uuid.UUID,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_READ)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);return _revision(await DeploymentService(session,scope,ctx.user_id).get_revision(revision_id))
 except HierarchyError as exc:raise to_http(exc) from None
@router.post("/targets/{target_id}/revisions",status_code=201)
async def create_revision(target_id:uuid.UUID,environment_id:uuid.UUID,body:RevisionInput,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_WRITE)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);row=await DeploymentService(session,scope,ctx.user_id).create_revision(target_id,**body.model_dump());await session.commit();return _revision(row)
 except HierarchyError as exc:raise to_http(exc) from None
@router.post("/revisions/{revision_id}/approve")
async def request_approval(revision_id:uuid.UUID,environment_id:uuid.UUID,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_APPROVE)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);case=await DeploymentService(session,scope,ctx.user_id).request_approval(revision_id);await session.commit();return {"review_case_id":str(case.id),"status":case.status,"human_decision_recorded":False}
 except HierarchyError as exc:raise to_http(exc) from None
@router.post("/revisions/{revision_id}/deploy")
async def queue_deploy(revision_id:uuid.UUID,environment_id:uuid.UUID,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_WRITE)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);job,created=await enqueue_deployment(session,scope,ctx.user_id,revision_id);await session.commit();return {"job_id":str(job.id),"status":job.status,"created":created,"deployment_observed":False,"runtime_verified":False}
 except HierarchyError as exc:raise to_http(exc) from None
@router.post("/revisions/{revision_id}/verify")
async def verify(revision_id:uuid.UUID,environment_id:uuid.UUID,body:VerificationInput,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_WRITE)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);proof=await DeploymentService(session,scope,ctx.user_id).verify(revision_id,**body.model_dump());await session.commit();return {"id":str(proof.id),"state":proof.state,"runtime_verified":False}
 except HierarchyError as exc:raise to_http(exc) from None
@router.post("/revisions/{revision_id}/suspend")
async def suspend(revision_id:uuid.UUID,environment_id:uuid.UUID,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_WRITE)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);row=await DeploymentService(session,scope,ctx.user_id).transition(revision_id,"suspended");await session.commit();return _revision(row)
 except HierarchyError as exc:raise to_http(exc) from None
@router.post("/revisions/{revision_id}/retire")
async def retire(revision_id:uuid.UUID,environment_id:uuid.UUID,ctx:TenantContext=Depends(require_permission(Permission.GOVERNANCE_WRITE)),session:AsyncSession=Depends(get_session)):
 try:
  scope=await resolve_scope(session,ctx,environment_id);row=await DeploymentService(session,scope,ctx.user_id).transition(revision_id,"retired");await session.commit();return _revision(row)
 except HierarchyError as exc:raise to_http(exc) from None
