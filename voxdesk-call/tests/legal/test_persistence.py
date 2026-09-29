"""Legal finding/citation persistence and human outcome semantics."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import Environment, Organization, UserRole
from app.governance.context import GovernanceScope
from app.governance.models import GovernancePolicy, ModelRegistry, ModelVersion, RiskAssessment
from app.legal.persistence import LegalCitationRecord, LegalFindingRecord, LegalOutcomeRecord, LegalReviewRecord, persist_review
from app.legal.review_engine import ReviewEngine
from app.legal.review_service import persist_and_open_review
from app.legal.schemas import DocumentChunk
from app.review.service import assign_case, decide_case, start_case
from app.specialized_agents.service import SpecializedAgentService
from tests.conftest import make_tenant, make_user


async def _seed(db, name):
    tenant = await make_tenant(db, name)
    actor = await make_user(db, tenant, UserRole.OWNER)
    reviewer = await make_user(db, tenant, UserRole.OWNER)
    organization = await db.get(Organization, tenant.organization_id)
    environment = await db.scalar(select(Environment).where(Environment.tenant_id == tenant.id, Environment.kind == "production"))
    scope = GovernanceScope(tenant, organization, environment)
    registry = ModelRegistry(tenant_id=tenant.id, organization_id=organization.id, provider=f"test-{uuid.uuid4().hex}", model_name="deterministic-legal-test", display_name="Legal Test", status="active", risk_tier="high", intended_use="local legal behavior test", data_classes=[], capabilities=["legal"], approved_for_channels=["legal"], approved_for_environments=[str(environment.id)], metadata_json={})
    db.add(registry)
    await db.flush()
    version = ModelVersion(registry_id=registry.id, tenant_id=tenant.id, organization_id=organization.id, provider=registry.provider, model_name=registry.model_name, version="1", fingerprint="a"*64, artifact_uri="registry://local/legal", artifact_digest="b"*64, capabilities=["legal"], input_modalities=["text"], output_modalities=["structured"], status="approved", evaluation_status="passed", evaluation_summary={"authoritative_verifier": "local-test", "passed": True})
    db.add_all([version, GovernancePolicy(tenant_id=tenant.id, organization_id=organization.id, environment_id=environment.id, name=f"legal-exec-{uuid.uuid4()}", policy_type="specialized_agent", status="published", version=1, rules={"decision": "allow"}, rationale="local test", created_by=actor.id), RiskAssessment(tenant_id=tenant.id, organization_id=organization.id, environment_id=environment.id, subject_type="specialized_agent", subject_id="legal", version=1, tier="high", status="approved", factors={}, required_controls=["human_review"], rationale="test risk record", assessed_by=actor.id, approved_by=actor.id), GovernancePolicy(tenant_id=tenant.id, organization_id=organization.id, environment_id=environment.id, name=f"review-decision-{uuid.uuid4()}", policy_type="review_decision", status="published", version=1, rules={"decision": "allow", "required_human_approval": True}, rationale="review test", created_by=actor.id)])
    await db.flush()
    return tenant, actor, reviewer, scope, version


@pytest.mark.asyncio
async def test_legal_findings_real_citations_review_bridge_and_human_outcomes(db):
    tenant, actor, reviewer, scope, version = await _seed(db, "legal-persist")
    engine = ReviewEngine()
    chunk = DocumentChunk(document_id="contract-1", chunk_id="chunk-1", source_title="Supplied contract", text="The supplier shall indemnify the customer for covered losses.", page_number=2, character_start=100, character_end=161)
    sources = engine.source_references([chunk])
    service = SpecializedAgentService(db, scope)
    context = service.context(actor_id=actor.id, request_id=uuid.uuid4().hex, trace_id=uuid.uuid4().hex, agent_type="legal", agent_version="1.0.0", model_version_id=version.id, risk_tier="high")
    outcome = await service.execute(context, idempotency_key="legal-persist-001", payload={"document_id": "contract-1", "chunk_fingerprint": sources[0].content_fingerprint}, source_references=sources, handler=lambda *_: engine.review(document_id="contract-1", chunks=[chunk]))
    case = await persist_and_open_review(db, scope, execution=outcome.record, output=outcome.output, actor_user_id=actor.id)
    await db.flush()
    review = await db.get(LegalReviewRecord, outcome.record.id)
    finding = await db.scalar(select(LegalFindingRecord).where(LegalFindingRecord.execution_id == outcome.record.id))
    citation = await db.scalar(select(LegalCitationRecord).where(LegalCitationRecord.execution_id == outcome.record.id))
    assert review is not None and review.review_required and review.status == "review_required"
    assert finding is not None and finding.citation_id == citation.id
    assert citation.document_id == "contract-1" and citation.chunk_id == "chunk-1"
    assert citation.content_fingerprint == sources[0].content_fingerprint
    assert case is not None and case.execution_id == outcome.record.id

    await assign_case(db, scope, case_id=case.id, reviewer_id=reviewer.id, assigned_by=actor.id)
    await start_case(db, scope, case_id=case.id, reviewer_id=reviewer.id)
    await decide_case(db, scope, case_id=case.id, reviewer_id=reviewer.id, decision="approve", rationale="Human reviewer considered the findings; legal validity is not certified.")
    assert review.status == "human_reviewed"
    assert review.review_state == "approved"
    assert review.completed_at is not None
    legal_outcome = await db.scalar(select(LegalOutcomeRecord).where(LegalOutcomeRecord.execution_id == outcome.record.id))
    assert legal_outcome is not None and legal_outcome.outcome == "human_reviewed"
    assert legal_outcome.metadata_json["legal_validity"] == "not_assessed"


@pytest.mark.asyncio
async def test_no_citation_row_is_fabricated_without_actual_citation(db):
    _tenant, actor, _reviewer, scope, version = await _seed(db, "legal-no-citation")
    service = SpecializedAgentService(db, scope)
    context = service.context(actor_id=actor.id, request_id=uuid.uuid4().hex, trace_id=uuid.uuid4().hex, agent_type="legal", agent_version="1.0.0", model_version_id=version.id, risk_tier="high")
    output = {"document_id": "document-no-citation", "disclaimer": ReviewEngine.DISCLAIMER, "review_required": True, "review_state": "required", "source_count": 0, "legal_certainty": "not_claimed", "citations": [], "findings": [{"clause": "indemnification", "category": "indemnification", "issue_type": "detected_language", "severity": "high", "confidence": 0.8, "extracted_text_reference": "a"*64, "location": {"document_id": "document-no-citation", "chunk_id": "unavailable", "page_number": None, "character_start": 0, "character_end": 1}, "rationale": "Test finding", "recommended_follow_up": "Human review", "source_reference": {"document_id": "document-no-citation", "chunk_id": "unavailable", "content_fingerprint": "b"*64}}]}
    outcome = await service.execute(context, idempotency_key="legal-no-citation-001", payload={"document_id": "document-no-citation"}, handler=lambda *_: output)
    record = await persist_review(db, scope, execution_id=outcome.record.id, output=output)
    finding = await db.scalar(select(LegalFindingRecord).where(LegalFindingRecord.execution_id == outcome.record.id))
    citations = list((await db.scalars(select(LegalCitationRecord).where(LegalCitationRecord.execution_id == outcome.record.id))).all())
    assert record.finding_count == 1
    assert citations == []
    assert finding is not None and finding.citation_id is None
