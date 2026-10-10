"""Unit & integration tests for RuntimeConfig precedence and scoped RAG/tools (Sub-Phase 2E)."""

from __future__ import annotations

import uuid

import pytest

from app.db.enterprise_models import (
    AgentTool,
    Experiment,
    ExperimentVariant,
    KnowledgeCollection,
    KnowledgeCollectionSource,
)
from app.db.models import (
    Agent,
    AgentVersion,
    Call,
    CallDirection,
    CallStatus,
    DocumentSourceType,
    DocumentStatus,
    KnowledgeChunk,
    KnowledgeDocument,
    Tenant,
)
from app.knowledge.context import build_rag_block
from app.knowledge.embeddings import get_embedder
from app.runtime.agent_config_resolver import resolve_runtime_config
from app.telephony.number_provisioning import (
    PhoneNumber,
    PhoneNumberStatus,
    PhoneNumberType,
)


@pytest.mark.asyncio
async def test_resolver_falls_back_to_tenant_when_no_agents_exist(db):
    tenant = Tenant(
        id=uuid.uuid4(),
        name="Legacy Solo Tenant",
        twilio_number="+14155550300",
        system_prompt_extra="You are the legacy receptionist.",
        greeting="Hello from legacy tenant!",
        voice_id="legacy-eleven-voice",
        llm_provider="openai",
        llm_model="gpt-4o-mini",
    )
    db.add(tenant)
    await db.flush()

    call = Call(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        call_sid="CA_LEGACY_001",
        from_number="+14155559000",
        to_number="+14155550300",
        direction=CallDirection.INBOUND,
        status=CallStatus.IN_PROGRESS,
    )
    db.add(call)
    await db.flush()

    cfg = await resolve_runtime_config(db, call, tenant)
    assert cfg.source == "tenant_fallback"
    assert cfg.agent_id is None
    assert cfg.agent_version_id is None
    assert cfg.system_prompt == "You are the legacy receptionist."
    assert cfg.greeting == "Hello from legacy tenant!"
    assert cfg.voice_id == "legacy-eleven-voice"


@pytest.mark.asyncio
async def test_resolver_rejects_environment_mismatch(db):
    from sqlalchemy import select
    from app.db.models import Environment

    tenant = Tenant(
        id=uuid.uuid4(),
        name="Env Mismatch Tenant",
        twilio_number="+14155550399",
    )
    db.add(tenant)
    await db.flush()

    env_prod = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id,
                Environment.kind == "production",
            )
        )
    ).scalars().first()
    assert env_prod is not None

    env_staging = Environment(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="staging",
        slug="staging",
        kind="staging",
    )
    db.add(env_staging)
    await db.flush()

    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        environment_id=env_prod.id,
        external_key="prod-only-agent",
        name="Prod Agent",
        status="published",
    )
    db.add(agent)
    await db.commit()

    ok_cfg = await resolve_runtime_config(
        db,
        tenant=tenant,
        agent_id=agent.id,
        environment=env_prod.id,
    )
    assert ok_cfg.agent_id == agent.id
    assert ok_cfg.environment_id == env_prod.id

    with pytest.raises(ValueError, match="Environment mismatch"):
        await resolve_runtime_config(
            db,
            tenant=tenant,
            agent_id=agent.id,
            environment=env_staging.id,
        )


@pytest.mark.asyncio
async def test_resolver_precedence_experiment_over_phone_number_and_scoped_rag(db):
    tenant = Tenant(
        id=uuid.uuid4(),
        name="Experiment Precedence Tenant",
        twilio_number="+14155550400",
    )
    db.add(tenant)
    await db.flush()

    agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        external_key="exp-agent",
        name="Experiment Agent",
        status="published",
    )
    db.add(agent)
    await db.flush()

    ver_control = AgentVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        agent_id=agent.id,
        version_number=1,
        status="published",
        is_active=True,
        config_snapshot={
            "system_prompt": "Control prompt v1",
            "voice_id": "control-voice",
        },
    )
    ver_treatment = AgentVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        agent_id=agent.id,
        version_number=2,
        status="published",
        is_active=False,
        config_snapshot={
            "system_prompt": "Treatment prompt v2",
            "voice_id": "treatment-voice",
        },
    )
    db.add_all([ver_control, ver_treatment])
    await db.flush()
    agent.published_version_id = ver_control.id
    agent.published_version_number = 1

    pn = PhoneNumber(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        e164="+14155550400",
        friendly_name="Main Line",
        country_code="US",
        number_type=PhoneNumberType.LOCAL,
        provider="twilio",
        provider_sid="PN_EXP_001",
        status=PhoneNumberStatus.ACTIVE,
        is_primary=True,
        inbound_agent_id=agent.id,
        inbound_agent_version=1,
    )
    db.add(pn)

    # Create a running experiment with 100% traffic to treatment + config override
    exp = Experiment(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Voice A/B",
        agent_id=str(agent.id),
        status="running",
        description="Treatment voice converts better",
    )
    db.add(exp)
    await db.flush()

    variant_ctrl = ExperimentVariant(
        id=uuid.uuid4(),
        experiment_id=exp.id,
        tenant_id=tenant.id,
        name="control",
        weight=0,
        is_control=True,
        config={"agent_version_id": str(ver_control.id)},
    )
    variant = ExperimentVariant(
        id=uuid.uuid4(),
        experiment_id=exp.id,
        tenant_id=tenant.id,
        name="treatment",
        weight=100,
        is_control=False,
        config={
            "agent_version_id": str(ver_treatment.id),
            "greeting": "Experiment override greeting!",
        },
    )
    db.add_all([variant_ctrl, variant])

    # Add an AgentTool and a scoped KnowledgeCollection
    tool = AgentTool(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        agent_id=str(agent.id),
        name="check_order_status",
        description="Look up order status by ID",
        schema={"type": "object", "properties": {"order_id": {"type": "string"}}},
        is_enabled=True,
    )
    db.add(tool)

    embedder = get_embedder()
    vec_allowed = await embedder.embed_one("Standard cleaning costs $125 for new patients.")
    vec_other = await embedder.embed_one("Standard cleaning bonus for staff is $500.")

    doc_allowed = KnowledgeDocument(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        title="Allowed Pricing Policy",
        source_type=DocumentSourceType.TEXT,
        status=DocumentStatus.READY,
        content_hash="hash_allowed_001",
        embedding_model=embedder.identity,
        embedding_dimensions=len(vec_allowed),
        chunk_count=1,
    )
    doc_other = KnowledgeDocument(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        title="Internal HR Handbook",
        source_type=DocumentSourceType.TEXT,
        status=DocumentStatus.READY,
        content_hash="hash_other_002",
        embedding_model=embedder.identity,
        embedding_dimensions=len(vec_other),
        chunk_count=1,
    )
    db.add_all([doc_allowed, doc_other])
    await db.flush()

    chunk_allowed = KnowledgeChunk(
        id=uuid.uuid4(),
        document_id=doc_allowed.id,
        tenant_id=tenant.id,
        chunk_index=0,
        version=1,
        text="Standard cleaning costs $125 for new patients.",
        token_estimate=9,
        content_hash="chunk_hash_allowed_001",
        chunk_metadata={"heading": "Pricing"},
        embedding=vec_allowed,
        embedding_model=embedder.identity,
    )
    chunk_other = KnowledgeChunk(
        id=uuid.uuid4(),
        document_id=doc_other.id,
        tenant_id=tenant.id,
        chunk_index=0,
        version=1,
        text="Standard cleaning bonus for staff is $500.",
        token_estimate=9,
        content_hash="chunk_hash_other_002",
        chunk_metadata={"heading": "HR Bonus"},
        embedding=vec_other,
        embedding_model=embedder.identity,
    )
    db.add_all([chunk_allowed, chunk_other])

    collection = KnowledgeCollection(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        name="Patient FAQ",
        agent_ids=[str(agent.id)],
        is_active=True,
    )
    db.add(collection)
    await db.flush()

    col_src = KnowledgeCollectionSource(
        id=uuid.uuid4(),
        collection_id=collection.id,
        tenant_id=tenant.id,
        source_type="document",
        source_id=str(doc_allowed.id),
    )
    db.add(col_src)
    await db.commit()

    call = Call(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        call_sid="CA_EXP_CALL_1",
        from_number="+14155558888",
        to_number="+14155550400",
        direction=CallDirection.INBOUND,
        status=CallStatus.IN_PROGRESS,
    )
    db.add(call)
    await db.flush()

    cfg = await resolve_runtime_config(db, call, tenant)
    assert cfg.source == "experiment"
    assert cfg.agent_id == agent.id
    assert cfg.agent_version_id == ver_treatment.id
    assert cfg.experiment_id == exp.id
    assert cfg.variant_id == variant.id
    assert cfg.system_prompt == "Treatment prompt v2"
    assert cfg.greeting == "Experiment override greeting!"
    assert any(t["name"] == "check_order_status" for t in cfg.tools)
    assert cfg.knowledge_collection_ids == [collection.id]
    assert cfg.knowledge_document_ids == [doc_allowed.id]

    rag_block = await build_rag_block(
        db,
        tenant_id=tenant.id,
        query="What does standard cleaning cost?",
        collection_ids=cfg.knowledge_collection_ids,
    )
    assert "Allowed Pricing Policy" in rag_block
    assert "$125" in rag_block
    assert "Internal HR Handbook" not in rag_block
