"""End-to-end PSTN latency: a real phone call through real providers.

This is the only test in the suite that measures the product the way a customer
experiences it: a call leaves the carrier, crosses the PSTN, is answered by this
deployment, and the answer comes back as audio. Every number asserted below was
produced by the live pipeline and read back from its own persistence.

It is skipped unless the deployment is configured for a live proof, because a
missing credential must never be papered over with a simulated call:

    LIVE_PROOF_ENABLED=1
    LIVE_PROOF_TEST_NUMBER=+1...       # the agent's own number
    LIVE_PROOF_CALLER_ID=+1...         # the number that places the call
    LIVE_PROOF_TENANT_ID=<uuid>        # the tenant that owns the agent
    TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_PHONE_NUMBER
    DEEPGRAM_API_KEY / OPENAI_API_KEY / ELEVENLABS_API_KEY

Thresholds: ``LIVE_PROOF_MAX_E2E_P95_MS`` (default 1500) and
``LIVE_PROOF_MIN_TURNS`` (default 2). They are operator-tunable because a
transatlantic PSTN leg is not a lab measurement.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest

from app.core.config import settings
from app.telephony.live_proof.pstn_probe import (
    LiveProofResult,
    ProviderNotConfigured,
    missing_provider_settings,
    run_pstn_probe,
)

pytestmark = [pytest.mark.live, pytest.mark.slow, pytest.mark.timeout(600)]

MAX_E2E_P95_MS = float(os.getenv("LIVE_PROOF_MAX_E2E_P95_MS", "1500"))
MIN_TURNS = int(os.getenv("LIVE_PROOF_MIN_TURNS", "2"))


def _live_tenant_id() -> str:
    raw = (os.getenv("LIVE_PROOF_TENANT_ID") or "").strip()
    if not raw:
        pytest.skip("live PSTN latency needs LIVE_PROOF_TENANT_ID (the tenant that owns the agent)")
    try:
        uuid.UUID(raw)
    except ValueError:
        pytest.fail(f"LIVE_PROOF_TENANT_ID is not a UUID: {raw!r}")
    return raw


def _require_live_harness() -> None:
    missing = missing_provider_settings()
    if missing:
        pytest.skip("live PSTN latency requires " + ", ".join(missing))
    if not settings.live_proof_enabled:
        pytest.skip("live PSTN latency requires LIVE_PROOF_ENABLED=1")


async def test_live_pstn_call_hits_the_latency_budget_with_real_providers(tmp_path):
    _require_live_harness()
    tenant_id = _live_tenant_id()

    try:
        result: LiveProofResult = await run_pstn_probe(
            tenant_id=tenant_id,
            evidence_dir=str(tmp_path),
            max_wait_seconds=float(settings.live_proof_timeout_seconds),
        )
    except ProviderNotConfigured as exc:  # pragma: no cover - guarded by _require_live_harness
        pytest.skip(f"provider not configured: {exc.message}")

    assert result.status == "succeeded", (result.status, result.error, result.notes)
    assert result.provider_fakes is False, result.notes
    assert result.turns >= MIN_TURNS, f"only {result.turns} turns were persisted"
    assert result.e2e_p50_ms is not None and result.e2e_p50_ms > 0
    assert result.e2e_p95_ms is not None
    assert result.e2e_p95_ms <= MAX_E2E_P95_MS, (
        f"p95 E2E {result.e2e_p95_ms} ms exceeded the {MAX_E2E_P95_MS} ms budget"
    )
    assert result.e2e_p99_ms is None or result.e2e_p99_ms <= MAX_E2E_P95_MS * 2
    assert result.stt_ttfb_p50_ms is not None
    assert result.llm_ttfb_p50_ms is not None
    assert result.tts_ttfb_p50_ms is not None
    assert result.call_sid, "the carrier never returned a call SID"
    assert result.call_id, "the inbound webhook never created a Call row"
    assert result.cost_usd > 0


async def test_live_pstn_evidence_is_hash_stamped_and_consistent(tmp_path):
    _require_live_harness()
    tenant_id = _live_tenant_id()

    result = await run_pstn_probe(
        tenant_id=tenant_id,
        evidence_dir=str(tmp_path),
        max_wait_seconds=float(settings.live_proof_timeout_seconds),
    )

    if not result.evidence_path:
        pytest.fail(f"the run produced no evidence file: {result.error} {result.notes}")

    path = Path(result.evidence_path)
    assert path.is_file()
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == result.evidence_sha256

    payload = json.loads(raw.decode("utf-8"))
    assert payload["provider_fakes"] is result.provider_fakes
    if not result.provider_fakes:
        assert payload["missing_real_stages"] == []
    else:
        assert payload["missing_real_stages"]
    assert payload["run_id"] == result.run_id
    assert payload["tenant_id"] == str(tenant_id)
    assert payload["recording_sha256"] == result.recording_sha256
    assert payload["stage_metrics"]["e2e"]["p95_ms"] == result.e2e_p95_ms
    assert payload["runtime"]["python_version"]
    assert payload["required_stages"] == ["telephony", "stt", "llm", "tts"]


async def test_live_pstn_run_row_is_readable_after_the_call(tmp_path):
    """The durable row and the returned result must agree."""
    _require_live_harness()
    tenant_id = _live_tenant_id()

    from sqlalchemy import select

    from app.db.live_proof_models import LiveProofRun
    from app.db.session import get_sessionmaker

    result = await run_pstn_probe(
        tenant_id=tenant_id,
        evidence_dir=str(tmp_path),
        max_wait_seconds=float(settings.live_proof_timeout_seconds),
    )

    session_factory = get_sessionmaker()
    async with session_factory() as session:
        row = await session.scalar(
            select(LiveProofRun).where(LiveProofRun.id == uuid.UUID(result.run_id))
        )

    assert row is not None, "the run row was not persisted"
    assert row.tenant_id == uuid.UUID(tenant_id)
    assert row.status == result.status
    assert row.provider_fakes is result.provider_fakes
    assert row.evidence_sha256 == result.evidence_sha256
    assert row.turns == result.turns
    assert row.is_evidence is (result.status == "succeeded" and not result.provider_fakes)
