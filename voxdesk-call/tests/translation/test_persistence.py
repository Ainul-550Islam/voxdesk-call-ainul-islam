"""Durable glossary, translation result projection and retry state tests."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import Environment, Organization, UserRole
from app.governance.context import GovernanceScope
from app.governance.models import GovernancePolicy, ModelRegistry, ModelVersion, RiskAssessment
from app.specialized_agents.service import SpecializedAgentService
from app.tenancy.isolation import BoundaryDenied, Conflict
from app.translation.job_service import get_or_create_glossary, retry_failed_segments
from app.translation.persistence import TranslationAttemptRecord, TranslationProgressRecord, TranslationSegmentRecord, persist_translation
from tests.conftest import make_tenant, make_user


async def _seed(db, name):
    tenant = await make_tenant(db, name)
    user = await make_user(db, tenant, UserRole.OWNER)
    organization = await db.get(Organization, tenant.organization_id)
    environment = await db.scalar(select(Environment).where(Environment.tenant_id == tenant.id, Environment.kind == "production"))
    scope = GovernanceScope(tenant, organization, environment)
    registry = ModelRegistry(tenant_id=tenant.id, organization_id=organization.id, provider=f"local-{uuid.uuid4().hex}", model_name="translation-test", display_name="Translation Test", status="active", risk_tier="moderate", intended_use="local persistence test", data_classes=[], capabilities=["translation"], approved_for_channels=["translation"], approved_for_environments=[str(environment.id)], metadata_json={})
    db.add(registry)
    await db.flush()
    model = ModelVersion(registry_id=registry.id, tenant_id=tenant.id, organization_id=organization.id, provider=registry.provider, model_name=registry.model_name, version="1", fingerprint="c"*64, artifact_uri="registry://local/translation", artifact_digest="d"*64, capabilities=["translation"], input_modalities=["text"], output_modalities=["text"], status="approved", evaluation_status="passed", evaluation_summary={"authoritative_verifier": "local-test", "passed": True})
    db.add_all([model, GovernancePolicy(tenant_id=tenant.id, organization_id=organization.id, environment_id=environment.id, name=f"translation-policy-{uuid.uuid4()}", policy_type="specialized_agent", status="published", version=1, rules={"decision": "allow"}, rationale="translation persistence test", created_by=user.id), RiskAssessment(tenant_id=tenant.id, organization_id=organization.id, environment_id=environment.id, subject_type="specialized_agent", subject_id="translation", version=1, tier="moderate", status="approved", factors={}, required_controls=[], rationale="test", assessed_by=user.id, approved_by=user.id)])
    await db.flush()
    return tenant, user, scope, model


@pytest.mark.asyncio
async def test_translation_job_segments_progress_glossary_immutability_and_retry(db):
    tenant, user, scope, model = await _seed(db, "translation-persistence")
    entries = [{"source_term": "Hello", "target_term": "Hola", "domain": "general", "case_behavior": "preserve", "preserve_exact": True, "notes": None, "version": "g-1", "active": True}]
    glossary = await get_or_create_glossary(db, scope, version="g-1", entries=entries)
    assert glossary.entries == entries
    service = SpecializedAgentService(db, scope)
    context = service.context(actor_id=user.id, request_id=uuid.uuid4().hex, trace_id=uuid.uuid4().hex, agent_type="translation", agent_version="1.0.0", model_version_id=model.id, risk_tier="moderate")
    output = {"source_language": "en", "target_language": "es", "glossary_version": "g-1", "quality_status": "degraded", "quality_flags": [{"code": "needs_review", "segment_id": "s1"}], "review_required": True, "review_state": "required", "provider": "local-test", "segments": [{"segment_id": "s1", "source_fingerprint": "a"*64, "target_fingerprint": "b"*64, "translated_text": "Hola"}, {"segment_id": "s2", "source_fingerprint": "c"*64, "target_fingerprint": "d"*64, "translated_text": "Buenos días"}]}
    outcome = await service.execute(context, idempotency_key="translation-persist-01", payload={"source_language": "en", "target_language": "es"}, handler=lambda *_: output)
    job = await persist_translation(db, scope, execution_id=outcome.record.id, output=output, dedupe_key="translation-persist-01", source_language="en", target_language="es", glossary_version="g-1", glossary_entries=entries, source_lengths={"s1": 5, "s2": 10})
    again = await persist_translation(db, scope, execution_id=outcome.record.id, output=output, dedupe_key="translation-persist-01", source_language="en", target_language="es", glossary_version="g-1", glossary_entries=entries, source_lengths={"s1": 5, "s2": 10})
    assert job.execution_id == again.execution_id
    assert job.status == "review_required"
    segments = list((await db.scalars(select(TranslationSegmentRecord).where(TranslationSegmentRecord.execution_id == outcome.record.id))).all())
    assert len(segments) == 2
    assert segments[0].source_length == 5 and segments[0].target_length == len("Hola")
    progress = await db.get(TranslationProgressRecord, outcome.record.id)
    assert progress.total_segments == 2 and progress.completed_segments == 2
    # The dedicated segment table stores integrity fingerprints, not translated text.
    assert not hasattr(segments[0], "translated_text")

    # Retry is bounded, records an attempt, and only requeues failed segments.
    segments[0].status = "failed"
    progress.state = "failed"
    progress.failed_segments = 1
    job.status = "failed"
    retryable = await retry_failed_segments(db, scope, execution_id=outcome.record.id, max_attempts=2)
    assert len(retryable) == 1 and retryable[0].segment_index == 0
    assert retryable[0].retry_count == 1
    assert progress.state == "queued"
    attempt = await db.scalar(select(TranslationAttemptRecord).where(TranslationAttemptRecord.execution_id == outcome.record.id))
    assert attempt is not None and attempt.status == "queued"
    with pytest.raises(Conflict):
        await get_or_create_glossary(db, scope, version="g-1", entries=[{"source_term": "Bye", "target_term": "Adiós"}])
    # ORM event guard prevents historical glossary-row mutation.
    glossary.active = False
    with pytest.raises(ValueError, match="immutable"):
        await db.flush()
    await db.rollback()


@pytest.mark.asyncio
async def test_translation_persistence_is_scope_checked(db):
    _tenant, user, scope, model = await _seed(db, "translation-scope-a")
    service = SpecializedAgentService(db, scope)
    context = service.context(actor_id=user.id, request_id=uuid.uuid4().hex, trace_id=uuid.uuid4().hex, agent_type="translation", agent_version="1", model_version_id=model.id, risk_tier="moderate")
    outcome = await service.execute(context, idempotency_key="translation-scope-01", payload={"x": 1}, handler=lambda *_: {"segments": [], "review_required": False})
    tenant_b = await make_tenant(db, "translation-scope-b")
    org_b = await db.get(Organization, tenant_b.organization_id)
    env_b = await db.scalar(select(Environment).where(Environment.tenant_id == tenant_b.id, Environment.kind == "production"))
    scope_b = GovernanceScope(tenant_b, org_b, env_b)
    with pytest.raises(BoundaryDenied):
        await persist_translation(db, scope_b, execution_id=outcome.record.id, output={"segments": []}, dedupe_key="cross-tenant", source_language="en", target_language="es", glossary_version="missing")
