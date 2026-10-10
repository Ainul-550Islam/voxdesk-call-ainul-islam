"""Unit and integration tests for Multi-Provider STT/TTS/LLM/S2S Registry & Voice Cloning (Sub-Phase 2C)."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.agent.providers import (
    LLM_PROVIDER_CATALOG,
    S2S_PROVIDER_CATALOG,
    STT_PROVIDER_CATALOG,
    TTS_PROVIDER_CATALOG,
    ProviderNotConfigured,
    build_llm_provider,
    build_s2s_provider,
    build_stt_provider,
    build_tts_provider,
    format_boosted_keywords,
    list_provider_catalog,
)
from app.db.models import UserRole, VoiceProfile
from sqlalchemy import select
from tests.conftest import auth_headers, make_tenant, make_user


class _DummySettings:
    pass


def test_missing_api_key_raises_provider_not_configured(monkeypatch):
    empty = _DummySettings()
    for spec_map in (
        STT_PROVIDER_CATALOG,
        TTS_PROVIDER_CATALOG,
        LLM_PROVIDER_CATALOG,
        S2S_PROVIDER_CATALOG,
    ):
        for spec in spec_map.values():
            for env_name in spec.required_env:
                monkeypatch.delenv(env_name, raising=False)

    for prov in STT_PROVIDER_CATALOG:
        with pytest.raises(ProviderNotConfigured) as exc_info:
            build_stt_provider(prov, configured_settings=empty)
        assert exc_info.value.provider == prov
        assert len(exc_info.value.missing_env) >= 1

    for prov in TTS_PROVIDER_CATALOG:
        with pytest.raises(ProviderNotConfigured) as exc_info:
            build_tts_provider(prov, configured_settings=empty)
        assert exc_info.value.provider == prov

    for prov in LLM_PROVIDER_CATALOG:
        with pytest.raises(ProviderNotConfigured) as exc_info:
            build_llm_provider(prov, configured_settings=empty)
        assert exc_info.value.provider == prov

    for prov in S2S_PROVIDER_CATALOG:
        with pytest.raises(ProviderNotConfigured) as exc_info:
            build_s2s_provider(prov, configured_settings=empty)
        assert exc_info.value.provider == prov


def test_configured_providers_instantiate_and_format_boosted_keywords():
    cfg_settings = _DummySettings()
    cfg_settings.deepgram_api_key = "dg-test-key"
    cfg_settings.assemblyai_api_key = "aai-test-key"
    cfg_settings.openai_api_key = "sk-test-openai-key"
    cfg_settings.anthropic_api_key = "sk-ant-test-key"
    cfg_settings.google_api_key = "goog-test-key"
    cfg_settings.groq_api_key = "gsk-test-key"
    cfg_settings.elevenlabs_api_key = "el-test-key"
    cfg_settings.elevenlabs_voice_id = "21m00Tcm4TlvDq8ikWAM"
    cfg_settings.cartesia_api_key = "cart-test-key"
    cfg_settings.playht_api_key = "pht-test-key"
    cfg_settings.playht_user_id = "pht-user-id"
    cfg_settings.azure_speech_key = "az-speech-key"
    cfg_settings.azure_speech_region = "eastus"
    cfg_settings.azure_openai_api_key = "az-oai-key"
    cfg_settings.azure_openai_endpoint = "https://example.openai.azure.com"
    cfg_settings.aws_access_key_id = "AKIAIOSFODNN7EXAMPLE"
    cfg_settings.aws_secret_access_key = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    cfg_settings.aws_region = "us-east-1"
    cfg_settings.custom_openai_api_key = "custom-key"
    cfg_settings.custom_openai_base_url = "https://llm.internal/v1"

    kw = format_boosted_keywords([("Invisalign", 3.5), ("Dr. Patel", 2.0)])
    assert kw == ["Invisalign:3.5", "Dr. Patel:2"]

    dg_stt = build_stt_provider(
        "deepgram",
        boosted_keywords=[("Invisalign", 3.5)],
        configured_settings=cfg_settings,
    )
    assert getattr(dg_stt, "provider", None) == "deepgram"
    assert getattr(dg_stt, "keywords", None) == ["Invisalign:3.5"]

    for stt_p in ("assemblyai", "openai_whisper", "azure", "google"):
        svc = build_stt_provider(stt_p, configured_settings=cfg_settings)
        assert getattr(svc, "provider", None) == stt_p

    for tts_p in ("elevenlabs", "openai", "deepgram", "cartesia", "playht", "azure", "google"):
        svc = build_tts_provider(
            tts_p,
            voice_settings={"stability": 0.6, "speed": 1.1, "pitch_semitones": 2.0},
            configured_settings=cfg_settings,
        )
        assert getattr(svc, "provider", None) == tts_p

    for llm_p in ("openai", "anthropic", "google", "groq", "azure_openai", "bedrock", "custom_openai"):
        svc = build_llm_provider(llm_p, configured_settings=cfg_settings)
        assert getattr(svc, "provider", None) == llm_p

    catalog = list_provider_catalog(configured_settings=cfg_settings)
    assert len(catalog) >= 20


@pytest.mark.asyncio
async def test_voice_catalog_and_elevenlabs_voice_clone_endpoint(client, db, monkeypatch):
    tenant = await make_tenant(db, name="Voice Clone Clinic")
    owner = await make_user(db, tenant, UserRole.OWNER)
    headers = await auth_headers(client, owner)

    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-live-test-key")

    cat_res = await client.get("/api/voices/catalog", headers=headers)
    assert cat_res.status_code == 200, cat_res.text
    cat_body = cat_res.json()
    assert cat_body["total"] >= 7
    assert any(item["provider"] == "elevenlabs" for item in cat_body["items"])

    with respx.mock(assert_all_called=True) as mock_http:
        mock_http.post("https://api.elevenlabs.io/v1/voices/add").mock(
            return_value=httpx.Response(200, json={"voice_id": "cloned_el_voice_999"})
        )
        clone_res = await client.post(
            "/api/voices/clone",
            headers=headers,
            data={
                "name": "Dr. Smith Cloned Voice",
                "description": "Warm clinical tone",
                "language": "en-US",
            },
            files={"file": ("sample.wav", b"RIFF\x24\x00\x00\x00WAVEfmt ", "audio/wav")},
        )
    assert clone_res.status_code == 200, clone_res.text
    clone_body = clone_res.json()
    assert clone_body["provider"] == "elevenlabs"
    assert clone_body["provider_voice_id"] == "cloned_el_voice_999"
    assert clone_body["voice_type"] == "cloned"

    rows = (
        await db.execute(select(VoiceProfile).where(VoiceProfile.tenant_id == tenant.id))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].provider_voice_id == "cloned_el_voice_999"
