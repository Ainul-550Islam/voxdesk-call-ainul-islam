"""Insight API auth, source validation, governance and retrieval preservation."""
from __future__ import annotations

import hashlib
import uuid

import pytest
from sqlalchemy import select

from app.db.models import DocumentSourceType, DocumentStatus, Environment, KnowledgeChunk, KnowledgeDocument, Organization, UserRole
from app.governance.models import GovernancePolicy, ModelRegistry, ModelVersion, RiskAssessment
from app.specialized_agents.executor import SpecializedExecutionRecord
from tests.conftest import auth_headers, make_tenant, make_user


async def _setup(db, tenant_name="insight-api"):
    tenant = await make_tenant(db, tenant_name)
    owner = await make_user(db, tenant, UserRole.OWNER)
    viewer = await make_user(db, tenant, UserRole.VIEWER)
    organization = await db.get(Organization, tenant.organization_id)
    environment = await db.scalar(select(Environment).where(Environment.tenant_id == tenant.id, Environment.kind == "production"))
    registry = ModelRegistry(tenant_id=tenant.id, organization_id=organization.id, provider=f"provider-{uuid.uuid4().hex}", model_name="insight-api-test", display_name="Insight API Test", status="active", risk_tier="high", intended_use="cited insight test", data_classes=["structured"], capabilities=["insight"], approved_for_channels=["insight"], approved_for_environments=[str(environment.id)], metadata_json={})
    db.add(registry)
    await db.flush()
    model = ModelVersion(registry_id=registry.id, tenant_id=tenant.id, organization_id=organization.id, provider=registry.provider, model_name=registry.model_name, version="1", fingerprint="e"*64, artifact_uri="registry://local/insight", artifact_digest="f"*64, capabilities=["insight"], input_modalities=["structured"], output_modalities=["structured"], status="approved", evaluation_status="passed", evaluation_summary={"authoritative_verifier": "test-fixture", "passed": True})
    db.add_all([model, GovernancePolicy(tenant_id=tenant.id, organization_id=organization.id, environment_id=environment.id, name=f"insight-policy-{uuid.uuid4()}", policy_type="specialized_agent", status="published", version=1, rules={"decision": "allow"}, rationale="API test", created_by=owner.id), RiskAssessment(tenant_id=tenant.id, organization_id=organization.id, environment_id=environment.id, subject_type="specialized_agent", subject_id="insight", version=1, tier="high", status="approved", factors={}, required_controls=[], rationale="test risk", assessed_by=owner.id, approved_by=owner.id)])
    await db.flush()
    text = "Quarterly revenue was 120 units in the supplied report."
    fingerprint = hashlib.sha256(text.encode()).hexdigest()
    document = KnowledgeDocument(tenant_id=tenant.id, environment_id=environment.id, title="Quarterly Report", source_type=DocumentSourceType.TEXT, content_hash=hashlib.sha256(b"report bytes").hexdigest(), status=DocumentStatus.READY, version=1, embedding_model="test", chunk_count=1, char_count=len(text), doc_metadata={})
    db.add(document)
    await db.flush()
    chunk = KnowledgeChunk(document_id=document.id, tenant_id=tenant.id, environment_id=environment.id, chunk_index=0, version=1, text=text, content_hash=fingerprint, chunk_metadata={"page": 1}, embedding=[1.0], embedding_model="test")
    db.add(chunk)
    await db.commit()
    return tenant, owner, viewer, environment, model, document, chunk, fingerprint


@pytest.mark.asyncio
async def test_insight_route_auth_valid_sources_governance_and_tenant_scope(client, db, monkeypatch):
    tenant, owner, viewer, environment, model, document, chunk, fingerprint = await _setup(db)
    unauthorized = await client.post("/api/insight/analyze", json={})
    assert unauthorized.status_code == 401
    # The existing Analytics Read permission is granted to viewers; auth is
    # exercised without introducing a parallel reviewer role.
    viewer_headers = await auth_headers(client, viewer)
    assert viewer_headers["Authorization"].startswith("Bearer ")

    from app.api import insight_routes
    from app.insight.service import InsightService

    def provider(payload, _context):
        return {"items": [{"kind": "observed_fact", "statement": "Quarterly revenue is reported as 120 units.", "source_references": payload["source_references"]}]}

    class TestInsightService(InsightService):
        def __init__(self):
            super().__init__(provider=provider)

    monkeypatch.setattr(insight_routes, "InsightService", TestInsightService)
    reference = {"document_id": str(document.id), "chunk_id": str(chunk.id), "source_title": "Quarterly Report", "content_fingerprint": fingerprint, "page_number": 1}
    response = await client.post("/api/insight/analyze", json={"environment_id": str(environment.id), "model_version_id": str(model.id), "idempotency_key": "insight-valid-01", "metrics": [{"name": "revenue", "value": 120}], "source_references": [reference], "question": "Summarize revenue", "analysis_type": "summary"}, headers=await auth_headers(client, owner))
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "succeeded"
    assert result["result"]["items"][0]["source_references"][0]["content_fingerprint"] == fingerprint
    assert result["lineage_root_id"] and result["evidence_root_hash"]
    persisted = await db.get(SpecializedExecutionRecord, uuid.UUID(result["execution_id"]))
    assert persisted is not None and persisted.policy_decision_id is not None
    assert persisted.result["items"][0]["statement"] == "[redacted]"

    tampered = dict(reference, content_fingerprint="0"*64)
    rejected = await client.post("/api/insight/analyze", json={"environment_id": str(environment.id), "model_version_id": str(model.id), "idempotency_key": "insight-tampered-01", "metrics": [{"name": "revenue", "value": 120}], "source_references": [tampered], "question": "Summarize revenue"}, headers=await auth_headers(client, owner))
    assert rejected.status_code == 422

    other_tenant, other_owner, _viewer, _env, _model, _doc, _chunk, _fingerprint = await _setup(db, "insight-other-tenant")
    cross = await client.get(f"/api/insight/{result['execution_id']}?environment_id={environment.id}", headers=await auth_headers(client, other_owner))
    assert cross.status_code == 404
    assert other_tenant.id != tenant.id
