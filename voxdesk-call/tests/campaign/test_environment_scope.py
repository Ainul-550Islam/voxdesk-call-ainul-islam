"""Campaign ↔ Lead/Segment environment binding (Batch 06).

A campaign belongs to exactly one tenant + environment pair and may only
target leads and segments from that same pair. Cross-environment and
cross-tenant associations fail closed with the safe not-found; suspended and
archived environments refuse campaign writes; and the existing resolution
order (explicit → server-side selection → default production) is honoured.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.errors import NotFoundError
from app.db.models import Campaign, Environment, EnvironmentSelection
from app.domain.campaign_models import Audience, CampaignDefinition, Segment
from app.services import campaign_service
from app.tenancy.isolation import LifecycleDenied
from tests.conftest import auth_headers, make_lead


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


async def _staging(db, tenant, *, status: str = "active") -> Environment:
    environment = Environment(
        tenant_id=tenant.id, name="Staging", slug="staging", kind="staging",
        status=status, is_default=False,
    )
    db.add(environment)
    await db.commit()
    await db.refresh(environment)
    return environment


def _definition(tenant_id: str, *, environment_id: str = "", lead_ids=(), segment_ids=(),
                name: str = "Spring outreach") -> CampaignDefinition:
    return CampaignDefinition(
        id=f"campaign-{uuid.uuid4().hex[:8]}",
        tenant_id=tenant_id,
        name=name,
        environment_id=environment_id,
        audience=Audience(
            tenant_id=tenant_id,
            lead_ids=tuple(str(x) for x in lead_ids),
            segment_ids=tuple(segment_ids),
            environment_id=environment_id,
        ),
    )


# ------------------------------------------------------- ownership binding ---

async def test_campaign_defaults_to_the_active_production_environment(db, tenant_a):
    production = await _production(db, tenant_a)
    row = await campaign_service.create_campaign(
        db, tenant_a, _definition(str(tenant_a.id))
    )
    assert row.environment_id == production.id
    assert row.tenant_id == tenant_a.id


async def test_campaign_accepts_an_explicit_same_tenant_environment(db, tenant_a):
    staging = await _staging(db, tenant_a)
    row = await campaign_service.create_campaign(
        db, tenant_a, _definition(str(tenant_a.id), environment_id=str(staging.id))
    )
    assert row.environment_id == staging.id


async def test_lookup_without_environment_uses_default_production(db, tenant_a):
    row = await campaign_service.create_campaign(
        db, tenant_a, _definition(str(tenant_a.id))
    )
    definition = await campaign_service.get_campaign(db, tenant_a, str(row.id))
    assert definition.environment_id == str(row.environment_id)
    assert definition.audience.environment_id == str(row.environment_id)


async def test_same_tenant_wrong_environment_lookup_is_not_found(db, tenant_a):
    staging = await _staging(db, tenant_a)
    row = await campaign_service.create_campaign(
        db, tenant_a, _definition(str(tenant_a.id), environment_id=str(staging.id))
    )
    # The default (production) context must not see a staging campaign...
    with pytest.raises(NotFoundError):
        await campaign_service.get_campaign(db, tenant_a, str(row.id))
    # ...but the explicit staging context does.
    definition = await campaign_service.get_campaign(
        db, tenant_a, str(row.id), environment_id=staging.id
    )
    assert definition.environment_id == str(staging.id)


async def test_cross_tenant_campaign_is_not_found(db, tenant_a, tenant_b):
    row = await campaign_service.create_campaign(
        db, tenant_b, _definition(str(tenant_b.id))
    )
    listed = await campaign_service.list_campaigns(db, tenant_a)
    assert [d.id for d in listed] == []
    with pytest.raises(NotFoundError):
        await campaign_service.get_campaign(db, tenant_a, str(row.id))


async def test_cross_tenant_environment_fails_closed(db, tenant_a, tenant_b):
    foreign = await _production(db, tenant_b)
    with pytest.raises(NotFoundError):
        await campaign_service.create_campaign(
            db, tenant_a,
            _definition(str(tenant_a.id), environment_id=str(foreign.id)),
        )
    rows = (
        await db.execute(select(Campaign).where(Campaign.tenant_id == tenant_a.id))
    ).scalars().all()
    assert rows == []


# ---------------------------------------------------------- audience scope ---

async def test_lead_from_another_environment_is_rejected(db, tenant_a):
    staging = await _staging(db, tenant_a)
    staging_lead = await make_lead(
        db, tenant_a, environment_id=staging.id, phone="+15550100001"
    )
    with pytest.raises(NotFoundError):
        await campaign_service.create_campaign(
            db, tenant_a,
            _definition(str(tenant_a.id), lead_ids=[staging_lead.id]),
        )


async def test_lead_from_another_tenant_is_rejected(db, tenant_a, tenant_b):
    foreign_lead = await make_lead(db, tenant_b, phone="+15550100002")
    with pytest.raises(NotFoundError):
        await campaign_service.create_campaign(
            db, tenant_a, _definition(str(tenant_a.id), lead_ids=[foreign_lead.id])
        )


async def test_segment_from_another_environment_is_rejected(db, tenant_a):
    staging = await _staging(db, tenant_a)
    segment = Segment(
        id="seg-staging", tenant_id=str(tenant_a.id), name="Staging VIPs",
        environment_id=str(staging.id),
    )
    campaign_service.register_segment(tenant_a, segment)
    with pytest.raises(NotFoundError):
        await campaign_service.create_campaign(
            db, tenant_a,
            _definition(str(tenant_a.id), segment_ids=["seg-staging"]),
        )


async def test_segment_resolution_is_scoped_to_the_campaign_environment(db, tenant_a):
    staging = await _staging(db, tenant_a)
    production_lead = await make_lead(db, tenant_a, phone="+15550100003")
    staging_lead = await make_lead(
        db, tenant_a, environment_id=staging.id, phone="+15550100004"
    )
    segment = Segment(
        id=f"seg-all-{uuid.uuid4().hex[:6]}", tenant_id=str(tenant_a.id), name="Everyone",
    )
    campaign_service.register_segment(tenant_a, segment)
    definition = _definition(str(tenant_a.id), segment_ids=[segment.id])
    row = await campaign_service.create_campaign(db, tenant_a, definition)
    fetched = await campaign_service.get_campaign(db, tenant_a, str(row.id))
    result = await campaign_service.validate_audience(db, tenant_a, fetched)
    assert result["lead_count"] == 1
    ids = await campaign_service.resolve_audience(db, tenant_a, fetched.audience)
    assert ids == [str(production_lead.id)]
    assert str(staging_lead.id) not in ids


async def test_execution_plan_skips_a_cross_environment_lead(db, tenant_a):
    staging = await _staging(db, tenant_a)
    staging_lead = await make_lead(
        db, tenant_a, environment_id=staging.id, phone="+15550100005"
    )
    row = await campaign_service.create_campaign(
        db, tenant_a, _definition(str(tenant_a.id))
    )
    # Simulate pre-0026 overlay data naming a staging lead: the planner must
    # refuse it even though the audience store still mentions the id.
    campaign_service._overlay(tenant_a.id, row.id)["audience"] = {
        "segment_ids": [], "lead_ids": [str(staging_lead.id)],
    }
    definition = await campaign_service.get_campaign(db, tenant_a, str(row.id))
    intents = await campaign_service.execution_plan(db, tenant_a, definition)
    assert intents == []


# ------------------------------------------------------- lifecycle gating ---

async def test_archived_environment_refuses_campaign_writes(db, tenant_a):
    archived = await _staging(db, tenant_a, status="archived")
    archived.slug = "staging-archived"
    await db.commit()
    with pytest.raises(LifecycleDenied):
        await campaign_service.create_campaign(
            db, tenant_a,
            _definition(str(tenant_a.id), environment_id=str(archived.id)),
        )


async def test_suspended_environment_refuses_campaign_writes(db, tenant_a):
    suspended = await _staging(db, tenant_a, status="suspended")
    suspended.slug = "staging-suspended"
    await db.commit()
    with pytest.raises(LifecycleDenied):
        await campaign_service.create_campaign(
            db, tenant_a,
            _definition(str(tenant_a.id), environment_id=str(suspended.id)),
        )


async def test_state_change_in_a_suspended_environment_is_refused(db, tenant_a):
    staging = await _staging(db, tenant_a)
    row = await campaign_service.create_campaign(
        db, tenant_a, _definition(str(tenant_a.id), environment_id=str(staging.id))
    )
    staging.status = "suspended"
    await db.commit()
    # keep the resolver's production cache honest for the other environments
    with pytest.raises(LifecycleDenied):
        await campaign_service.schedule_campaign(
            db, tenant_a, str(row.id), environment_id=staging.id
        )


# ------------------------------------------------------------ API resolution ---

async def test_api_create_uses_the_selected_environment(client, db, tenant_a, manager_a):
    staging = await _staging(db, tenant_a)
    db.add(EnvironmentSelection(
        user_id=manager_a.id, tenant_id=tenant_a.id, environment_id=staging.id,
    ))
    await db.commit()
    headers = await auth_headers(client, manager_a)
    response = await client.post(
        "/api/campaigns", json={"name": "Selected env campaign"}, headers=headers
    )
    assert response.status_code == 201, response.text
    assert response.json()["environment_id"] == str(staging.id)


async def test_api_create_with_explicit_environment(client, db, tenant_a, manager_a):
    production = await _production(db, tenant_a)
    headers = await auth_headers(client, manager_a)
    response = await client.post(
        "/api/campaigns",
        json={"name": "Explicit env campaign", "environment_id": str(production.id)},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["environment_id"] == str(production.id)


async def test_api_create_with_a_foreign_environment_is_404(client, db, tenant_a, tenant_b,
                                                            manager_a):
    foreign = await _production(db, tenant_b)
    headers = await auth_headers(client, manager_a)
    response = await client.post(
        "/api/campaigns",
        json={"name": "Hostile", "environment_id": str(foreign.id)},
        headers=headers,
    )
    assert response.status_code == 404
    rows = (
        await db.execute(select(Campaign).where(Campaign.tenant_id == tenant_a.id))
    ).scalars().all()
    assert rows == []


async def test_api_defaults_to_production_without_any_context(client, db, tenant_a, manager_a):
    production = await _production(db, tenant_a)
    headers = await auth_headers(client, manager_a)
    response = await client.post(
        "/api/campaigns", json={"name": "Default env campaign"}, headers=headers
    )
    assert response.status_code == 201, response.text
    assert response.json()["environment_id"] == str(production.id)


async def test_client_supplied_tenant_cannot_change_the_authenticated_tenant(
    client, db, tenant_a, tenant_b, manager_a
):
    """The body has no tenant field at all (extra=forbid); identity is the token's."""
    headers = await auth_headers(client, manager_a)
    response = await client.post(
        "/api/campaigns",
        json={"name": "Sneaky", "tenant_id": str(tenant_b.id)},
        headers=headers,
    )
    assert response.status_code == 422  # extra fields forbidden
    rows = (
        await db.execute(select(Campaign).where(Campaign.tenant_id == tenant_b.id))
    ).scalars().all()
    assert rows == []


async def test_audience_environment_stamp_is_normalized_never_honored(db, tenant_a):
    """A definition that arrives with a mismatched audience environment stamp
    cannot drag the campaign anywhere: create normalizes audience scope to
    the resolved campaign environment (and the audience's *contents* are
    still validated lead-by-lead against it)."""
    staging = await _staging(db, tenant_a)
    production = await _production(db, tenant_a)
    definition = _definition(
        str(tenant_a.id), environment_id=str(production.id), name="Mismatched"
    )
    # audience stamped for staging while the campaign lives in production
    object.__setattr__(
        definition, "audience",
        Audience(tenant_id=str(tenant_a.id), environment_id=str(staging.id)),
    )
    row = await campaign_service.create_campaign(db, tenant_a, definition)
    assert row.environment_id == production.id

    fetched = await campaign_service.get_campaign(db, tenant_a, str(row.id))
    assert fetched.environment_id == str(production.id)
    assert fetched.audience.environment_id == str(production.id)
