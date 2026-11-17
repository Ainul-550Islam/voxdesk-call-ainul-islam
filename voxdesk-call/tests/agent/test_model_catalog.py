"""P12-10 — the model catalogue must be complete, consistent, and honest about gaps."""

from __future__ import annotations

from datetime import date

import pytest

from app.agent.providers.model_catalog import (
    CATALOG,
    UnknownModelError,
    capabilities,
    catalog_index,
    default_for,
    deprecated_models,
    get_model,
    models_for,
    validate_catalog,
)
from scripts.refresh_model_catalog import collect_statuses, main as refresh_main


def test_the_catalogue_validates_and_has_no_duplicates() -> None:
    assert validate_catalog() == []
    keys = [(spec.provider, spec.model_id) for spec in CATALOG]
    assert len(keys) == len(set(keys))


def test_lookup_returns_the_spec_and_refuses_unknown_models() -> None:
    spec = get_model("deepgram", "nova-2")
    assert spec.kind == "stt"
    assert spec.supports_streaming is True

    with pytest.raises(UnknownModelError) as excinfo:
        get_model("deepgram", "nova-9")
    assert "Known deepgram models" in str(excinfo.value)


def test_capability_flags_are_exposed_per_model() -> None:
    caps = capabilities("openai", "gpt-4o-mini")
    assert caps["tools"] is True and caps["structured"] is True
    assert caps["realtime"] is False
    assert caps["context_window"] == 128_000

    realtime = capabilities("openai", "gpt-4o-realtime-preview")
    assert realtime["realtime"] is True


def test_structured_without_tools_is_rejected_as_a_fiction() -> None:
    from app.agent.providers.model_catalog import ModelSpec

    broken = ModelSpec("x", "y", "llm", supports_structured=True, supports_tools=False)
    assert any("structured output without tool support" in p for p in broken.validate())


def test_filters_by_provider_and_kind() -> None:
    tts = models_for(provider="elevenlabs", kind="tts")
    assert {spec.model_id for spec in tts} >= {"eleven_turbo_v2_5", "eleven_v3"}
    assert all(spec.provider == "elevenlabs" for spec in models_for(provider="elevenlabs"))
    assert models_for(kind="s2s")


def test_every_provider_the_repo_talks_to_is_catalogued() -> None:
    providers = set(catalog_index())
    assert {"openai", "anthropic", "deepgram", "elevenlabs", "cartesia", "minimax", "fish_audio"} <= providers
    for kind in ("llm", "stt", "tts", "s2s"):
        assert models_for(kind=kind), kind


def test_deprecation_is_a_date_test_not_a_vibe() -> None:
    from app.agent.providers.model_catalog import ModelSpec

    retired = ModelSpec("x", "old-model", "llm", deprecated_on="2026-01-01")
    assert retired.is_deprecated(date(2026, 6, 1)) is True
    assert retired.is_deprecated(date(2025, 6, 1)) is False
    assert ModelSpec("x", "new", "llm").is_deprecated(date(2030, 1, 1)) is False
    with pytest.raises(ValueError):
        ModelSpec("x", "y", "llm", deprecated_on="soon").is_deprecated()


def test_default_for_prefers_fast_and_skips_deprecated() -> None:
    spec = default_for("openai", "llm")
    assert spec.model_id == "gpt-4o-mini"
    assert spec.latency_tier == "fast"
    with pytest.raises(UnknownModelError):
        default_for("minimax", "s2s")


def test_prices_are_estimates_or_absent_never_invented() -> None:
    for spec in CATALOG:
        if spec.usd_per_minute is not None:
            assert spec.usd_per_minute > 0
            assert spec.notes, f"{spec.provider}/{spec.model_id} has a price but no provenance note"


def test_refresh_script_dry_run_touches_nothing() -> None:
    statuses = collect_statuses(allow_network=False)
    assert statuses
    assert all(status.fetched is False for status in statuses)
    assert all("dry run" in status.reason or "no public model-list endpoint" in status.reason
               for status in statuses)


def test_refresh_script_without_keys_reports_the_missing_variable(monkeypatch) -> None:
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPGRAM_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    statuses = collect_statuses(allow_network=True)
    fetched = [status for status in statuses if status.fetched]
    assert fetched == [], "nothing may be fetched without a key"
    assert any("is not set" in status.reason for status in statuses)


def test_refresh_cli_exits_zero_and_names_deprecations(capsys) -> None:
    assert refresh_main([]) == 0
    out = capsys.readouterr().out
    assert "catalogue valid" in out
    assert "dry run" in out


def test_no_deprecated_entry_silently_stays_in_use() -> None:
    """If anything is ever deprecated, the test suite must say so out loud."""
    deprecated = deprecated_models()
    assert deprecated == [], (
        "catalogue contains deprecated models: "
        + ", ".join(f"{spec.provider}/{spec.model_id}" for spec in deprecated)
    )
