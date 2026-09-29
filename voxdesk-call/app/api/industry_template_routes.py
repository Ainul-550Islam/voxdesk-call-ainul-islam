"""Tenant-scoped instantiation of vendor-neutral industry starter templates."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.compliance import repository
from app.compliance.industry_templates import (
    framework_controls,
    list_industry_templates,
    resolve_industry_template,
)
from app.compliance.service import ComplianceService
from app.db.session import get_session
from app.governance.audit import record_governance_audit
from app.governance.context import resolve_scope
from app.governance.evidence import append_event
from app.governance.policy import require_policy
from app.review import repository as review_repository
from app.review.service import create_case
from app.tenancy.isolation import HierarchyError, LifecycleDenied, to_http

router = APIRouter(prefix="/industry-templates", tags=["compliance-templates"])


class InstantiateTemplateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    source_references: dict[str, str] = Field(min_length=1, max_length=20)


class ActivateFrameworkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment_id: uuid.UUID


@router.get("")
async def list_templates(
    _ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
) -> list[dict]:
    return list_industry_templates()


@router.post("/{template_key}/frameworks", status_code=201)
async def instantiate_template(
    template_key: str,
    body: InstantiateTemplateInput,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        template = resolve_industry_template(template_key)
    except KeyError:
        raise HTTPException(status_code=404, detail="Industry template not found") from None
    try:
        controls = framework_controls(template, body.source_references)
        scope = await resolve_scope(session, ctx, body.environment_id)
        framework = await ComplianceService(session, scope, ctx.user_id).create_framework(
            framework_type=template.framework_type,
            name=body.name,
            version=body.version,
            configuration={
                "starter_template_key": template.key,
                "starter_template_version": template.version,
                "limitations": list(template.limitations),
                "tenant_review_required": True,
            },
            controls=controls,
        )
        framework.status = "draft"
        review_case = await create_case(
            session,
            scope,
            actor_user_id=ctx.user_id,
            case_type="compliance",
            agent_type="compliance",
            subject_type="compliance_framework",
            subject_id=str(framework.id),
            reason="Tenant review is required before enabling this industry starter framework",
            requested_controls=[control["control_key"] for control in controls],
            metadata={
                "framework_id": str(framework.id),
                "template_key": template.key,
                "template_version": template.version,
            },
        )
        framework.configuration = {
            **(framework.configuration or {}),
            "review_case_id": str(review_case.id),
            "review_state": "pending",
        }
        await session.commit()
        return {
            "id": str(framework.id),
            "environment_id": str(framework.environment_id),
            "framework_type": framework.framework_type,
            "name": framework.name,
            "version": framework.version,
            "status": framework.status,
            "starter_template_key": template.key,
            "review_case_id": str(review_case.id),
            "review_state": "pending",
            "limitations": list(template.limitations),
            "control_keys": [control["control_key"] for control in controls],
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/frameworks/{framework_id}/activate")
async def activate_reviewed_framework(
    framework_id: uuid.UUID,
    body: ActivateFrameworkInput,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        scope = await resolve_scope(session, ctx, body.environment_id)
        framework = await repository.get_framework(session, scope, framework_id, lock=True)
        configuration = dict(framework.configuration or {})
        known_template_keys = {template["key"] for template in list_industry_templates()}
        if configuration.get("starter_template_key") not in known_template_keys:
            raise LifecycleDenied("framework is not a review-gated industry starter")
        if framework.status != "draft":
            raise LifecycleDenied("only draft starter frameworks can be activated")
        try:
            review_case_id = uuid.UUID(str(configuration.get("review_case_id")))
        except (ValueError, TypeError, AttributeError) as exc:
            raise LifecycleDenied("framework review case is missing") from exc
        review_case = await review_repository.get_case(
            session,
            tenant_id=scope.tenant_id,
            organization_id=scope.organization_id,
            environment_id=scope.environment_id,
            case_id=review_case_id,
            lock=True,
        )
        if (
            review_case is None
            or review_case.subject_type != "compliance_framework"
            or review_case.subject_id != str(framework.id)
            or review_case.status != "approved"
        ):
            raise LifecycleDenied("framework requires an approved, scope-matched review case")
        decisions = await review_repository.decisions(session, review_case)
        if not decisions or decisions[-1].decision != "approve":
            raise LifecycleDenied("latest review decision does not approve this framework")
        policy_decision = await require_policy(
            session,
            scope,
            policy_type="compliance_framework",
            context={
                "operation": "activate",
                "framework_id": str(framework.id),
                "framework_type": framework.framework_type,
                "review_case_id": str(review_case.id),
                "required_human_approval": True,
            },
            principal_id=ctx.user_id,
            correlation_id=str(framework.id),
        )
        framework.status = "active"
        framework.configuration = {
            **configuration,
            "review_state": "approved",
            "activation_policy_decision_id": str(policy_decision.id),
        }
        await session.flush()
        await record_governance_audit(
            session,
            scope,
            event="industry_starter_framework_activated",
            actor_user_id=ctx.user_id,
            detail={
                "framework_id": str(framework.id),
                "review_case_id": str(review_case.id),
                "policy_decision_id": str(policy_decision.id),
            },
        )
        await append_event(
            session,
            scope,
            event_type="industry_starter_framework_activated",
            payload={
                "framework_id": str(framework.id),
                "review_case_id": str(review_case.id),
                "review_decision_id": str(decisions[-1].id),
                "policy_decision_id": str(policy_decision.id),
            },
            actor_user_id=ctx.user_id,
            subject_type="compliance_framework",
            subject_id=str(framework.id),
        )
        await session.commit()
        return {
            "id": str(framework.id),
            "status": framework.status,
            "review_case_id": str(review_case.id),
            "review_state": "approved",
        }
    except HierarchyError as exc:
        raise to_http(exc) from None
