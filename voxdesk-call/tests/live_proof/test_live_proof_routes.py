"""Live-proof HTTP surface: RBAC, tenant isolation, idempotency and refusal.

The runner is overridden here because these tests are about the route contract --
permissions, scoping, idempotent replay, refusal when the harness is off. Nothing
in this file claims a live call happened: the live call is covered by
``tests/test_live_pstn_latency.py``, which is marked ``live`` and skips without
provider credentials.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from app.api.live_proof_routes import get_live_proof_runner
from app.core.config import settings
from app.db.live_proof_models import LiveProofRun
from app.telephony.live_proof.pstn_probe import LiveProofResult

pytestmark = pytest.mark.asyncio

_PROVIDER_SETTINGS = (
    "twilio_account_sid",
    "twilio_auth_token",
    "twilio_phone_number",
    "deepgram_api_key",
    "elevenlabs_api_key",
    "openai_api_key",
    "live_proof_test_number",
)


@pytest.fixture
def harness_on(monkeypatch):
    """Configuration that would permit a run -- no credentials are real here."""
    monkeypatch.setattr(settings, "live_proof_enabled", True)
    monkeypatch.setattr(settings, "twilio_account_sid", "AC" + "0" * 32)
    monkeypatch.setattr(settings, "twilio_auth_token", "auth-token-0123456789abcdef")
    monkeypatch.setattr(settings, "twilio_phone_number", "+15550001111")
    monkeypatch.setattr(settings, "deepgram_api_key", "dg-key-0123456789")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "el-key-0123456789")
    monkeypatch.setattr(settings, "openai_api_key", "sk-key-0123456789")
    monkeypatch.setattr(settings, "live_proof_test_number", "+15550002222")
    monkeypatch.setattr(settings, "live_proof_caller_id", "+15550001111")
    monkeypatch.setattr(settings, "live_proof_max_minutes", 3.0)
    monkeypatch.setattr(settings, "live_proof_max_usd", 0.5)
    monkeypatch.setattr(settings, "live_proof_usd_per_minute", 0.05)
    return monkeypatch


@pytest.fixture
def runner_override(app):
    """Install a runner that returns a measured-looking result without dialling."""

    async def _runner(**kwargs) -> LiveProofResult:
        return LiveProofResult(
            run_id=kwargs["run_id"],
            tenant_id=kwargs["tenant_id"],
            status="succeeded",
            provider_set=("twilio", "deepgram", "elevenlabs", "openai"),
            provider_fakes=False,
            call_sid="CA" + "1" * 32,
            call_id=str(uuid.uuid4()),
            turns=4,
            e2e_p50_ms=742.5,
            e2e_p95_ms=1180.0,
            e2e_p99_ms=1201.0,
            stt_ttfb_p50_ms=180.0,
            llm_ttfb_p50_ms=260.0,
            tts_ttfb_p50_ms=190.0,
            cost_usd=0.0125,
            minutes=0.25,
            started_at=datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc),
            finished_at=datetime(2026, 10, 24, 9, 1, tzinfo=timezone.utc),
            notes=("measured by the pipeline",),
        )

    app.dependency_overrides[get_live_proof_runner] = lambda: _runner
    yield _runner
    app.dependency_overrides.pop(get_live_proof_runner, None)


async def _headers(client, user) -> dict:
    from tests.conftest import auth_headers

    return await auth_headers(client, user)


async def test_readiness_reports_exactly_what_is_missing(client, viewer_a):
    headers = await _headers(client, viewer_a)

    response = await client.get("/api/live-proof/readiness", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is False
    assert body["ready"] is False
    assert body["calls_are_real"] is True
    assert "LIVE_PROOF_ENABLED is not set" in " ".join(body["problems"]) or body["missing_settings"]
    assert body["router_prefix"] == "/api/live-proof"


async def test_readiness_is_ready_only_with_every_credential(client, viewer_a, harness_on):
    headers = await _headers(client, viewer_a)

    body = (await client.get("/api/live-proof/readiness", headers=headers)).json()

    assert body["enabled"] is True
    assert body["missing_settings"] == []
    assert body["ready"] is True


async def test_readiness_requires_authentication(client):
    response = await client.get("/api/live-proof/readiness")

    assert response.status_code in {401, 403}


async def test_starting_a_run_requires_the_write_permission(client, viewer_a, harness_on):
    headers = await _headers(client, viewer_a)

    response = await client.post(
        "/api/live-proof/runs",
        json={},
        headers={**headers, "Idempotency-Key": "viewer-attempt-1"},
    )

    assert response.status_code == 403


async def test_a_manager_cannot_start_a_live_proof(client, manager_a, harness_on):
    """Owner/admin only: a manager holds CALL_WRITE but cannot spend the budget."""
    headers = await _headers(client, manager_a)

    response = await client.post(
        "/api/live-proof/runs",
        json={},
        headers={**headers, "Idempotency-Key": "manager-attempt-1"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "admin_only"


async def test_an_idempotency_key_is_mandatory(client, owner_a, harness_on):
    headers = await _headers(client, owner_a)

    missing = await client.post("/api/live-proof/runs", json={}, headers=headers)
    too_short = await client.post(
        "/api/live-proof/runs", json={}, headers={**headers, "Idempotency-Key": "short"}
    )

    assert missing.status_code == 400
    assert missing.json()["detail"]["code"] == "idempotency_key_required"
    assert too_short.status_code == 400
    assert too_short.json()["detail"]["code"] == "idempotency_key_invalid"


async def test_starting_a_run_is_refused_when_the_harness_is_off(client, owner_a, monkeypatch):
    for name in _PROVIDER_SETTINGS:
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "live_proof_enabled", False)
    headers = await _headers(client, owner_a)

    response = await client.post(
        "/api/live-proof/runs",
        json={},
        headers={**headers, "Idempotency-Key": "disabled-run-1"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "live_proof_disabled"


async def test_starting_a_run_names_the_missing_provider_credentials(client, owner_a, harness_on):
    harness_on.setattr(settings, "deepgram_api_key", "")
    harness_on.setattr(settings, "live_proof_test_number", "")
    headers = await _headers(client, owner_a)

    response = await client.post(
        "/api/live-proof/runs",
        json={},
        headers={**headers, "Idempotency-Key": "missing-creds-1"},
    )

    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "provider_not_configured"
    assert "DEEPGRAM_API_KEY" in body["detail"]["missing"]
    assert "LIVE_PROOF_TEST_NUMBER" in body["detail"]["missing"]


async def test_a_misconfigured_harness_is_refused_with_its_reason(client, owner_a, harness_on):
    harness_on.setattr(settings, "live_proof_max_minutes", 0.0)
    headers = await _headers(client, owner_a)

    response = await client.post(
        "/api/live-proof/runs",
        json={},
        headers={**headers, "Idempotency-Key": "misconfigured-1"},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "live_proof_misconfigured"


async def test_a_run_is_created_with_the_measured_result(client, owner_a, harness_on, runner_override):
    headers = await _headers(client, owner_a)

    response = await client.post(
        "/api/live-proof/runs",
        json={"to_number": "+15550002222", "note": "operator check"},
        headers={**headers, "Idempotency-Key": "happy-path-run-1"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["provider_fakes"] is False
    # The runner returned no evidence hash, so the row must not claim to be
    # evidence: a run only counts as proof when a hash-stamped artifact exists.
    assert body["is_evidence"] is False
    assert body["turns"] == 4
    assert body["e2e_p50_ms"] == 742.5
    assert body["call_sid"].startswith("CA")
    assert body["provider_set"] == ["twilio", "deepgram", "elevenlabs", "openai"]


async def test_a_replayed_start_returns_the_same_run_and_never_dials_twice(
    client, owner_a, harness_on, app
):
    dialled: list[dict] = []

    async def _counting_runner(**kwargs) -> LiveProofResult:
        dialled.append(kwargs)
        return LiveProofResult(
            run_id=kwargs["run_id"],
            tenant_id=kwargs["tenant_id"],
            status="succeeded",
            provider_set=("twilio",),
            provider_fakes=False,
            turns=2,
            e2e_p50_ms=500.0,
        )

    app.dependency_overrides[get_live_proof_runner] = lambda: _counting_runner
    headers = await _headers(client, owner_a)
    payload = {"to_number": "+15550002222"}
    request = {**headers, "Idempotency-Key": "replay-me-1234"}

    first = await client.post("/api/live-proof/runs", json=payload, headers=request)
    second = await client.post("/api/live-proof/runs", json=payload, headers=request)

    assert first.status_code == 201
    assert second.status_code == 200 or second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert len(dialled) == 1


async def test_reusing_a_key_for_a_different_request_conflicts(client, owner_a, harness_on, runner_override):
    headers = await _headers(client, owner_a)
    request = {**headers, "Idempotency-Key": "conflict-key-1234"}

    await client.post("/api/live-proof/runs", json={"note": "first"}, headers=request)
    conflicting = await client.post("/api/live-proof/runs", json={"note": "second"}, headers=request)

    assert conflicting.status_code == 409
    assert conflicting.json()["detail"]["code"] == "idempotency_conflict"


async def test_a_second_concurrent_run_is_refused(client, owner_a, harness_on, db, tenant_a, runner_override):
    db.add(
        LiveProofRun(
            id=uuid.uuid4(),
            tenant_id=tenant_a.id,
            status="running",
            requested_to_number="+15550002222",
        )
    )
    await db.commit()
    headers = await _headers(client, owner_a)

    response = await client.post(
        "/api/live-proof/runs",
        json={},
        headers={**headers, "Idempotency-Key": "second-run-1234"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "live_proof_already_running"


async def test_a_cap_above_the_configured_ceiling_is_refused(client, owner_a, harness_on, runner_override):
    headers = await _headers(client, owner_a)

    response = await client.post(
        "/api/live-proof/runs",
        json={"max_usd": 25.0},
        headers={**headers, "Idempotency-Key": "greedy-cap-1234"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "live_proof_cap_too_high"


async def test_a_bad_phone_number_is_refused_by_validation(client, owner_a, harness_on):
    headers = await _headers(client, owner_a)

    response = await client.post(
        "/api/live-proof/runs",
        json={"to_number": "call-me-maybe"},
        headers={**headers, "Idempotency-Key": "bad-number-1234"},
    )

    assert response.status_code == 422


async def test_runs_are_listed_and_read_back(client, owner_a, harness_on, db, tenant_a):
    row = LiveProofRun(
        id=uuid.uuid4(),
        tenant_id=tenant_a.id,
        status="succeeded",
        provider_set="twilio,deepgram,elevenlabs,openai",
        turns=3,
        e2e_p50_ms=800.0,
        cost_usd=0.01,
        provider_fakes=False,
        evidence_sha256="b" * 64,
    )
    db.add(row)
    await db.commit()
    headers = await _headers(client, owner_a)

    listed = await client.get("/api/live-proof/runs", headers=headers)
    single = await client.get(f"/api/live-proof/runs/{row.id}", headers=headers)

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["runs"][0]["id"] == str(row.id)
    assert single.status_code == 200
    assert single.json()["is_evidence"] is True
    assert single.json()["provider_set"] == ["twilio", "deepgram", "elevenlabs", "openai"]


async def test_another_tenants_run_is_a_plain_404(client, owner_b, harness_on, db, tenant_a):
    row = LiveProofRun(id=uuid.uuid4(), tenant_id=tenant_a.id, status="succeeded")
    db.add(row)
    await db.commit()
    headers = await _headers(client, owner_b)

    response = await client.get(f"/api/live-proof/runs/{row.id}", headers=headers)
    listed = await client.get("/api/live-proof/runs", headers=headers)

    assert response.status_code == 404
    assert listed.json()["total"] == 0


async def test_a_run_without_evidence_returns_404(client, owner_a, harness_on, db, tenant_a):
    row = LiveProofRun(id=uuid.uuid4(), tenant_id=tenant_a.id, status="failed", evidence_path="")
    db.add(row)
    await db.commit()
    headers = await _headers(client, owner_a)

    response = await client.get(f"/api/live-proof/runs/{row.id}/evidence", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "live_proof_evidence_missing"


async def test_evidence_is_returned_only_with_a_matching_hash(
    client, owner_a, harness_on, db, tenant_a, tmp_path
):
    harness_on.setattr(settings, "live_proof_evidence_dir", str(tmp_path))
    directory = tmp_path / "2026-10-24"
    directory.mkdir()
    evidence_file = directory / "run-1.json"
    payload = json.dumps({"run_id": "run-1", "provider_fakes": False}).encode("utf-8")
    evidence_file.write_bytes(payload)

    import hashlib

    row = LiveProofRun(
        id=uuid.uuid4(),
        tenant_id=tenant_a.id,
        status="succeeded",
        evidence_path=str(evidence_file),
        evidence_sha256=hashlib.sha256(payload).hexdigest(),
        provider_fakes=False,
    )
    db.add(row)
    await db.commit()
    headers = await _headers(client, owner_a)

    response = await client.get(f"/api/live-proof/runs/{row.id}/evidence", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["sha256_matches"] is True
    assert body["sha256_recorded"] == body["sha256_actual"]
    assert body["evidence"]["run_id"] == "run-1"


async def test_a_tampered_evidence_file_is_reported_as_not_matching(
    client, owner_a, harness_on, db, tenant_a, tmp_path
):
    harness_on.setattr(settings, "live_proof_evidence_dir", str(tmp_path))
    directory = tmp_path / "2026-10-24"
    directory.mkdir()
    evidence_file = directory / "run-2.json"
    evidence_file.write_bytes(b'{"run_id": "run-2", "provider_fakes": false}')
    row = LiveProofRun(
        id=uuid.uuid4(),
        tenant_id=tenant_a.id,
        status="succeeded",
        evidence_path=str(evidence_file),
        evidence_sha256="c" * 64,
        provider_fakes=False,
    )
    db.add(row)
    await db.commit()
    headers = await _headers(client, owner_a)

    body = (await client.get(f"/api/live-proof/runs/{row.id}/evidence", headers=headers)).json()

    assert body["sha256_matches"] is False


async def test_an_evidence_path_outside_the_root_is_refused(
    client, owner_a, harness_on, db, tenant_a, tmp_path
):
    harness_on.setattr(settings, "live_proof_evidence_dir", str(tmp_path / "evidence"))
    outside = tmp_path / "elsewhere.json"
    outside.write_bytes(b"{}")
    row = LiveProofRun(
        id=uuid.uuid4(),
        tenant_id=tenant_a.id,
        status="succeeded",
        evidence_path=str(outside),
        evidence_sha256="d" * 64,
    )
    db.add(row)
    await db.commit()
    headers = await _headers(client, owner_a)

    response = await client.get(f"/api/live-proof/runs/{row.id}/evidence", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "live_proof_evidence_outside_root"


async def test_a_deleted_evidence_file_reports_gone(client, owner_a, harness_on, db, tenant_a, tmp_path):
    harness_on.setattr(settings, "live_proof_evidence_dir", str(tmp_path))
    directory = tmp_path / "2026-10-24"
    directory.mkdir()
    missing = directory / "gone.json"
    row = LiveProofRun(
        id=uuid.uuid4(),
        tenant_id=tenant_a.id,
        status="succeeded",
        evidence_path=str(missing),
        evidence_sha256="e" * 64,
    )
    db.add(row)
    await db.commit()
    headers = await _headers(client, owner_a)

    response = await client.get(f"/api/live-proof/runs/{row.id}/evidence", headers=headers)

    assert response.status_code == 410


async def test_abort_marks_a_stuck_run_and_is_idempotent(client, owner_a, harness_on, db, tenant_a):
    row = LiveProofRun(id=uuid.uuid4(), tenant_id=tenant_a.id, status="running")
    db.add(row)
    await db.commit()
    headers = await _headers(client, owner_a)
    request = {**headers, "Idempotency-Key": "abort-key-1234"}

    first = await client.post(f"/api/live-proof/runs/{row.id}/abort", headers=request)
    second = await client.post(f"/api/live-proof/runs/{row.id}/abort", headers=request)

    assert first.status_code == 200, first.text
    assert first.json()["changed"] is True
    assert first.json()["run"]["status"] == "aborted"
    assert first.json()["run"]["is_evidence"] is False
    assert second.status_code in {200, 409}


async def test_abort_cannot_launder_a_failed_run_into_evidence(client, owner_a, harness_on, db, tenant_a):
    row = LiveProofRun(
        id=uuid.uuid4(),
        tenant_id=tenant_a.id,
        status="failed",
        provider_fakes=True,
        evidence_sha256="f" * 64,
    )
    db.add(row)
    await db.commit()
    headers = await _headers(client, owner_a)

    response = await client.post(
        f"/api/live-proof/runs/{row.id}/abort",
        headers={**headers, "Idempotency-Key": "abort-failed-1234"},
    )

    assert response.status_code == 200
    assert response.json()["changed"] is False
    assert response.json()["run"]["status"] == "failed"
    assert response.json()["run"]["is_evidence"] is False


async def test_aborting_another_tenants_run_is_404(client, owner_b, harness_on, db, tenant_a):
    row = LiveProofRun(id=uuid.uuid4(), tenant_id=tenant_a.id, status="running")
    db.add(row)
    await db.commit()
    headers = await _headers(client, owner_b)

    response = await client.post(
        f"/api/live-proof/runs/{row.id}/abort",
        headers={**headers, "Idempotency-Key": "cross-tenant-abort-1"},
    )

    assert response.status_code == 404


async def test_a_successful_run_writes_an_audit_row_in_the_same_transaction(
    client, owner_a, harness_on, runner_override, db, tenant_a
):
    """The proof run is a privileged action: it must be on the audit ledger."""
    from sqlalchemy import select

    from app.db.models import AuditLog

    headers = await _headers(client, owner_a)

    response = await client.post(
        "/api/live-proof/runs",
        json={"to_number": "+15550002222"},
        headers={**headers, "Idempotency-Key": "audited-run-1234"},
    )

    assert response.status_code == 201, response.text
    run_id = response.json()["id"]

    rows = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_a.id,
                AuditLog.resource_type == "live_proof_run",
                AuditLog.resource_id == run_id,
            )
        )
    ).scalars().all()

    # One row for the privileged start, one for the measured outcome: a run that
    # is started but never recorded as finished would be invisible in the ledger.
    assert {row.event_type for row in rows} == {"live_proof.run.start", "live_proof.run.finished"}
    assert all(row.result == "success" for row in rows)
    row = next(item for item in rows if item.event_type == "live_proof.run.start")
    assert row.result == "success"
    assert row.actor_type == "human"
    assert str(row.actor_user_id) == str(owner_a.id)
    # The ledger must not become a second copy of the credentials.
    serialized = json.dumps(row.detail or {}, default=str).lower()
    for secret in ("auth-token-0123456789", "dg-key-0123456789", "el-key-0123456789", "sk-key-0123456789"):
        assert secret not in serialized


async def test_an_abort_is_audited_too(client, owner_a, harness_on, db, tenant_a):
    from sqlalchemy import select

    from app.db.models import AuditLog

    row = LiveProofRun(id=uuid.uuid4(), tenant_id=tenant_a.id, status="running")
    db.add(row)
    await db.commit()
    headers = await _headers(client, owner_a)

    response = await client.post(
        f"/api/live-proof/runs/{row.id}/abort",
        headers={**headers, "Idempotency-Key": "audited-abort-1234"},
    )

    assert response.status_code == 200, response.text
    events = (
        await db.execute(
            select(AuditLog.event_type).where(
                AuditLog.tenant_id == tenant_a.id,
                AuditLog.resource_id == str(row.id),
            )
        )
    ).scalars().all()

    assert "live_proof.run.aborted" in events
