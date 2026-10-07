"""Unit and database-backed service tests for ``app.domain.agent_models`` and ``app.services.agent_service``."""

from __future__ import annotations

from datetime import time

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError
from app.db.models import AgentVersion as AgentVersionRow, AuditLog
from app.domain.agent_models import (
    AgentConfig,
    AgentStatus,
    HandoffConfig,
    HandoffMode,
    LanguageConfig,
    ModelConfig,
    OperatingHours,
    SafetyPolicy,
    VoiceConfig,
    can_transition,
)
from app.services import agent_service


def _sample_config(tenant_id: str, name: str = "Support Concierge") -> AgentConfig:
    return AgentConfig(
        tenant_id=tenant_id,
        name=name,
        greeting="Welcome to Acme Dental. How may I direct your call?",
        system_prompt="You are a polite receptionist for Acme Dental.",
        persona="warm, concise, HIPAA-aware",
        voice=VoiceConfig(
            voice_id="voice_en_us_01",
            speech_speed=1.05,
            stability=0.8,
            similarity_boost=0.75,
            barge_in_enabled=True,
            silence_timeout_ms=1400,
        ),
        language=LanguageConfig(
            primary="en-US",
            fallbacks=("es-US", "fr-CA"),
            auto_detect=True,
        ),
        model=ModelConfig(
            provider="anthropic",
            model="claude-haiku-4-5-20251001",
            temperature=0.25,
            max_tokens=350,
            fallback_provider="openai",
        ),
        operating_hours=OperatingHours(
            timezone="America/New_York",
            start=time(8, 30),
            end=time(18, 0),
            days=(0, 1, 2, 3, 4),
            after_hours_greeting="We are currently closed.",
        ),
        handoff=HandoffConfig(
            mode=HandoffMode.WARM,
            destination="+14155550199",
            max_failed_turns=3,
            ring_timeout_seconds=30,
        ),
        safety=SafetyPolicy(
            pii_redaction=True,
            profanity_filter=True,
            record_calls=True,
            disallowed_topics=("medical_diagnosis",),
            require_consent_disclosure=True,
        ),
        enabled_tools=("book_appointment", "check_availability", "transfer_to_human"),
        knowledge_source_ids=("kb_faq", "kb_insurance"),
    )


class TestAgentDomainValidation:
    def test_valid_config_has_no_problems(self):
        cfg = _sample_config("tenant-1")
        assert cfg.validate() == []

    def test_rejects_bad_voice_bounds(self):
        cfg = AgentConfig(
            tenant_id="tenant-1",
            name="Agent",
            voice=VoiceConfig(speech_speed=3.5, stability=-0.1, silence_timeout_ms=50),
        )
        problems = cfg.validate()
        assert any("speech_speed" in p for p in problems)
        assert any("stability" in p for p in problems)
        assert any("silence_timeout_ms" in p for p in problems)

    def test_rejects_invalid_bcp47_and_handoff_phone(self):
        cfg = AgentConfig(
            tenant_id="tenant-1",
            name="Agent",
            language=LanguageConfig(primary="not_a_lang_tag!!"),
            handoff=HandoffConfig(mode=HandoffMode.WARM, destination="555-1234"),
        )
        problems = cfg.validate()
        assert any("language.primary" in p for p in problems)
        assert any("handoff.destination" in p for p in problems)

    def test_rejects_secret_like_metadata_keys(self):
        cfg = AgentConfig(
            tenant_id="tenant-1",
            name="Agent",
            metadata={"nested": {"openai_api_key": "sk-secret"}},
        )
        problems = cfg.validate()
        assert any("credential-like keys" in p for p in problems)

    def test_lifecycle_transitions(self):
        assert can_transition(AgentStatus.DRAFT, AgentStatus.PUBLISHED)
        assert can_transition(AgentStatus.PUBLISHED, AgentStatus.ARCHIVED)
        assert can_transition(AgentStatus.ARCHIVED, AgentStatus.DRAFT)
        assert can_transition(AgentStatus.PUBLISHED, AgentStatus.RETIRED)
        assert not can_transition(AgentStatus.RETIRED, AgentStatus.PUBLISHED)
        assert not can_transition(AgentStatus.RETIRED, AgentStatus.DRAFT)

    def test_snapshot_roundtrip_preserves_fields(self):
        cfg = _sample_config("tenant-1", "Roundtrip Agent")
        snap = cfg.to_snapshot_dict()
        restored = AgentConfig.from_snapshot_dict(snap, tenant_id="tenant-1")
        assert restored.name == cfg.name
        assert restored.greeting == cfg.greeting
        assert restored.language.primary == cfg.language.primary
        assert restored.handoff.destination == cfg.handoff.destination
        assert restored.canonical_hash() == cfg.canonical_hash()


class TestAgentServiceLifecycle:
    async def test_publish_rollback_and_retire_in_database(self, db, tenant_a):
        cfg = _sample_config(str(tenant_a.id))
        agent_row = await agent_service.create_draft_async(
            db, tenant_a, cfg, actor="owner@acme.test"
        )
        assert agent_row.status == "draft"
        assert agent_row.draft_etag.startswith('W/"')

        updated = await agent_service.configure_tools_async(
            db, tenant_a, cfg.id, ("book_appointment", "lookup_customer")
        )
        assert updated.enabled_tools == ("book_appointment", "lookup_customer")

        with_kb = await agent_service.attach_knowledge_sources_async(
            db, tenant_a, cfg.id, ("kb_1", "kb_2", "kb_1")
        )
        assert with_kb.knowledge_source_ids == ("kb_1", "kb_2")

        v1 = await agent_service.publish_async(
            db, tenant_a, with_kb, actor="owner@acme.test", changelog="initial release"
        )
        assert v1.version == 1
        assert tenant_a.agent_name == "Support Concierge"
        assert tenant_a.language == "en-US"
        assert tenant_a.escalation_number == "+14155550199"
        assert tenant_a.record_calls is True

        v2_cfg = _sample_config(str(tenant_a.id))
        v2_cfg = AgentConfig(
            **{
                **v2_cfg.__dict__,
                "greeting": "Updated v2 greeting",
                "language": LanguageConfig(primary="es-MX"),
            }
        )
        await agent_service.update_draft_async(db, tenant_a, v2_cfg)
        v2 = await agent_service.publish_async(
            db, tenant_a, v2_cfg, actor="owner@acme.test", changelog="spanish greeting"
        )
        assert v2.version == 2
        assert tenant_a.greeting == "Updated v2 greeting"
        assert tenant_a.language == "es-MX"

        rolled = await agent_service.rollback_async(
            db, tenant_a, cfg.id, target_version=1, actor="owner@acme.test"
        )
        assert rolled.version == 3
        assert rolled.is_rollback is True
        assert tenant_a.greeting.startswith("Welcome to Acme Dental")
        assert tenant_a.language == "en-US"

        history = await agent_service.version_history_async(db, tenant_a, cfg.id)
        assert [h.version for h in history] == [3, 2, 1]

        # Verify database rows directly
        db_versions = (
            await db.execute(
                select(AgentVersionRow)
                .where(AgentVersionRow.agent_id == agent_row.id)
                .order_by(AgentVersionRow.version_number.asc())
            )
        ).scalars().all()
        assert len(db_versions) == 3
        assert db_versions[0].version_number == 1
        assert db_versions[0].is_active is False
        assert db_versions[2].version_number == 3
        assert db_versions[2].is_active is True
        assert db_versions[2].is_rollback is True
        assert db_versions[2].source_version_id == db_versions[0].id

        await agent_service.retire_async(db, tenant_a, cfg.id)
        with pytest.raises(ValueError, match="retired"):
            await agent_service.publish_async(db, tenant_a, cfg)

        # Verify audit events were recorded
        audits = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_a.id,
                )
            )
        ).scalars().all()
        actions = {
            (a.detail or {}).get("event")
            for a in audits
            if (a.detail or {}).get("resource_type") == "agent"
        }
        assert "agent.created" in actions
        assert "agent.published" in actions
        assert "agent.rolled_back" in actions
        assert "agent.retired" in actions

    async def test_optimistic_concurrency_conflict_on_stale_etag(self, db, tenant_a):
        cfg = _sample_config(str(tenant_a.id), "Concurrency Agent")
        row = await agent_service.create_draft_async(db, tenant_a, cfg)
        initial_etag = row.draft_etag

        cfg_v2 = AgentConfig(**{**cfg.__dict__, "greeting": "Updated once"})
        row = await agent_service.update_draft_async(
            db, tenant_a, cfg_v2, expected_etag=initial_etag
        )
        assert row.draft_etag != initial_etag

        cfg_v3 = AgentConfig(**{**cfg.__dict__, "greeting": "Stale overwrite attempt"})
        with pytest.raises(ConflictError):
            await agent_service.update_draft_async(
                db, tenant_a, cfg_v3, expected_etag=initial_etag
            )

    async def test_from_tenant_and_projection_have_no_secret_fields(self, db, tenant_a):
        cfg = agent_service.from_tenant(tenant_a)
        proj = agent_service.project(
            cfg, status=AgentStatus.PUBLISHED, active_version=1
        )
        assert proj.tenant_id == str(tenant_a.id)
        assert "api_key" not in proj.__dict__
        assert "secret" not in proj.__dict__
