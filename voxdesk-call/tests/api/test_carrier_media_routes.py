"""The Batch 02 admin surface: carrier capabilities, connection checks and
the speech-to-speech session list.

The tests use the real application, the real authorization layer and the
real database. The carrier probes are not configured in the test
environment, so no test contacts a carrier.
"""

from __future__ import annotations

import uuid

import pytest_asyncio

import app.db.carrier_s2s_models  # noqa: F401 - registers the tables
from app.db.carrier_s2s_models import CarrierConnectionCheck, S2SSessionRecord


@pytest_asyncio.fixture
async def s2s_session(db, tenant_a):
    row = S2SSessionRecord(
        id=uuid.uuid4(),
        tenant_id=tenant_a.id,
        provider="openai_realtime",
        model="gpt-4o-realtime-preview",
        status="completed",
        input_tokens=120,
        output_tokens=80,
        cost_micro_usd=250,
        cost_known=True,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@pytest_asyncio.fixture
async def failed_s2s_session(db, tenant_a):
    row = S2SSessionRecord(
        id=uuid.uuid4(),
        tenant_id=tenant_a.id,
        provider="gemini_live",
        model="gemini-2.0-flash-exp",
        status="failed_over",
        failover_reason="provider_error",
        error="the provider dropped the socket",
        input_tokens=10,
        output_tokens=0,
        cost_micro_usd=0,
        cost_known=False,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def test_capabilities_require_authentication(client):
    response = await client.get("/api/v1/carriers/capabilities")
    assert response.status_code in (401, 403)


async def test_capabilities_report_every_carrier(client, owner_a):
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    response = await client.get("/api/v1/carriers/capabilities", headers=headers)
    assert response.status_code == 200
    carriers = {row["carrier"] for row in response.json()["carriers"]}
    assert carriers == {"twilio", "telnyx", "vonage", "sip", "s2s"}
    for row in response.json()["carriers"]:
        assert row["status"] in {"ok", "not_configured", "failed"}
        assert row["reason"]


async def test_capabilities_are_not_readable_by_a_viewer(client, viewer_a):
    from tests.conftest import auth_headers

    headers = await auth_headers(client, viewer_a)
    response = await client.get("/api/v1/carriers/capabilities", headers=headers)
    assert response.status_code == 403


async def test_a_connection_check_requires_an_idempotency_key(client, owner_a):
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    response = await client.post("/api/v1/carriers/vonage/test-connection", headers=headers)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "idempotency_key_required"


async def test_an_unknown_carrier_is_a_404(client, owner_a):
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    headers["Idempotency-Key"] = "check-unknown-carrier"
    response = await client.post("/api/v1/carriers/nope/test-connection", headers=headers)
    assert response.status_code == 404


async def test_a_connection_check_is_not_permitted_for_a_viewer(client, viewer_a):
    from tests.conftest import auth_headers

    headers = await auth_headers(client, viewer_a)
    headers["Idempotency-Key"] = "check-viewer-denied"
    response = await client.post("/api/v1/carriers/vonage/test-connection", headers=headers)
    assert response.status_code == 403


async def test_a_connection_check_reports_the_real_state(client, owner_a):
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    headers["Idempotency-Key"] = "check-vonage-not-configured"
    response = await client.post("/api/v1/carriers/vonage/test-connection", headers=headers)
    assert response.status_code == 200
    body = response.json()
    # Nothing is configured in the test environment, and the route says so.
    assert body["status"] == "not_configured"
    assert body["reason"] == "vonage_media_credentials_missing"


async def test_a_connection_check_is_idempotent(client, owner_a, db):
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    headers["Idempotency-Key"] = "check-idempotent"
    first = await client.post("/api/v1/carriers/sip/test-connection", headers=headers)
    second = await client.post("/api/v1/carriers/sip/test-connection", headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    rows = (
        await db.execute(
            select_checks(owner_a.tenant_id, "sip")
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_a_connection_check_is_audited(client, owner_a, db):
    from sqlalchemy import select

    from app.db.models import AuditLog
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    headers["Idempotency-Key"] = "check-audited"
    await client.post("/api/v1/carriers/vonage/test-connection", headers=headers)
    rows = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == owner_a.tenant_id,
                AuditLog.event_type == "carrier.test_connection",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].resource_type == "carrier_connection_check"


async def test_s2s_sessions_are_listed_for_the_tenant(client, owner_a, s2s_session):
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    response = await client.get("/api/v1/s2s/sessions", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["sessions"][0]["id"] == str(s2s_session.id)
    assert body["sessions"][0]["cost_micro_usd"] == 250


async def test_s2s_sessions_are_isolated_between_tenants(client, owner_b, s2s_session):
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_b)
    response = await client.get("/api/v1/s2s/sessions", headers=headers)
    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_a_foreign_session_is_not_readable(client, owner_b, s2s_session):
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_b)
    response = await client.get(f"/api/v1/s2s/sessions/{s2s_session.id}", headers=headers)
    assert response.status_code == 404


async def test_one_session_is_returned(client, owner_a, s2s_session):
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    response = await client.get(f"/api/v1/s2s/sessions/{s2s_session.id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["provider"] == "openai_realtime"


async def test_a_connection_check_reports_whether_the_carrier_answered(
    client, owner_a
):
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    headers["Idempotency-Key"] = "check-ok-flag"
    response = await client.post("/api/v1/carriers/vonage/test-connection", headers=headers)
    assert response.status_code == 200
    body = response.json()
    # Nothing is configured, so the check did not succeed.
    assert body["ok"] is False
    assert body["status"] == "not_configured"


async def test_a_failed_session_reports_its_reason_and_error(
    client, owner_a, failed_s2s_session
):
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    response = await client.get(
        f"/api/v1/s2s/sessions/{failed_s2s_session.id}", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed_over"
    assert body["failover_reason"] == "provider_error"
    assert body["error"] == "the provider dropped the socket"


async def test_an_unknown_session_is_a_404(client, owner_a):
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    response = await client.get(f"/api/v1/s2s/sessions/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


def select_checks(tenant_id, carrier):
    from sqlalchemy import select

    return select(CarrierConnectionCheck).where(
        CarrierConnectionCheck.tenant_id == tenant_id,
        CarrierConnectionCheck.carrier == carrier,
    )


async def test_s2s_capability_tracks_enable_s2s_and_provider_credentials(
    client, owner_a, monkeypatch
):
    from app.carriers_v2.settings import get_carrier_v2_settings
    from app.core.config import settings as core_settings
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)

    monkeypatch.setenv("ENABLE_S2S", "false")
    monkeypatch.setattr(core_settings, "openai_api_key", "")
    get_carrier_v2_settings.cache_clear()
    try:
        off_resp = await client.get("/api/v1/carriers/capabilities", headers=headers)
        assert off_resp.status_code == 200
        s2s_off = next(r for r in off_resp.json()["carriers"] if r["carrier"] == "s2s")
        assert s2s_off["status"] == "not_configured"

        monkeypatch.setattr(core_settings, "openai_api_key", "sk-live-test-key-998877")
        monkeypatch.setenv("ENABLE_S2S", "true")
        get_carrier_v2_settings.cache_clear()
        on_resp = await client.get("/api/v1/carriers/capabilities", headers=headers)
        assert on_resp.status_code == 200
        s2s_on = next(r for r in on_resp.json()["carriers"] if r["carrier"] == "s2s")
        assert s2s_on["status"] == "ok"
    finally:
        get_carrier_v2_settings.cache_clear()

