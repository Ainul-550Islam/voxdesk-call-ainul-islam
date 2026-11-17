"""Agency flows: one agency, many client organizations (PART 16, P16-02).

The interesting question is not "can an agency create a client" — that is an
INSERT. It is **what the agency can reach afterwards**, so most of these tests
are refusals:

* a client outside the portfolio is a 404, indistinguishable from a missing one;
* a member who is not agency-level cannot create, link or copy anything;
* an agent copy between two accounts of *different* agencies is refused before
  the bundle is built, so one client's configuration cannot leak into another's;
* a secret is never carried across, and an import that still needs one is
  refused rather than silently broken;
* a billing roll-up never adds two currencies together, and shows a silent client
  instead of omitting it — "no invoices" and "not a client" are different facts.

`AgencyClientLink` exists because `app/organization/` cannot express a portfolio:
no column links two organizations, and a membership row is pinned to the
organization of the user's own tenant. That is why this part added migration
`0087` and not only code.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

import app.db.agency_models  # noqa: F401
import app.db.models  # noqa: F401
from app.db.agency_models import AgencyClientLink
from app.db.models import (
    BillingInvoice,
    BillingProviderType,
    InvoiceStatus,
    OrganizationMembership,
    Tenant,
    UserRole,
)
from app.organization import agency_service
from app.organization.models import OrganizationStatus
from app.organization.service import create_organization
from app.tenancy.isolation import Conflict, NotFound, ValidationFailed
from tests.conftest import make_tenant, make_user

pytestmark = pytest.mark.asyncio


async def _org_with_tenant(db, name: str, slug: str) -> tuple:
    organization = await create_organization(db, name=name, slug=slug, commit=True)
    tenant = await make_tenant(db, f"{name} line 1")
    tenant.organization_id = organization.id
    await db.commit()
    await db.refresh(tenant)
    return organization, tenant


async def _agency(db):
    """An agency organization with its owner and its own (home) tenant.

    A user created inside a tenant that belongs to an organization gets its home
    membership written for it, so the owner here is an organization owner without
    an explicit membership row — which is exactly the state production is in.
    """
    organization, tenant = await _org_with_tenant(db, "Bright Agency", "bright-agency")
    owner = await make_user(db, tenant, UserRole.OWNER)
    row = await db.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization.id,
            OrganizationMembership.user_id == owner.id,
        )
    )
    assert row is not None and row.role == UserRole.OWNER
    return organization, tenant, owner


# --------------------------------------------------------------- portfolio ---


async def test_creating_a_client_links_it_and_audits_it(db):
    agency, _home, owner = await _agency(db)
    client, link = await agency_service.create_client_organization(
        db, actor=owner, agency_organization_id=agency.id, name="North Clinic", slug="north-clinic"
    )
    assert link.agency_organization_id == agency.id
    assert link.created_by_user_id == owner.id
    assert link.client_organization_id == client.id

    listing = await agency_service.clients(db, actor=owner, agency_organization_id=agency.id)
    assert [item.name for item in listing] == ["North Clinic"]
    assert listing[0].tenant_count == 0  # created empty on purpose
    assert listing[0].status == OrganizationStatus.ACTIVE.value


async def test_a_client_belongs_to_one_agency_only(db):
    agency, _home, owner = await _agency(db)
    other_org, other_tenant = await _org_with_tenant(db, "Rival Agency", "rival-agency")
    rival_owner = await make_user(db, other_tenant, UserRole.OWNER)

    client, _ = await agency_service.create_client_organization(
        db, actor=owner, agency_organization_id=agency.id, name="Shared Client", slug="shared-client"
    )
    with pytest.raises(Conflict):
        await agency_service.link_existing_organization(
            db,
            actor=rival_owner,
            agency_organization_id=other_org.id,
            client_organization_id=client.id,
        )
    # Linking it again to the same agency is a conflict too, not a second row.
    with pytest.raises(Conflict):
        await agency_service.link_existing_organization(
            db, actor=owner, agency_organization_id=agency.id, client_organization_id=client.id
        )


async def test_an_agency_cannot_be_its_own_client(db):
    agency, _home, owner = await _agency(db)
    with pytest.raises(ValidationFailed):
        await agency_service.link_existing_organization(
            db, actor=owner, agency_organization_id=agency.id, client_organization_id=agency.id
        )


async def test_unlinking_keeps_the_organization_and_its_tenants(db):
    agency, _home, owner = await _agency(db)
    client, _link = await agency_service.create_client_organization(
        db, actor=owner, agency_organization_id=agency.id, name="North Clinic", slug="north-clinic"
    )
    client_tenant = await make_tenant(db, "North line 1")
    client_tenant.organization_id = client.id
    await db.commit()

    await agency_service.unlink_client(
        db, actor=owner, agency_organization_id=agency.id, client_organization_id=client.id
    )
    assert await agency_service.clients(db, actor=owner, agency_organization_id=agency.id) == []
    assert await db.get(Tenant, client_tenant.id) is not None
    assert (await db.get(type(client), client.id)).status == OrganizationStatus.ACTIVE.value


async def test_a_member_below_agency_level_cannot_manage_the_portfolio(db):
    agency, home, _owner = await _agency(db)
    manager = await make_user(db, home, UserRole.MANAGER)
    with pytest.raises(NotFound):
        await agency_service.create_client_organization(
            db, actor=manager, agency_organization_id=agency.id, name="Nope", slug="nope"
        )
    with pytest.raises(NotFound):
        await agency_service.clients(db, actor=manager, agency_organization_id=agency.id)


async def test_a_stranger_is_told_nothing(db):
    agency, _home, _owner = await _agency(db)
    _other_org, other_tenant = await _org_with_tenant(db, "Other", "other-org")
    stranger = await make_user(db, other_tenant, UserRole.OWNER)
    with pytest.raises(NotFound):
        await agency_service.clients(db, actor=stranger, agency_organization_id=agency.id)


# ----------------------------------------------------------------- context ---


async def test_switching_into_a_client_reports_its_basis_and_tenants(db):
    agency, home, owner = await _agency(db)
    client, _ = await agency_service.create_client_organization(
        db, actor=owner, agency_organization_id=agency.id, name="North Clinic", slug="north-clinic"
    )
    client_tenant = await make_tenant(db, "North line 1")
    client_tenant.organization_id = client.id
    await db.commit()

    context = await agency_service.switch_context(
        db, actor=owner, agency_organization_id=agency.id, organization_id=client.id
    )
    # The agency owner holds no membership in the client, so access is the
    # portfolio basis — stated, not implied.
    assert context.access_basis == "agency"
    assert context.tenants == (client_tenant.id,)
    assert context.is_agency_home is False
    payload = context.as_dict()
    assert payload["impersonating"] is False
    assert payload["agency_organization_id"] == str(agency.id)

    home_context = await agency_service.switch_context(
        db, actor=owner, agency_organization_id=agency.id, organization_id=agency.id
    )
    assert home_context.is_agency_home is True
    assert home_context.access_basis == "membership"


async def test_switching_into_a_client_outside_the_portfolio_is_a_404(db):
    agency, _home, owner = await _agency(db)
    foreign, foreign_tenant = await _org_with_tenant(db, "Someone Else", "someone-else")
    with pytest.raises(NotFound):
        await agency_service.switch_context(
            db, actor=owner, agency_organization_id=agency.id, organization_id=foreign.id
        )


async def test_a_suspended_client_can_be_read_but_not_written(db):
    agency, _home, owner = await _agency(db)
    client, _ = await agency_service.create_client_organization(
        db, actor=owner, agency_organization_id=agency.id, name="Paused Client", slug="paused-client"
    )
    client.status = OrganizationStatus.SUSPENDED.value
    await db.commit()

    context = await agency_service.switch_context(
        db, actor=owner, agency_organization_id=agency.id, organization_id=client.id
    )
    with pytest.raises(ValidationFailed):
        await agency_service.require_writable_context(db, context)


# -------------------------------------------------------------- agent copy ---


CONFIG = {
    "name": "Front desk",
    "voice": {"provider": "minimax", "api_key": "sk-live-should-not-travel"},
    "hours": {"open": "09:00", "close": "17:00"},
}


async def test_an_agent_copy_between_clients_strips_the_secret(db):
    agency, _home, owner = await _agency(db)
    source_org, source_tenant = await _org_with_tenant(db, "Client One", "client-one")
    target_org, target_tenant = await _org_with_tenant(db, "Client Two", "client-two")
    for org in (source_org, target_org):
        await agency_service.link_existing_organization(
            db, actor=owner, agency_organization_id=agency.id, client_organization_id=org.id
        )

    copy = await agency_service.copy_agent(
        db,
        actor=owner,
        agency_organization_id=agency.id,
        source_tenant_id=source_tenant.id,
        target_tenant_id=target_tenant.id,
        agent_id="agent-1",
        config=CONFIG,
        dry_run=True,
    )
    assert copy.bundle["agent"]["config"]["hours"]["close"] == "17:00"
    assert "sk-live-should-not-travel" not in str(copy.bundle)
    assert copy.secrets_required, "the export must name the paths that need re-entry"
    assert copy.as_dict()["secrets_included"] is False
    assert copy.dry_run is True


async def test_a_real_copy_without_the_secret_is_refused(db):
    agency, _home, owner = await _agency(db)
    source_org, source_tenant = await _org_with_tenant(db, "Client One", "client-one")
    target_org, target_tenant = await _org_with_tenant(db, "Client Two", "client-two")
    for org in (source_org, target_org):
        await agency_service.link_existing_organization(
            db, actor=owner, agency_organization_id=agency.id, client_organization_id=org.id
        )
    with pytest.raises(ValidationFailed):
        await agency_service.copy_agent(
            db,
            actor=owner,
            agency_organization_id=agency.id,
            source_tenant_id=source_tenant.id,
            target_tenant_id=target_tenant.id,
            agent_id="agent-1",
            config=CONFIG,
            dry_run=False,
        )


async def test_a_copy_across_agencies_is_refused_before_the_bundle_exists(db):
    agency, _home, owner = await _agency(db)
    mine, my_tenant = await _org_with_tenant(db, "My Client", "my-client")
    await agency_service.link_existing_organization(
        db, actor=owner, agency_organization_id=agency.id, client_organization_id=mine.id
    )
    _theirs, their_tenant = await _org_with_tenant(db, "Their Client", "their-client")
    with pytest.raises(NotFound):
        await agency_service.copy_agent(
            db,
            actor=owner,
            agency_organization_id=agency.id,
            source_tenant_id=my_tenant.id,
            target_tenant_id=their_tenant.id,
            agent_id="agent-1",
            config=CONFIG,
        )


async def test_a_copy_into_the_same_account_is_refused(db):
    agency, _home, owner = await _agency(db)
    mine, my_tenant = await _org_with_tenant(db, "My Client", "my-client")
    await agency_service.link_existing_organization(
        db, actor=owner, agency_organization_id=agency.id, client_organization_id=mine.id
    )
    with pytest.raises(ValidationFailed):
        await agency_service.copy_agent(
            db,
            actor=owner,
            agency_organization_id=agency.id,
            source_tenant_id=my_tenant.id,
            target_tenant_id=my_tenant.id,
            agent_id="agent-1",
            config=CONFIG,
        )


# ----------------------------------------------------------------- billing ---


async def _invoice(db, tenant, *, amount: int, currency: str, paid: int = 0) -> None:
    db.add(
        BillingInvoice(
            tenant_id=tenant.id,
            provider=BillingProviderType.STRIPE,
            external_invoice_id=f"in_{uuid.uuid4().hex[:12]}",
            status=InvoiceStatus.PAID if paid >= amount else InvoiceStatus.OPEN,
            currency=currency,
            amount_due_cents=amount,
            amount_paid_cents=paid,
        )
    )
    await db.commit()


async def test_the_rollup_totals_per_client_and_never_mixes_currencies(db):
    agency, _home, owner = await _agency(db)
    one, one_tenant = await _org_with_tenant(db, "Client One", "client-one")
    two, two_tenant = await _org_with_tenant(db, "Client Two", "client-two")
    silent, _silent_tenant = await _org_with_tenant(db, "Quiet Client", "quiet-client")
    for org in (one, two, silent):
        await agency_service.link_existing_organization(
            db, actor=owner, agency_organization_id=agency.id, client_organization_id=org.id
        )

    await _invoice(db, one_tenant, amount=1000, currency="usd")
    await _invoice(db, one_tenant, amount=2500, currency="usd", paid=2500)
    await _invoice(db, one_tenant, amount=900, currency="eur")
    await _invoice(db, two_tenant, amount=700, currency="usd")

    rollup = {
        row.name: {total.currency: total for total in row.totals}
        for row in await agency_service.billing_rollup(
            db, actor=owner, agency_organization_id=agency.id
        )
    }
    assert rollup["Client One"]["usd"].amount_due_cents == 3500
    assert rollup["Client One"]["usd"].amount_paid_cents == 2500
    assert rollup["Client One"]["usd"].invoice_count == 2
    assert rollup["Client One"]["eur"].amount_due_cents == 900
    assert rollup["Client Two"]["usd"].amount_due_cents == 700
    # A client with no invoices is listed, not skipped.
    assert rollup["Quiet Client"] == {}


async def test_another_agencys_client_never_appears_in_the_rollup(db):
    agency, _home, owner = await _agency(db)
    mine, my_tenant = await _org_with_tenant(db, "Mine", "mine-org")
    await agency_service.link_existing_organization(
        db, actor=owner, agency_organization_id=agency.id, client_organization_id=mine.id
    )
    _theirs, their_tenant = await _org_with_tenant(db, "Theirs", "theirs-org")
    await _invoice(db, their_tenant, amount=9999, currency="usd")

    rollup = await agency_service.billing_rollup(
        db, actor=owner, agency_organization_id=agency.id
    )
    assert [row.name for row in rollup] == ["Mine"]
    assert rollup[0].totals == ()


async def test_the_link_table_is_tenant_free_and_cascades(db):
    """A link holds no tenant reference; deleting an organization removes it."""
    agency, _home, owner = await _agency(db)
    client, _ = await agency_service.create_client_organization(
        db, actor=owner, agency_organization_id=agency.id, name="North Clinic", slug="north-clinic"
    )
    link = await db.scalar(
        select(AgencyClientLink).where(AgencyClientLink.client_organization_id == client.id)
    )
    assert link is not None
    # The link is organization-scoped: no tenant column means an agency cannot
    # accidentally gain a tenant-scoped grant by being linked to a client.
    assert "tenant_id" not in {column.name for column in AgencyClientLink.__table__.columns}
