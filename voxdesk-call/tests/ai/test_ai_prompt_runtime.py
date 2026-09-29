"""Prompt versions stay deterministic and production selection is registry-backed."""

from __future__ import annotations

import uuid

from app.ai.prompts.rollout import bucket
from app.ai.prompts.versioning import checksum


def test_rollout_bucket_and_prompt_checksum_are_stable():
    tenant_id = uuid.uuid4()
    slot = bucket(tenant_id, "agent.system", "release-2026-09")
    assert 0 <= slot < 100
    assert bucket(tenant_id, "agent.system", "release-2026-09") == slot
    assert checksum("immutable prompt") == checksum("immutable prompt")
    assert checksum("immutable prompt") != checksum("changed prompt")


def test_runtime_resolves_prompt_through_existing_registry():
    from pathlib import Path

    source = Path("app/ai/gateway.py").read_text(encoding="utf-8")
    runtime = Path("app/ai/runtime.py").read_text(encoding="utf-8")
    registry = Path("app/ai/prompts/registry.py").read_text(encoding="utf-8")
    assert "resolve_for_runtime" in source
    assert "live.prompt" in runtime
    assert "version_for_runtime" in registry
    assert "checksum" in registry
