"""Tenant-scoped compliance framework, check, finding, remediation + QMS adapters API.

Preserves existing:
- Frameworks CRUD, checks, findings, remediations
- Industry template router include

Adds per GAP-P1-03:
- GET /api/compliance/qms/providers — list QMS providers (veeva, mastercontrol, etq)
- POST /api/compliance/qms/health — health check for configured QMS
- GET /api/compliance/qms/{provider}/documents — list docs (honest empty when not configured)
- GET /api/compliance/qms/{provider}/documents/{external_id}
- GET /api/compliance/qms/{provider}/traceability?subject_type=&subject_id=
- POST /api/compliance/qms/{provider}/audit-package — assemble audit package
- POST /api/compliance/qms/{provider}/findings — push finding to external QMS

All QMS endpoints preserve tenant isolation, honest unavailable when creds missing,
no fabricated provider rows.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.compliance import repository
from app.compliance.schemas import (
    CheckInput,
    FrameworkInput,
    RemediationInput,
    RemediationTransitionInput,
)
from app.compliance.service import ComplianceService
from app.db.session import get_session
from app.governance.context import resolve_scope
from app.tenancy.isolation import HierarchyError, to_http

router = APIRouter(prefix="/api/compliance", tags=["compliance"])


def _finding(row):
    return {
        "id": str(row.id),
        "framework_id": str(row.framework_id),
        "control_id": str(row.control_id),
        "subject_type": row.subject_type,
        "subject_id": row.subject_id,
        "status": row.status,
        "severity": row.severity,
        "result": row.result,
        "rationale": row.rationale,
        "source_reference": row.source_reference,
        "evidence_fingerprint": row.evidence_fingerprint,
        "review_required": row.review_required,
        "created_at": row.created_at.isoformat(),
    }


def _framework(row):
    return {
        "id": str(row.id),
        "framework_type": row.framework_type,
        "name": row.name,
        "version": row.version,
        "status": row.status,
        "configuration": row.configuration,
        "environment_id": str(row.environment_id),
    }


@router.post("/frameworks", status_code=201)
async def create_framework(
    body: FrameworkInput,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, body.environment_id)
        service = ComplianceService(session, scope, ctx.user_id)
        row = await service.create_framework(
            framework_type=body.framework_type,
            name=body.name,
            version=body.version,
            configuration=body.configuration,
            controls=[control.model_dump() for control in body.controls],
        )
        await session.commit()
        return _framework(row)
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/frameworks")
async def list_frameworks(
    environment_id: uuid.UUID,
    framework_type: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, environment_id)
        rows = await repository.list_frameworks(
            session, scope, framework_type=framework_type, limit=limit
        )
        return [_framework(row) for row in rows]
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/checks", status_code=201)
async def run_check(
    body: CheckInput,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, body.environment_id)
        results = await ComplianceService(session, scope, ctx.user_id).evaluate(
            framework_id=body.framework_id,
            subject_type=body.subject_type,
            subject_id=body.subject_id,
            observed=body.observed,
            evidence_references=body.evidence_references,
        )
        await session.commit()
        return {"findings": results, "certification": "not_assessed"}
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/findings")
async def list_findings(
    environment_id: uuid.UUID,
    status: str | None = None,
    framework_id: uuid.UUID | None = None,
    limit: int = Query(100, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, environment_id)
        rows = await repository.list_findings(
            session, scope, status=status, framework_id=framework_id, limit=limit
        )
        return [_finding(row) for row in rows]
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/findings/{finding_id}")
async def get_finding(
    finding_id: uuid.UUID,
    environment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, environment_id)
        return _finding(await repository.get_finding(session, scope, finding_id))
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/findings/{finding_id}/remediations", status_code=201)
async def create_remediation(
    finding_id: uuid.UUID,
    environment_id: uuid.UUID,
    body: RemediationInput,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, environment_id)
        row = await ComplianceService(session, scope, ctx.user_id).create_remediation(
            finding_id, owner_id=body.owner_id, due_at=body.due_at
        )
        await session.commit()
        return {
            "id": str(row.id),
            "finding_id": str(row.finding_id),
            "status": row.status,
            "owner_id": str(row.owner_id) if row.owner_id else None,
        }
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/remediations")
async def list_remediations(
    environment_id: uuid.UUID,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, environment_id)
        rows = await repository.list_remediations(
            session, scope, status=status, limit=limit
        )
        return [
            {
                "id": str(row.id),
                "finding_id": str(row.finding_id),
                "status": row.status,
                "owner_id": str(row.owner_id) if row.owner_id else None,
                "due_at": row.due_at.isoformat() if row.due_at else None,
                "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
                "resolution_reference": row.resolution_reference,
            }
            for row in rows
        ]
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.patch("/remediations/{remediation_id}")
async def transition_remediation(
    remediation_id: uuid.UUID,
    body: RemediationTransitionInput,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, body.environment_id)
        row = await ComplianceService(session, scope, ctx.user_id).transition_remediation(
            remediation_id,
            status=body.status,
            owner_id=body.owner_id,
            due_at=body.due_at,
            resolution_reference=body.resolution_reference,
        )
        await session.commit()
        return {
            "id": str(row.id),
            "status": row.status,
            "resolution_reference": row.resolution_reference,
            "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        }
    except HierarchyError as exc:
        raise to_http(exc) from None


# ---- QMS Native Adapters (GAP-P1-03) ----

@router.get("/qms/providers")
async def list_qms_providers(
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
):
    from app.compliance.qms_adapters import list_qms_providers as _list

    return {"providers": _list(), "note": "Veeva Vault, MasterControl, ETQ — honest unavailable when credentials missing"}


@router.post("/qms/health")
async def qms_health(
    payload: dict,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        environment_id = payload.get("environment_id")
        if not environment_id:
            from app.tenancy.isolation import HierarchyError as HE

            raise HE("environment_id required", code="validation_failed", status_code=422)
        env_uuid = uuid.UUID(str(environment_id))
        scope = await resolve_scope(session, ctx, env_uuid)

        from app.compliance.qms_adapters import QMSContext, get_qms_adapter

        provider = str(payload.get("provider") or "").lower()
        if not provider:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="provider required")
        config = payload.get("config") or {}
        credentials = payload.get("credentials") or {}
        qms_ctx = QMSContext(
            tenant_id=scope.tenant_id,
            organization_id=scope.organization_id,
            environment_id=scope.environment_id,
            provider=provider,
            config=config,
            credentials=credentials,
        )
        adapter = get_qms_adapter(qms_ctx)
        result = await adapter.health_check()
        return {
            "provider": result.provider,
            "connected": result.connected,
            "latency_ms": result.latency_ms,
            "message": result.safe_message,
            "details": result.details,
        }
    except HierarchyError as exc:
        raise to_http(exc) from None
    except ValueError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/qms/{provider}/documents")
async def qms_list_documents(
    provider: str,
    environment_id: uuid.UUID,
    q: str | None = Query(None),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, environment_id)
        from app.compliance.qms_adapters import QMSContext, get_qms_adapter

        qms_ctx = QMSContext(
            tenant_id=scope.tenant_id,
            organization_id=scope.organization_id,
            environment_id=scope.environment_id,
            provider=provider,
            config={},
            credentials={},
        )
        adapter = get_qms_adapter(qms_ctx)
        health = await adapter.health_check()
        if not health.connected:
            return {
                "provider": provider,
                "connected": False,
                "message": health.safe_message,
                "documents": [],
                "external_blocker": True,
            }
        docs = await adapter.list_documents(query=q)
        return {
            "provider": provider,
            "connected": True,
            "documents": [d.__dict__ for d in docs],
        }
    except HierarchyError as exc:
        raise to_http(exc) from None
    except RuntimeError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=424, detail=str(exc)) from None


@router.get("/qms/{provider}/documents/{external_id}")
async def qms_get_document(
    provider: str,
    external_id: str,
    environment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, environment_id)
        from app.compliance.qms_adapters import QMSContext, get_qms_adapter

        qms_ctx = QMSContext(
            tenant_id=scope.tenant_id,
            organization_id=scope.organization_id,
            environment_id=scope.environment_id,
            provider=provider,
            config={},
            credentials={},
        )
        adapter = get_qms_adapter(qms_ctx)
        doc = await adapter.get_document(external_id)
        return doc.__dict__
    except HierarchyError as exc:
        raise to_http(exc) from None
    except RuntimeError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=424, detail=str(exc)) from None


@router.get("/qms/{provider}/traceability")
async def qms_traceability(
    provider: str,
    environment_id: uuid.UUID,
    subject_type: str = Query(...),
    subject_id: str = Query(...),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, environment_id)
        from app.compliance.qms_adapters import QMSContext, get_qms_adapter

        qms_ctx = QMSContext(
            tenant_id=scope.tenant_id,
            organization_id=scope.organization_id,
            environment_id=scope.environment_id,
            provider=provider,
            config={},
            credentials={},
        )
        adapter = get_qms_adapter(qms_ctx)
        result = await adapter.get_traceability(subject_type=subject_type, subject_id=subject_id)
        return result
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/qms/{provider}/audit-package")
async def qms_audit_package(
    provider: str,
    payload: dict,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        environment_id = payload.get("environment_id")
        framework_id = payload.get("framework_id")
        if not environment_id or not framework_id:
            from app.tenancy.isolation import HierarchyError as HE

            raise HE("environment_id and framework_id required", code="validation_failed", status_code=422)
        env_uuid = uuid.UUID(str(environment_id))
        scope = await resolve_scope(session, ctx, env_uuid)
        from app.compliance.qms_adapters import QMSContext, get_qms_adapter

        qms_ctx = QMSContext(
            tenant_id=scope.tenant_id,
            organization_id=scope.organization_id,
            environment_id=scope.environment_id,
            provider=provider,
            config=payload.get("config") or {},
            credentials=payload.get("credentials") or {},
        )
        adapter = get_qms_adapter(qms_ctx)
        result = await adapter.assemble_audit_package(str(framework_id))
        return result
    except HierarchyError as exc:
        raise to_http(exc) from None


from app.api.industry_template_routes import router as industry_template_router

router.include_router(industry_template_router)
