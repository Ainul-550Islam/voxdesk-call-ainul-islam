"""Legacy campaign API compatibility (Batch 06).

Every pre-existing URL, method, request shape and response field must keep
working for existing clients: /api/campaigns (+ state transitions, plan,
results, kpis, audience, eligibility) and the legacy
/api/tenants/{tenant_id}/campaigns(+/{campaign_id}/run) trio. The only
difference is additive: responses now also carry ``environment_id`` and the
endpoints resolve the effective environment (explicit → selected → default
production) exactly like the rest of the platform.
"""

from __future__ import annotations

import uuid
from datetime import time

from sqlalchemy import select

from app.db.models import Environment, UserRole
from tests.conftest import auth_headers, make_lead, make_user


# ----------------------------------------------------------------- helpers ---

async def _production(db, tenant) -> Environment:
    return (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()


async def _staging(db, tenant) -> Environment:
    environment = Environment(
        tenant_id=tenant.id, name="Staging", slug="staging", kind="staging",
        status="active", is_default=False,
    )
    db.add(environment)
    await db.commit()
    await db.refresh(environment)
    return environment


def _open_window(tenant) -> None:
    """Open the tenant's outbound window all day (tests only)."""
    tenant.outbound_window_open = time(0, 0)
    tenant.outbound_window_close = time(23, 59, 59)


LEGACY_CAMPAIGN_FIELDS = {
    "id", "tenant_id", "name", "goal", "channel", "state", "script_prompt",
    "opening_line", "lead_ids", "segment_ids", "daily_start", "daily_end",
    "days_of_week", "timezone", "calls_per_minute", "daily_limit",
    "max_attempts_per_lead",
}


# ------------------------------------------------- /api/campaigns (modern) ---

async def test_create_list_get_round_trip(client, db, tenant_a, manager_a):
    headers = await auth_headers(client, manager_a)
    production = await _production(db, tenant_a)

    created = await client.post(
        "/api/campaigns",
        json={"name": "Legacy shape", "goal": "remind", "calls_per_minute": 5},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    # every pre-existing field still present, plus the additive environment id
    assert LEGACY_CAMPAIGN_FIELDS <= set(body)
    assert body["environment_id"] == str(production.id)
    assert body["name"] == "Legacy shape"
    assert body["goal"] == "remind"
    assert body["channel"] == "voice"
    assert body["state"] == "draft"
    assert body["calls_per_minute"] == 5
    assert body["daily_start"] == "09:00" and body["daily_end"] == "20:00"
    assert body["days_of_week"] == [0, 1, 2, 3, 4, 5, 6]

    listed = await client.get("/api/campaigns", headers=headers)
    assert listed.status_code == 200
    assert [c["id"] for c in listed.json()] == [body["id"]]

    fetched = await client.get(f"/api/campaigns/{body['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Legacy shape"
    assert fetched.json()["environment_id"] == str(production.id)


async def test_patch_preserves_environment_and_id(client, db, tenant_a, manager_a):
    headers = await auth_headers(client, manager_a)
    created = await client.post(
        "/api/campaigns", json={"name": "Before"}, headers=headers
    )
    campaign_id = created.json()["id"]
    environment_id = created.json()["environment_id"]

    patched = await client.patch(
        f"/api/campaigns/{campaign_id}", json={"name": "After"}, headers=headers
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["id"] == campaign_id
    assert patched.json()["name"] == "After"
    assert patched.json()["environment_id"] == environment_id


async def test_state_machine_endpoints_keep_working(client, db, tenant_a, manager_a):
    headers = await auth_headers(client, manager_a)
    _open_window(tenant_a)
    await db.commit()

    created = await client.post(
        "/api/campaigns", json={"name": "Stateful"}, headers=headers
    )
    campaign_id = created.json()["id"]

    scheduled = await client.post(
        f"/api/campaigns/{campaign_id}/schedule", headers=headers
    )
    assert scheduled.status_code == 200 and scheduled.json()["state"] == "scheduled"

    # the stored machine: draft→scheduled→running→paused→running→cancelled
    resumed = await client.post(f"/api/campaigns/{campaign_id}/resume", headers=headers)
    assert resumed.status_code == 200 and resumed.json()["state"] == "running"

    paused = await client.post(f"/api/campaigns/{campaign_id}/pause", headers=headers)
    assert paused.status_code == 200 and paused.json()["state"] == "paused"

    resumed_again = await client.post(
        f"/api/campaigns/{campaign_id}/resume", headers=headers
    )
    assert resumed_again.status_code == 200 and resumed_again.json()["state"] == "running"

    cancelled = await client.post(
        f"/api/campaigns/{campaign_id}/cancel", headers=headers
    )
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"

    # pause from scheduled (not running) stays illegal, as before Batch 06
    other = await client.post(
        "/api/campaigns", json={"name": "Stateful 2"}, headers=headers
    )
    other_id = other.json()["id"]
    await client.post(f"/api/campaigns/{other_id}/schedule", headers=headers)
    illegal = await client.post(f"/api/campaigns/{other_id}/pause", headers=headers)
    assert illegal.status_code == 422


async def test_plan_results_kpis_audience_eligibility_keep_working(
    client, db, tenant_a, manager_a
):
    headers = await auth_headers(client, manager_a)
    _open_window(tenant_a)  # the plan gate uses the tenant's outbound window
    await db.commit()
    lead = await make_lead(db, tenant_a, phone="+15550200001")
    created = await client.post(
        "/api/campaigns",
        json={"name": "Plannable", "lead_ids": [str(lead.id)]},
        headers=headers,
    )
    campaign_id = created.json()["id"]

    plan = await client.post(f"/api/campaigns/{campaign_id}/plan", headers=headers)
    assert plan.status_code == 200
    intents = plan.json()
    assert [i["lead_id"] for i in intents] == [str(lead.id)]
    assert all(i["skipped"] is False for i in intents)
    assert {"id", "campaign_id", "lead_id", "channel", "skipped", "reason_skipped"} <= set(intents[0])

    eligibility = await client.get(
        f"/api/campaigns/{campaign_id}/eligibility", headers=headers
    )
    assert eligibility.status_code == 200
    assert eligibility.json()["eligible"] == 1

    audience = await client.get(f"/api/campaigns/{campaign_id}/audience", headers=headers)
    assert audience.status_code == 200
    assert audience.json()["lead_count"] == 1

    results = await client.get(f"/api/campaigns/{campaign_id}/results", headers=headers)
    assert results.status_code == 200
    assert "metrics" in results.json() and "eligibility" in results.json()

    kpis = await client.get(f"/api/campaigns/{campaign_id}/kpis", headers=headers)
    assert kpis.status_code == 200
    assert isinstance(kpis.json(), dict)

    status = await client.get(
        f"/api/campaigns/{campaign_id}/execution-status", headers=headers
    )
    assert status.status_code == 200
    assert status.json()["id"] == campaign_id


async def test_get_by_id_from_a_different_environment_is_404(
    client, db, tenant_a, manager_a
):
    headers = await auth_headers(client, manager_a)
    staging = await _staging(db, tenant_a)
    created = await client.post(
        "/api/campaigns",
        json={"name": "Staging only", "environment_id": str(staging.id)},
        headers=headers,
    )
    assert created.status_code == 201
    campaign_id = created.json()["id"]
    assert created.json()["environment_id"] == str(staging.id)

    # default (production) context → invisible, safe not-found
    missing = await client.get(f"/api/campaigns/{campaign_id}", headers=headers)
    assert missing.status_code == 404
    # explicit staging context → visible
    found = await client.get(
        f"/api/campaigns/{campaign_id}",
        params={"environment_id": str(staging.id)},
        headers=headers,
    )
    assert found.status_code == 200 and found.json()["name"] == "Staging only"


# ------------------------------------------- /api/tenants/... (legacy URLs) ---

async def test_legacy_create_and_list_urls_still_work(client, db, tenant_a, manager_a):
    headers = await auth_headers(client, manager_a)
    production = await _production(db, tenant_a)
    url = f"/api/tenants/{tenant_a.id}/campaigns"

    created = await client.post(url, json={"name": "Legacy campaign"}, headers=headers)
    assert created.status_code == 201, created.text
    body = created.json()
    # pre-existing response fields unchanged; environment_id is additive
    assert {"id", "name", "is_active"} <= set(body)
    assert body["is_active"] is False
    assert body["environment_id"] == str(production.id)

    listed = await client.get(url, headers=headers)
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert {"id", "name", "goal", "is_active", "calls_per_minute",
            "leads_total", "leads_done"} <= set(rows[0])
    assert rows[0]["environment_id"] == str(production.id)
    assert rows[0]["leads_total"] == 0 and rows[0]["leads_done"] == 0


async def test_legacy_list_counts_only_same_environment_leads(
    client, db, tenant_a, manager_a
):
    headers = await auth_headers(client, manager_a)
    staging = await _staging(db, tenant_a)
    campaign = await client.post(
        f"/api/tenants/{tenant_a.id}/campaigns", json={"name": "Counter"}, headers=headers
    )
    campaign_id = campaign.json()["id"]

    await make_lead(
        db, tenant_a, phone="+15550200002", campaign_id=uuid.UUID(campaign_id)
    )
    await make_lead(
        db, tenant_a, phone="+15550200003", environment_id=staging.id,
        campaign_id=uuid.UUID(campaign_id),
    )

    rows = (await client.get(
        f"/api/tenants/{tenant_a.id}/campaigns", headers=headers
    )).json()
    assert rows[0]["leads_total"] == 1  # staging lead must not inflate production


async def test_legacy_run_url_still_works(client, db, tenant_a, manager_a):
    headers = await auth_headers(client, manager_a)
    created = await client.post(
        f"/api/tenants/{tenant_a.id}/campaigns", json={"name": "Runner"}, headers=headers
    )
    campaign_id = created.json()["id"]
    run_url = f"/api/tenants/{tenant_a.id}/campaigns/{campaign_id}/run"

    # default dry_run=true, inactive campaign → the documented legacy response
    response = await client.post(run_url, headers=headers)
    assert response.status_code == 200
    assert response.json() == {"dialed": 0, "reason": "campaign_inactive"}


async def test_legacy_run_dry_run_on_active_campaign(client, db, tenant_a, manager_a):
    headers = await auth_headers(client, manager_a)
    _open_window(tenant_a)
    tenant_a.outbound_enabled = True
    await db.commit()
    created = await client.post(
        f"/api/tenants/{tenant_a.id}/campaigns",
        json={"name": "Live", "is_active": True},
        headers=headers,
    )
    campaign_id = created.json()["id"]
    await make_lead(db, tenant_a, phone="+15550200004", campaign_id=uuid.UUID(campaign_id))

    response = await client.post(
        f"/api/tenants/{tenant_a.id}/campaigns/{campaign_id}/run", headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["dialed"] == 1
    assert body["results"][0]["ok"] is True and body["results"][0]["dry_run"] is True


async def test_legacy_run_refuses_a_campaign_from_another_environment(
    client, db, tenant_a, manager_a
):
    headers = await auth_headers(client, manager_a)
    staging = await _staging(db, tenant_a)
    created = await client.post(
        f"/api/tenants/{tenant_a.id}/campaigns",
        json={"name": "Staging runner", "environment_id": str(staging.id)},
        headers=headers,
    )
    campaign_id = created.json()["id"]

    # default context is production → campaign invisible → 404 (fail closed)
    response = await client.post(
        f"/api/tenants/{tenant_a.id}/campaigns/{campaign_id}/run", headers=headers
    )
    assert response.status_code == 404

    # explicit staging context → works
    ok = await client.post(
        f"/api/tenants/{tenant_a.id}/campaigns/{campaign_id}/run",
        params={"environment_id": str(staging.id)},
        headers=headers,
    )
    assert ok.status_code == 200
    assert ok.json() == {"dialed": 0, "reason": "campaign_inactive"}


# ------------------------------------------------------------ cross routes ---

async def test_campaigns_created_via_legacy_route_are_visible_in_the_modern_api(
    client, db, tenant_a, manager_a
):
    headers = await auth_headers(client, manager_a)
    created = await client.post(
        f"/api/tenants/{tenant_a.id}/campaigns", json={"name": "Bridged"}, headers=headers
    )
    campaign_id = created.json()["id"]

    modern = await client.get(f"/api/campaigns/{campaign_id}", headers=headers)
    assert modern.status_code == 200
    assert modern.json()["name"] == "Bridged"
    assert modern.json()["state"] == "draft"

    listed = await client.get("/api/campaigns", headers=headers)
    assert campaign_id in [c["id"] for c in listed.json()]


async def test_campaigns_created_via_modern_api_are_visible_in_legacy_list(
    client, db, tenant_a, manager_a
):
    headers = await auth_headers(client, manager_a)
    created = await client.post(
        "/api/campaigns", json={"name": "Bridged back"}, headers=headers
    )
    campaign_id = created.json()["id"]

    rows = (await client.get(
        f"/api/tenants/{tenant_a.id}/campaigns", headers=headers
    )).json()
    assert campaign_id in [r["id"] for r in rows]


async def test_other_tenants_campaigns_never_leak(client, db, tenant_a, tenant_b, manager_a):
    headers = await auth_headers(client, manager_a)
    await client.post("/api/campaigns", json={"name": "Tenant A only"}, headers=headers)

    other_user = await make_user(db, tenant_b, UserRole.MANAGER)
    other = await auth_headers(client, other_user)
    rows = (await client.get("/api/campaigns", headers=other)).json()
    assert rows == []
    listed = (await client.get(
        f"/api/tenants/{tenant_a.id}/campaigns", headers=other
    ))
    assert listed.status_code == 404  # scoped_permission hides the other tenant
