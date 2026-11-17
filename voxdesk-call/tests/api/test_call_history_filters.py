# File: tests/api/test_call_history_filters.py — the new call-history filters, saved views, signed recordings
"""Call history tests (PART 15, P15-07).

These filters are the difference between a call list and a call *search*. Each
test seeds two calls that differ in exactly one dimension, so a filter that
matches everything (or nothing) fails instead of passing by accident.

The signed-recording tests are the security-relevant half: an export must hand
out a URL that expires and that the platform signed, never the provider's raw
URL, and a tampered signature must be refused.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

import app.db.models  # noqa: F401
from app.db.enterprise_models import AnalysisResult, AnalysisSchema
from app.db.models import Call, Environment, Tenant, UserRole
from tests.conftest import auth_headers, make_call, make_tenant, make_user

SEARCH = "/api/calls/search"
#: `calls.agent_id` is a UUID column, so fixtures use real ids.
AGENT_VIP = uuid.uuid4()
AGENT_PLAIN = uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _environment(db, tenant: Tenant, *, name: str, slug: str, kind: str) -> Environment:
    """A tenant has one environment per kind, so production is reused, not recreated."""
    from sqlalchemy import select as _select

    existing = await db.scalar(
        _select(Environment).where(
            Environment.tenant_id == tenant.id, Environment.kind == kind
        )
    )
    if existing is not None:
        return existing
    row = Environment(
        tenant_id=tenant.id,
        name=name,
        slug=slug,
        kind=kind,
        status="active",
        is_default=False,
        release_version=1,
        deployment_status="deployed",
        deployment_source="test",
        health_state="healthy",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def _analysis(db, tenant: Tenant, call: Call, result: dict) -> AnalysisResult:
    schema = AnalysisSchema(
        tenant_id=tenant.id,
        environment_id=call.environment_id,
        name="Booking",
        fields=[],
        is_active=True,
    )
    db.add(schema)
    await db.commit()
    await db.refresh(schema)
    row = AnalysisResult(
        tenant_id=tenant.id,
        call_id=call.id,
        schema_id=schema.id,
        environment_id=call.environment_id,
        schema_version=1,
        schema_snapshot={"fields": []},
        provenance="verified",
        result=result,
        status="completed",
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@pytest_asyncio.fixture
async def seeded(db, tenant_a):
    """Two calls that differ in one dimension each time."""
    prod = await _environment(db, tenant_a, name="Production", slug="prod", kind="production")
    staging = await _environment(db, tenant_a, name="Staging", slug="staging", kind="staging")

    vip = await make_call(
        db,
        tenant_a,
        agent_id=AGENT_VIP,
        started_at=_now() - timedelta(hours=2),
        environment_id=prod.id,
        agent_version_id=uuid.uuid4(),
        transfer_destination="sales-team",
        transfer_context={"dynamic_variables": {"customer_tier": "vip"}, "facts": {"plan": "pro"}},
        recording_url="https://recordings.example/vip.mp3",
    )
    plain = await make_call(
        db,
        tenant_a,
        agent_id=AGENT_PLAIN,
        started_at=_now() - timedelta(hours=1),
        environment_id=staging.id,
        agent_version_id=uuid.uuid4(),
        transfer_destination="support-team",
        transfer_context={"dynamic_variables": {"customer_tier": "basic"}},
    )
    return {"vip": vip, "plain": plain, "prod": prod, "staging": staging}


async def _ids(client, headers, **filters) -> list[str]:
    response = await client.post(SEARCH, headers=headers, json=filters)
    assert response.status_code == 200, response.text
    return [row["id"] for row in response.json()["calls"]]


@pytest_asyncio.fixture
async def owner_headers(client, db, tenant_a):
    owner = await make_user(db, tenant_a, UserRole.OWNER)
    return await auth_headers(client, owner)


# -------------------------------------------------------------------- filters ---


@pytest.mark.asyncio
async def test_agent_filter_matches_the_column(client, seeded, owner_headers):
    """The agent filter used to require a substring hit on call_sid; it now works."""
    assert await _ids(client, owner_headers, agent_id=str(AGENT_VIP)) == [str(seeded["vip"].id)]
    assert await _ids(client, owner_headers, agent_id="agent-nobody") == []


@pytest.mark.asyncio
async def test_dynamic_variable_filter(client, seeded, owner_headers):
    # Newest first: the plain call is one hour old, the VIP call two.
    assert await _ids(client, owner_headers, dynamic_variable="customer_tier") == [
        str(seeded["plain"].id),
        str(seeded["vip"].id),
    ]
    assert await _ids(
        client, owner_headers, dynamic_variable="customer_tier", dynamic_variable_value="vip"
    ) == [str(seeded["vip"].id)]
    # The value check is exact: "vip" must not match "vip_ish".
    assert await _ids(
        client, owner_headers, dynamic_variable="customer_tier", dynamic_variable_value="vi"
    ) == []


@pytest.mark.asyncio
async def test_analysis_value_and_presence(client, db, seeded, owner_headers):
    await _analysis(
        db, await db.get(Tenant, seeded["vip"].tenant_id), seeded["vip"], {"outcome": "refund_requested"}
    )

    assert await _ids(client, owner_headers, analysis_field="outcome", analysis_value="refund_requested") == [
        str(seeded["vip"].id)
    ]
    assert await _ids(client, owner_headers, analysis="exists") == [str(seeded["vip"].id)]
    assert await _ids(client, owner_headers, analysis="not_exists") == [str(seeded["plain"].id)]


@pytest.mark.asyncio
async def test_transfer_agent_filter(client, seeded, owner_headers):
    assert await _ids(client, owner_headers, transfer_agent="sales") == [str(seeded["vip"].id)]
    assert await _ids(client, owner_headers, transfer_agent="support") == [str(seeded["plain"].id)]


@pytest.mark.asyncio
async def test_environment_tag_filter(client, seeded, owner_headers):
    prod = seeded["prod"]
    # Both the slug and the display name resolve to the same environment.
    assert await _ids(client, owner_headers, environment_tag=prod.slug) == [str(seeded["vip"].id)]
    assert await _ids(client, owner_headers, environment_tag=prod.name) == [str(seeded["vip"].id)]
    assert await _ids(client, owner_headers, environment_tag=seeded["staging"].slug) == [
        str(seeded["plain"].id)
    ]
    assert await _ids(client, owner_headers, environment_tag="no-such-env") == []


@pytest.mark.asyncio
async def test_pinned_version_filter(client, seeded, owner_headers):
    version_id = seeded["plain"].agent_version_id
    assert await _ids(client, owner_headers, agent_version_id=str(version_id)) == [
        str(seeded["plain"].id)
    ]


@pytest.mark.asyncio
async def test_custom_columns_in_the_response(client, seeded, owner_headers):
    response = await client.post(
        SEARCH, headers=owner_headers, json={"agent_id": str(AGENT_VIP)}
    )
    row = response.json()["calls"][0]
    assert row["transfer_agent"] == "sales-team"
    assert row["agent_version_id"] == str(seeded["vip"].agent_version_id)
    assert row["environment_id"] == str(seeded["prod"].id)


@pytest.mark.asyncio
async def test_tenant_isolation(client, db, seeded, owner_headers):
    other = await make_tenant(db, "History B")
    other_owner = await make_user(db, other, UserRole.OWNER)
    other_headers = await auth_headers(client, other_owner)
    response = await client.post(SEARCH, headers=other_headers, json={})
    assert response.status_code == 200
    assert response.json()["calls"] == []
    assert response.json()["total"] == 0


# -------------------------------------------------------------- signed URLs ---


@pytest.mark.asyncio
async def test_export_signs_the_recording_url(client, seeded, owner_headers):
    response = await client.post(
        "/api/calls/export",
        headers=owner_headers,
        json={
            "format": "json",
            "fields": ["id", "recording_url"],
            "filters": {"agent_id": str(AGENT_VIP)},
            "sign_recordings": True,
            "redact_pii": False,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    row = payload["data"][0]
    assert row["recording_url"].startswith("/api/calls/")
    assert "signature=" in row["recording_url"]
    # The provider's URL never leaves the server.
    assert "recordings.example" not in str(payload)


@pytest.mark.asyncio
async def test_signed_url_redirects_and_rejects_a_bad_signature(client, seeded, owner_headers):
    response = await client.post(
        "/api/calls/export",
        headers=owner_headers,
        json={
            "format": "json",
            "fields": ["id", "recording_url"],
            "filters": {"agent_id": str(AGENT_VIP)},
            "sign_recordings": True,
            "redact_pii": False,
        },
    )
    path = response.json()["data"][0]["recording_url"]

    ok = await client.get(path, follow_redirects=False)
    assert ok.status_code == 302
    assert ok.headers["location"] == "https://recordings.example/vip.mp3"

    tampered = path.replace("signature=", "signature=0")
    bad = await client.get(tampered, follow_redirects=False)
    assert bad.status_code == 403


@pytest.mark.asyncio
async def test_expired_signature_is_refused(client, seeded, owner_headers):
    from app.api.call_search_export_routes import sign_recording_url

    call_id = seeded["vip"].id
    path = sign_recording_url(call_id, ttl_seconds=60)
    # Rewrite the expiry into the past while keeping the same signature: the
    # signature no longer covers the new expiry, so this must fail twice over.
    from app.api.call_search_export_routes import _recording_signature

    past = 1
    forged = f"/api/calls/{call_id}/recording-access?expires={past}&signature={_recording_signature(call_id, past)}"
    response = await client.get(forged, follow_redirects=False)
    assert response.status_code == 403
    assert path.startswith("/api/calls/")


# -------------------------------------------------------------- saved views ---


@pytest.mark.asyncio
async def test_saved_view_round_trip(client, seeded, owner_headers):
    created = await client.post(
        "/api/calls/saved-views",
        headers=owner_headers,
        json={
            "name": "VIP transfers",
            "filters": {"dynamic_variable": "customer_tier", "dynamic_variable_value": "vip"},
            "columns": ["id", "transfer_agent"],
            "shared": True,
        },
    )
    assert created.status_code == 201, created.text
    view_id = created.json()["id"]

    listed = await client.get("/api/calls/saved-views", headers=owner_headers)
    assert [row["name"] for row in listed.json()["views"]] == ["VIP transfers"]

    # A view whose filters the search endpoint would reject cannot be stored.
    invalid = await client.post(
        "/api/calls/saved-views",
        headers=owner_headers,
        json={"name": "Broken", "filters": {"not_a_filter": 1}},
    )
    assert invalid.status_code == 422

    # Unknown column names are rejected rather than silently dropped.
    bad_column = await client.post(
        "/api/calls/saved-views",
        headers=owner_headers,
        json={"name": "Broken 2", "filters": {}, "columns": ["secret_field"]},
    )
    assert bad_column.status_code == 422

    removed = await client.delete(f"/api/calls/saved-views/{view_id}", headers=owner_headers)
    assert removed.status_code == 204
    assert (await client.get("/api/calls/saved-views", headers=owner_headers)).json()["views"] == []


@pytest.mark.asyncio
async def test_saved_view_is_tenant_scoped(client, db, seeded, owner_headers):
    other = await make_tenant(db, "Views B")
    other_owner = await make_user(db, other, UserRole.OWNER)
    other_headers = await auth_headers(client, other_owner)
    created = await client.post(
        "/api/calls/saved-views",
        headers=owner_headers,
        json={"name": "Mine", "filters": {}, "shared": True},
    )
    assert created.status_code == 201
    assert (await client.get("/api/calls/saved-views", headers=other_headers)).json()["views"] == []
