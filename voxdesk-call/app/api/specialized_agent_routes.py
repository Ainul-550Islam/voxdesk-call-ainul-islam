"""Shared capability discovery and governed specialized-agent execution API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.errors import request_id_from
from app.db.session import get_session
from app.governance.context import resolve_scope
from app.specialized_agents.context import SpecializedAgentContext
from app.specialized_agents.exceptions import SpecializedAgentError, UnsupportedFeatureError
from app.specialized_agents.executor import SpecializedExecutionRecord
from app.specialized_agents.registry import list_agents, resolve_agent
from app.specialized_agents.schemas import (
    AgentCapabilityResponse,
    SpecializedExecutionRequest,
    SpecializedExecutionResponse,
    SourceReferenceIn,
)
from app.specialized_agents.service import SpecializedAgentService
from app.specialized_agents.sources import SourceReference
from app.tenancy.isolation import HierarchyError, NotFound, to_http

router = APIRouter(prefix="/api/specialized-agents", tags=["specialized-agents"])


def _source_objects(references: list[SourceReferenceIn]) -> list[SourceReference]:
    return [
        SourceReference(
            document_id=reference.document_id,
            chunk_id=reference.chunk_id,
            source_title=reference.source_title,
            content_fingerprint=reference.content_fingerprint.lower(),
            page_number=reference.page_number,
            character_start=reference.character_start,
            character_end=reference.character_end,
            retrieval_timestamp=reference.retrieval_timestamp,
        )
        for reference in references
    ]


def _context(
    service: SpecializedAgentService,
    *,
    agent_type: str,
    request: SpecializedExecutionRequest,
    ctx: TenantContext,
    request_id: str,
    trace_id: str,
) -> SpecializedAgentContext:
    return service.context(
        actor_id=ctx.user_id,
        request_id=request_id,
        trace_id=trace_id,
        agent_type=agent_type,
        agent_version=request.agent_version,
        model_version_id=request.model_version_id,
        risk_tier=request.risk_tier,
        locale=request.locale,
        language=request.language,
        source_references=tuple(reference.content_fingerprint.lower() for reference in request.source_references),
    )


def response_for(row: SpecializedExecutionRecord, result: dict) -> SpecializedExecutionResponse:
    return SpecializedExecutionResponse.model_validate({
        "id": row.id,
        "tenant_id": row.tenant_id,
        "organization_id": row.organization_id,
        "environment_id": row.environment_id,
        "request_id": row.request_id,
        "trace_id": row.trace_id,
        "agent_type": row.agent_type,
        "agent_version": row.agent_version,
        "model_version_id": row.model_version_id,
        "risk_tier": row.risk_tier,
        "status": row.status,
        "input_fingerprint": row.input_fingerprint,
        "output_fingerprint": row.output_fingerprint,
        "policy_decision_id": row.policy_decision_id,
        "lineage_root_id": row.lineage_root_id,
        "evidence_root_hash": row.evidence_root_hash,
        "review_required": row.review_required,
        "review_state": row.review_state,
        "result": result,
        "failure_code": row.failure_code,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    })


async def _scope(session: AsyncSession, ctx: TenantContext, environment_id: uuid.UUID):
    scope = await resolve_scope(session, ctx, environment_id)
    if scope.environment_id is None:
        raise HierarchyError("Environment is required", code="environment_required", status_code=422)
    return scope


@router.get("", response_model=list[AgentCapabilityResponse])
async def capabilities(
    _ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
):
    return [AgentCapabilityResponse.model_validate(definition.as_dict()) for definition in list_agents()]


@router.get("/{agent_type}", response_model=AgentCapabilityResponse)
async def capability(
    agent_type: str,
    _ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
):
    try:
        return AgentCapabilityResponse.model_validate(resolve_agent(agent_type).as_dict())
    except SpecializedAgentError as exc:
        raise to_http(exc) from None


@router.post("/{agent_type}/execute", response_model=SpecializedExecutionResponse, status_code=201)
async def execute_specialized_agent(
    agent_type: str,
    payload: SpecializedExecutionRequest,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        resolve_agent(agent_type)
        if agent_type in {"intake_flow","virtual_paralegal","billing_guard","billing_ops","ocg_compliance","qms_compliance","healthcare","manufacturing","retail"} and not ctx.can(Permission.COMPLIANCE_WRITE):
            raise HTTPException(status_code=403, detail="Compliance write permission is required for this legal-agent execution")
        scope = await _scope(session, ctx, payload.environment_id)
        service = SpecializedAgentService(session, scope)
        context = _context(
            service,
            agent_type=agent_type,
            request=payload,
            ctx=ctx,
            request_id=request_id_from(request),
            trace_id=request.headers.get("X-Trace-ID") or uuid.uuid4().hex,
        )
        from app.anomaly.service import AnomalyService
        from app.anomaly.schemas import DetectorConfig, Observation
        from app.legal.review_engine import ReviewEngine
        from app.legal.schemas import DocumentChunk
        from app.translation.engine import TranslationEngine
        from app.translation.glossary import Glossary
        from app.translation.schemas import GlossaryEntryInput, TranslationSegment
        source_resolver = None
        policy_context = None

        if agent_type == "anomaly":
            metric = str(payload.payload.get("metric") or "")
            observations = [Observation.model_validate(item) for item in payload.payload.get("observations", [])]
            configuration = DetectorConfig.model_validate(payload.payload.get("configuration", {}))
            analyzer = AnomalyService()

            def handler(_context, _payload, _sources):
                return analyzer.analyze(
                    metric=metric, observations=observations, configuration=configuration
                )

            sources = analyzer.source_references(metric, observations)
        elif agent_type == "legal":
            document_id = str(payload.payload.get("document_id") or "")
            chunks = [DocumentChunk.model_validate(item) for item in payload.payload.get("chunks", [])]
            engine = ReviewEngine()

            def handler(_context, _payload, _sources):
                return engine.review(document_id=document_id, chunks=chunks)

            sources = engine.source_references(chunks)
        elif agent_type == "translation":
            source_language = str(payload.payload.get("source_language") or "")
            target_language = str(payload.payload.get("target_language") or "")
            segments = [TranslationSegment.model_validate(item) for item in payload.payload.get("segments", [])]
            glossary_version = str(payload.payload.get("glossary_version") or "")
            glossary_entries = [GlossaryEntryInput.model_validate(item) for item in payload.payload.get("glossary", [])]
            engine = TranslationEngine(session=session)
            glossary = Glossary.from_inputs(glossary_version, glossary_entries)

            async def handler(_context, _payload, _sources):
                return await engine.translate(
                    source_language=source_language,
                    target_language=target_language,
                    segments=segments,
                    glossary=glossary,
                    context=_context,
                )

            sources = _source_objects(payload.source_references)
        elif agent_type == "intake_flow":
            from app.legal_agents.intake_flow import triage_intake
            request_data = dict(payload.payload)
            def handler(_context, _payload, _sources):
                return triage_intake(request_data)
            sources = _source_objects(payload.source_references)
        elif agent_type == "virtual_paralegal":
            from app.knowledge.retrieval import retrieve
            from app.legal_agents.virtual_paralegal import prepare_assistance
            task = str(payload.payload.get("task") or "")
            query = str(payload.payload.get("question") or "")
            if task not in {"summarize_matter","extract_facts","retrieve_information","review_questions","follow_up_draft"}:
                raise UnsupportedFeatureError("virtual paralegal task is not supported")
            if not query.strip():
                raise UnsupportedFeatureError("virtual paralegal requires a retrieval question")
            retrieved = []
            async def source_resolver(_context, _payload):
                from app.specialized_agents.sources import SourceReference
                chunks = await retrieve(session, tenant_id=scope.tenant_id, environment_id=scope.environment_id, query=query, document_ids=payload.payload.get("document_ids"))
                retrieved.extend(chunks)
                return [SourceReference.from_text(document_id=chunk.document_id, chunk_id=chunk.chunk_id, source_title=chunk.title, text=chunk.text) for chunk in chunks]
            def handler(_context, _payload, safe_sources):
                refs = [ref.key() for ref in safe_sources.references]
                result = prepare_assistance(task=task, validated_documents=[], legal_findings=[], knowledge_sources=[{"source_reference": ref} for ref in refs], missing_information=[] if refs else ["No tenant-scoped knowledge source matched the question"])
                result["citations"] = refs
                result["review_required"] = True
                result["review_reason"] = "Source-grounded legal assistance requires qualified human review"
                result["requested_controls"] = ["qualified_human_review", "source_citation_validation"]
                return result
            sources = []
        elif agent_type in {"billing_guard", "billing_ops"}:
            from app.governance.models import GovernancePolicy
            from app.legal_agents.billing_guard import validate_billing_line
            from app.legal_agents.billing_ops import prepare_billing_package
            policy = await session.scalar(select(GovernancePolicy).where(GovernancePolicy.tenant_id==scope.tenant_id, GovernancePolicy.organization_id==scope.organization_id, GovernancePolicy.environment_id==scope.environment_id, GovernancePolicy.policy_type=="billing_guard", GovernancePolicy.status=="published").order_by(GovernancePolicy.version.desc()).limit(1))
            if policy is None:
                raise UnsupportedFeatureError("a published, scope-bound billing_guard policy is required")
            configured_rules = (policy.rules or {}).get("billing_rules")
            if not isinstance(configured_rules, dict) or not configured_rules:
                raise UnsupportedFeatureError("published billing_guard policy has no configured billing_rules")
            lines = payload.payload.get("billing_lines")
            if not isinstance(lines, list) or not lines or len(lines)>500 or any(not isinstance(line,dict) for line in lines):
                raise UnsupportedFeatureError("billing_lines must be a non-empty list of structured line items")
            source_keys=[ref.key() for ref in _source_objects(payload.source_references)]
            observed=set(); checks=[]
            for line in lines:
                check=validate_billing_line(line,configured_rules,known_fingerprints=observed,source_references=source_keys)
                observed.add(check["line_fingerprint"]);checks.append(check)
            async def billing_handler(_context,_payload,_sources):
                from app.governance.policy import require_policy
                await require_policy(session,scope,policy_type="billing_guard",context={"agent_type":agent_type,"billing_policy_id":str(policy.id),"line_count":len(checks)},principal_id=ctx.user_id,correlation_id=_context.trace_id)
                if agent_type=="billing_guard":
                    return {"findings":checks,"review_required":any(c["status"]!="pass" for c in checks),"review_reason":"Configured billing checks flagged one or more lines","requested_controls":["billing_rule_review","supporting_source_review"]}
                prepared=prepare_billing_package(lines,checks,connector_name=payload.payload.get("connector_name"),action_name=payload.payload.get("action_name"),approval_reference=None)
                prepared["review_required"]=prepared["action_readiness"]=="ready_for_governed_approval"
                prepared["review_reason"]="Prepared billing action requires human approval and existing connector authorization"
                prepared["requested_controls"]=["human_approval","connector_authorization","billing_rule_review"]
                return prepared
            handler=billing_handler
            policy_context={"billing_policy_id":str(policy.id),"billing_policy_version":policy.version}
            sources=_source_objects(payload.source_references)
        elif agent_type in {"ocg_compliance","qms_compliance","healthcare","manufacturing","retail"}:
            from app.compliance.enums import FrameworkType
            from app.compliance.repository import get_framework
            from app.compliance.service import ComplianceService
            framework_raw=payload.payload.get("framework_id")
            try: framework_id=uuid.UUID(str(framework_raw))
            except (ValueError,TypeError,AttributeError) as exc: raise UnsupportedFeatureError("framework_id must be a persisted UUID") from exc
            framework=await get_framework(session,scope,framework_id)
            expected_framework={"ocg_compliance":FrameworkType.OCG.value,"qms_compliance":FrameworkType.QMS.value,"healthcare":FrameworkType.HEALTHCARE.value,"manufacturing":FrameworkType.MANUFACTURING.value,"retail":FrameworkType.RETAIL.value}[agent_type]
            if framework.framework_type!=expected_framework or framework.status!="active":
                raise UnsupportedFeatureError(f"an active {expected_framework} framework is required")
            subject_type=str(payload.payload.get("subject_type") or "")
            subject_id=str(payload.payload.get("subject_id") or "")
            observed_data=payload.payload.get("observed")
            if not subject_type or not subject_id or not isinstance(observed_data,dict):
                raise UnsupportedFeatureError("compliance-agent execution requires scoped subject identifiers and observed input")
            source_keys=[ref.key() for ref in _source_objects(payload.source_references)]
            async def handler(_context,_payload,_sources):
                results=await ComplianceService(session,scope,ctx.user_id).evaluate(framework_id=framework_id,subject_type=subject_type,subject_id=subject_id,observed=observed_data,evidence_references=source_keys)
                required=any(bool(item.get("review_required")) for item in results)
                return {"findings":results,"review_required":required,"review_cases_persisted":required,"review_reason":"Persisted OCG findings require human review","requested_controls":["qualified_compliance_review"]}
            policy_context={"compliance_framework_id":str(framework.id),"compliance_framework_type":expected_framework,"industry_agent_type":agent_type}
            sources=_source_objects(payload.source_references)
        elif agent_type == "metrics_insights":
            from app.legal_agents.metrics_insights import operational_metrics
            async def handler(_context,_payload,_sources):
                return await operational_metrics(session,scope)
            sources=_source_objects(payload.source_references)
        else:
            raise UnsupportedFeatureError("generic execution for this agent requires its typed service endpoint")

        outcome = await service.execute(
            context,
            idempotency_key=payload.idempotency_key,
            payload=payload.payload,
            source_references=sources,
            handler=handler,
            source_resolver=source_resolver,
            policy_context=policy_context,
        )
        return response_for(outcome.record, outcome.output)
    except SpecializedAgentError as exc:
        raise to_http(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/{agent_type}/executions/{execution_id}", response_model=SpecializedExecutionResponse)
async def get_execution(
    agent_type: str,
    execution_id: uuid.UUID,
    environment_id: uuid.UUID = Query(...),
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        resolve_agent(agent_type)
        scope = await _scope(session, ctx, environment_id)
        row = await session.scalar(
            select(SpecializedExecutionRecord).where(
                SpecializedExecutionRecord.id == execution_id,
                SpecializedExecutionRecord.tenant_id == scope.tenant_id,
                SpecializedExecutionRecord.organization_id == scope.organization_id,
                SpecializedExecutionRecord.environment_id == scope.environment_id,
                SpecializedExecutionRecord.agent_type == agent_type,
            )
        )
        if row is None:
            raise NotFound()
        return response_for(row, row.result or {})
    except HierarchyError as exc:
        raise to_http(exc) from None
