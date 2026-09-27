"""Legacy lead + campaign route compatibility (Batch 06).

The dashboard's original URLs keep working with their original request and
response shapes:

* POST /api/tenants/{tid}/leads            → {"created", "skipped"}
* GET  /api/tenants/{tid}/leads            → row list
* POST /api/tenants/{tid}/leads/{lid}/do-not-call
* POST /api/tenants/{tid}/campaigns        → {"id","name","is_active",...}
* GET  /api/tenants/{tid}/campaigns
* POST /api/tenants/{tid}/campaigns/{cid}/run

Batch 06 changes are additive and fail-closed: created leads always carry
the resolved environment (legacy rows without one can no longer be created),
lists are environment-scoped, the DNC route goes through the canonical
consent/lifecycle path, and a client-supplied environment can never cross
the tenant boundary.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models import Campaign, Environment, Lead, LeadStatus, UserRole
from app.leads.activities import history_for
from app.leads.models import LeadConsent
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


def _bulk(*phones, **extra):
    body = {"leads": [{"phone": p, "name": f"Lead {p[-4:]}"} for p in phones]}
    body.update(extra)
    return body


# ------------------------------------------------------------- bulk import ---

async def test_bulk_import_url_and_response_shape_unchanged(
    client, db, tenant_a, manager_a
):
    headers = await auth_headers(client, manager_a)
    production = await _production(db, tenant_a)

    response = await client.post(
        f"/api/tenants/{tenant_a.id}/leads",
        json=_bulk("+15550900001", "+15550900002"),
        headers=headers,
    )
    assert response.status_code == 201, response.text
    assert response.json() == {"created": 2, "skipped": 0}

    rows = (
        await db.execute(
            select(Lead).where(Lead.tenant_id == tenant_a.id)
        )
    ).scalars().all()
    assert len(rows) == 2
    # the Batch-06 core gap: a legacy bulk lead without environment_id can
    # no longer exist — every row is bound to the resolved environment
    assert all(row.environment_id == production.id for row in rows)
    assert all(row.status is LeadStatus.NEW for row in rows)
    # canonical creation history exists for each
    for row in rows:
        history = await history_for(db, row.tenant_id, row.environment_id, row.id)
        assert [(h.from_status, h.to_status) for h in history] == [(None, "new")]
        assert history[0].source == "legacy_bulk_import"


async def test_bulk_import_skips_duplicates_within_the_environment(
    client, db, tenant_a, manager_a
):
    headers = await auth_headers(client, manager_a)
    url = f"/api/tenants/{tenant_a.id}/leads"
    first = await client.post(url, json=_bulk("+15550900003"), headers=headers)
    assert first.json() == {"created": 1, "skipped": 0}
    second = await client.post(url, json=_bulk("+15550900003"), headers=headers)
    assert second.json() == {"created": 0, "skipped": 1}


async def test_same_phone_may_exist_in_two_environments(client, db, tenant_a, manager_a):
    """The duplicate check is environment-scoped: a staging test lead and a
    production lead may share a phone without either write touching the
    other."""
    headers = await auth_headers(client, manager_a)
    staging = await _staging(db, tenant_a)
    url = f"/api/tenants/{tenant_a.id}/leads"

    prod = await client.post(url, json=_bulk("+15550900004"), headers=headers)
    assert prod.json() == {"created": 1, "skipped": 0}
    stage = await client.post(
        url,
        json=_bulk("+15550900004", environment_id=str(staging.id)),
        headers=headers,
    )
    assert stage.json() == {"created": 1, "skipped": 0}

    rows = (
        await db.execute(
            select(Lead).where(
                Lead.tenant_id == tenant_a.id, Lead.phone == "+15550900004"
            )
        )
    ).scalars().all()
    assert len(rows) == 2
    assert {row.environment_id for row in rows} == {
        staging.id, (await _production(db, tenant_a)).id
    }


async def test_bulk_import_refuses_a_cross_environment_campaign(
    client, db, tenant_a, manager_a
):
    headers = await auth_headers(client, manager_a)
    staging = await _staging(db, tenant_a)
    staging_campaign = Campaign(
        tenant_id=tenant_a.id, environment_id=staging.id, name="Staging push"
    )
    db.add(staging_campaign)
    await db.commit()

    # default (production) context + staging campaign → 404, nothing created
    response = await client.post(
        f"/api/tenants/{tenant_a.id}/leads",
        json=_bulk("+15550900005", campaign_id=str(staging_campaign.id)),
        headers=headers,
    )
    assert response.status_code == 404
    rows = (
        await db.execute(select(Lead).where(Lead.phone == "+15550900005"))
    ).scalars().all()
    assert rows == []

    # matching context → accepted and attached
    ok = await client.post(
        f"/api/tenants/{tenant_a.id}/leads",
        json=_bulk("+15550900006", campaign_id=str(staging_campaign.id),
                   environment_id=str(staging.id)),
        headers=headers,
    )
    assert ok.status_code == 201 and ok.json() == {"created": 1, "skipped": 0}
    lead = (
        await db.execute(select(Lead).where(Lead.phone == "+15550900006"))
    ).scalar_one()
    assert lead.campaign_id == staging_campaign.id
    assert lead.environment_id == staging.id


async def test_bulk_import_refuses_a_foreign_environment(client, db, tenant_a, tenant_b,
                                                         manager_a):
    headers = await auth_headers(client, manager_a)
    foreign = await _production(db, tenant_b)
    response = await client.post(
        f"/api/tenants/{tenant_a.id}/leads",
        json=_bulk("+15550900007", environment_id=str(foreign.id)),
        headers=headers,
    )
    assert response.status_code == 404
    rows = (
        await db.execute(select(Lead).where(Lead.phone == "+15550900007"))
    ).scalars().all()
    assert rows == []


# ------------------------------------------------------------------- list ---

async def test_list_url_shape_and_environment_scope(client, db, tenant_a, manager_a):
    headers = await auth_headers(client, manager_a)
    staging = await _staging(db, tenant_a)
    production_lead = await make_lead(db, tenant_a, phone="+15550900008")
    staging_lead = await make_lead(
        db, tenant_a, phone="+15550900009", environment_id=staging.id
    )

    rows = (await client.get(
        f"/api/tenants/{tenant_a.id}/leads", headers=headers
    )).json()
    ids = [row["id"] for row in rows]
    assert str(production_lead.id) in ids
    assert str(staging_lead.id) not in ids  # another environment never leaks
    sample = next(row for row in rows if row["id"] == str(production_lead.id))
    assert {"id", "name", "phone", "email", "company", "status"} <= set(sample)
    assert sample["environment_id"] == str(production_lead.environment_id)

    staged = (await client.get(
        f"/api/tenants/{tenant_a.id}/leads",
        params={"environment_id": str(staging.id)},
        headers=headers,
    )).json()
    assert [row["id"] for row in staged] == [str(staging_lead.id)]


# -------------------------------------------------------------------- dnc ---

async def test_do_not_call_route_goes_through_consent_and_history(
    client, db, tenant_a, manager_a
):
    headers = await auth_headers(client, manager_a)
    lead = await make_lead(db, tenant_a, phone="+15550900010")
    url = f"/api/tenants/{tenant_a.id}/leads/{lead.id}/do-not-call"

    response = await client.post(url, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "ok": True, "phone": "+15550900010", "status": "do_not_call",
    }

    await db.refresh(lead)
    assert lead.status is LeadStatus.DNC
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert [(h.from_status, h.to_status) for h in history] == [("new", "do_not_call")]
    assert history[-1].source == "consent"
    assert "consent:voice:denied" in history[-1].reason

    consent = (
        await db.execute(
            select(LeadConsent).where(
                LeadConsent.lead_id == lead.id, LeadConsent.channel == "voice"
            )
        )
    ).scalar_one()
    assert consent.decision == "denied"
    assert consent.source == "api:legacy_do_not_call"

    # idempotent, and DNC is never reversed by repeating the call
    again = await client.post(url, headers=headers)
    assert again.status_code == 200
    assert again.json()["status"] == "do_not_call"
    await db.refresh(lead)
    assert lead.status is LeadStatus.DNC
    after = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert len(after) == len(history)


async def test_do_not_call_from_the_wrong_environment_is_404(
    client, db, tenant_a, manager_a
):
    headers = await auth_headers(client, manager_a)
    staging = await _staging(db, tenant_a)
    lead = await make_lead(
        db, tenant_a, phone="+15550900011", environment_id=staging.id
    )
    url = f"/api/tenants/{tenant_a.id}/leads/{lead.id}/do-not-call"

    response = await client.post(url, headers=headers)  # production context
    assert response.status_code == 404
    await db.refresh(lead)
    assert lead.status is LeadStatus.NEW  # untouched

    ok = await client.post(
        url, params={"environment_id": str(staging.id)}, headers=headers
    )
    assert ok.status_code == 200
    await db.refresh(lead)
    assert lead.status is LeadStatus.DNC


# ------------------------------------------------------- legacy campaigns ---

async def test_legacy_campaign_urls_still_work(client, db, tenant_a, manager_a):
    headers = await auth_headers(client, manager_a)
    production = await _production(db, tenant_a)

    created = await client.post(
        f"/api/tenants/{tenant_a.id}/campaigns", json={"name": "Old client"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert {"id", "name", "is_active"} <= set(body)
    assert body["is_active"] is False
    assert body["environment_id"] == str(production.id)

    rows = (await client.get(
        f"/api/tenants/{tenant_a.id}/campaigns", headers=headers
    )).json()
    assert [row["id"] for row in rows] == [body["id"]]
    assert {"goal", "calls_per_minute", "leads_total", "leads_done"} <= set(rows[0])

    run = await client.post(
        f"/api/tenants/{tenant_a.id}/campaigns/{body['id']}/run", headers=headers
    )
    assert run.status_code == 200
    assert run.json() == {"dialed": 0, "reason": "campaign_inactive"}


# ------------------------------------------------------------ access rules ---

async def test_foreign_tenant_manager_gets_404_on_every_legacy_url(
    client, db, tenant_a, tenant_b
):
    other = await auth_headers(client, await make_user(db, tenant_b, UserRole.MANAGER))
    lead = await make_lead(db, tenant_a, phone="+15550900012")
    base = f"/api/tenants/{tenant_a.id}"

    assert (await client.post(
        f"{base}/leads", json=_bulk("+15550900013"), headers=other
    )).status_code == 404
    assert (await client.get(f"{base}/leads", headers=other)).status_code == 404
    assert (await client.post(
        f"{base}/leads/{lead.id}/do-not-call", headers=other
    )).status_code == 404
    assert (await client.post(
        f"{base}/campaigns", json={"name": "Hostile"}, headers=other
    )).status_code == 404
    assert (await client.get(f"{base}/campaigns", headers=other)).status_code == 404


async def test_viewer_cannot_write_legacy_lead_routes(client, db, tenant_a, viewer_a):
    headers = await auth_headers(client, viewer_a)
    response = await client.post(
        f"/api/tenants/{tenant_a.id}/leads",
        json=_bulk("+15550900014"),
        headers=headers,
    )
    assert response.status_code == 403
    rows = (
        await db.execute(select(Lead).where(Lead.phone == "+15550900014"))
    ).scalars().all()
    assert rows == []
