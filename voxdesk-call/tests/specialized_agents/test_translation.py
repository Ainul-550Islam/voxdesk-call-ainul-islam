"""Behavioral tests for translation, glossary, and quality validation."""

from __future__ import annotations

import pytest

from app.specialized_agents.context import SpecializedAgentContext
from app.translation.engine import TranslationEngine
from app.translation.glossary import Glossary
from app.translation.quality import validate_translation
from app.translation.schemas import GlossaryEntryInput, TranslationSegment


def glossary():
    return Glossary.from_inputs(
        "v1",
        [GlossaryEntryInput(source_term="VoxDesk", target_term="VoxDesk", preserve_exact=True, version="v1")],
    )


def context():
    import uuid

    return SpecializedAgentContext(
        tenant_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        actor_id=uuid.uuid4(),
        request_id="translation-request",
        trace_id="translation-trace",
        agent_type="translation",
        agent_version="1.0.0",
        policy_version=1,
        model_version_id=uuid.uuid4(),
        risk_tier="moderate",
        language="fr",
    )


@pytest.mark.asyncio
async def test_translation_uses_language_and_glossary_version_and_preserves_terms():
    calls = []

    async def provider(prompt, source_language, target_language, _context):
        calls.append((prompt, source_language, target_language))
        return "Bonjour VoxDesk 42"

    segment = TranslationSegment(segment_id="s1", source_text="Hello VoxDesk 42")
    result = await TranslationEngine(provider=provider).translate(
        source_language="en",
        target_language="fr",
        segments=[segment],
        glossary=glossary(),
        context=context(),
    )
    assert result["source_language"] == "en"
    assert result["target_language"] == "fr"
    assert result["glossary_version"] == "v1"
    assert result["quality_status"] == "verified"
    assert result["segments"][0]["translated_text"] == "Bonjour VoxDesk 42"
    assert calls[0][1:] == ("en", "fr")


def test_translation_quality_flags_numbers_urls_placeholders_and_review():
    source = "Hello {{name}} visit https://example.com or email a@example.com; total 42."
    target = "Bonjour {name} visitez https://wrong.example.com ou contactez b@example.com; total 24."
    report = validate_translation(
        [TranslationSegment(segment_id="s1", source_text=source, translated_text=target)],
        glossary(),
    )
    codes = {flag.code for flag in report.flags}
    assert {"placeholder_corruption", "number_corruption", "url_corruption", "email_corruption"} <= codes
    assert report.review_required is True
    assert report.status == "degraded"


def test_language_validation_rejects_same_language_and_untranslated_output():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        from app.translation.schemas import TranslationJobRequest

        TranslationJobRequest(
            environment_id=context().environment_id,
            model_version_id=context().model_version_id,
            idempotency_key="translation-idem",
            source_language="en",
            target_language="EN",
            segments=[TranslationSegment(segment_id="s1", source_text="same")],
            glossary_version="v1",
        )
    report = validate_translation(
        [TranslationSegment(segment_id="s1", source_text="same", translated_text="same")], glossary()
    )
    assert any(flag.code == "untranslated_source" for flag in report.flags)
