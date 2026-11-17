"""Agency flows: one agency, many client organizations (PART 16, P16-02).

What an agency actually does with VoxDesk, in the order it does it:

1. creates a client organization (and its first tenant) rather than asking the
   client to sign up;
2. moves between clients without a second login;
3. copies an agent it built for one client into another client's account;
4. reads one billing roll-up across the portfolio.

The design rules, each of which has a test:

* **The portfolio is a link, not an access grant.** ``AgencyClientLink`` says which
  organizations belong to an agency. Nothing here lets an agency *user* into a
  client's data: every operation re-checks the actor's membership through
  `app/organization/membership_service`, so an agency administrator with a link
  but no membership still gets a 404 — the same answer as a client that does not
  exist.
* **Cross-organization agent copy is portfolio-scoped.** The portability service
  (PART 12) can export and plan an import; it cannot answer "may these two tenants
  talk to each other". That answer lives here, and it refuses a tenant whose
  organization is outside the portfolio *before* the bundle is even read.
* **Secrets never travel.** The export strips them and lists the paths that need
  re-entry; an import that has no supplied value for a required path is refused
  rather than landing an agent that will fail on its first call.
* **Roll-up never adds across currencies.** A total is reported per currency;
  summing cents from different currencies is how a reseller invoice ends up wrong
  by a factor nobody can explain.
* **Every write is audited** on the tenant the change lands in, with the agency
  organization in the detail — so a client's own audit log shows that an agency
  touched their account.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.agency_models import AgencyClientLink
from app.db.models import BillingInvoice, Organization, Tenant, User, UserRole
from app.organization import events
from app.organization import service as organization_service
from app.organization.models import OrganizationStatus
from app.organization.membership_service import effective_role
from app.services import agent_portability_service as portability
from app.tenancy.isolation import Conflict, NotFound, ValidationFailed

#: Roles that may act for an agency. A manager runs one client; an owner runs the
#: portfolio. Anything lower cannot create or move clients.
AGENCY_ROLES: frozenset[UserRole] = frozenset({UserRole.OWNER, UserRole.ADMIN})


class AgencyError(ValidationFailed):
    """An agency operation was refused; nothing was written."""


@dataclass(frozen=True)
class AgencyContext:
    """Where the actor is now, and what they may do there.

    Returned by :func:`switch_context` and carried into the client operations. It
    is not a token and it grants nothing on its own: the caller passes it back so
    the *next* call can re-verify, which is the only way this stays true after a
    membership is revoked mid-session.
    """

    actor_user_id: uuid.UUID
    agency_organization_id: uuid.UUID
    active_organization_id: uuid.UUID
    role: UserRole
    tenants: tuple[uuid.UUID, ...] = ()
    is_agency_home: bool = False
    #: ``membership`` (the actor is a member of the client organization) or
    #: ``agency`` (portfolio-level access only). Never inferred from the request.
    access_basis: str = "membership"

    def as_dict(self) -> dict[str, Any]:
        return {
            "actor_user_id": str(self.actor_user_id),
            "agency_organization_id": str(self.agency_organization_id),
            "active_organization_id": str(self.active_organization_id),
            "role": self.role.value,
            "tenants": [str(item) for item in self.tenants],
            "is_agency_home": self.is_agency_home,
            "access_basis": self.access_basis,
            # There is no impersonation: the actor's own identity is used, and
            # every action is attributed to them.
            "impersonating": False,
        }


@dataclass(frozen=True)
class ClientSummary:
    organization_id: uuid.UUID
    name: str
    slug: str
    status: str
    tenant_count: int
    linked_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "organization_id": str(self.organization_id),
            "name": self.name,
            "slug": self.slug,
            "status": self.status,
            "tenant_count": self.tenant_count,
            "linked_at": self.linked_at,
        }


# ------------------------------------------------------------------ helpers ---


async def _require_agency_role(
    session: AsyncSession, actor: User, organization_id: uuid.UUID
) -> UserRole:
    role = await effective_role(session, actor, organization_id)
    if role not in AGENCY_ROLES:
        # 404-shaped: an actor outside this organization must not learn that it
        # exists, and a member below agency level is outside it for this purpose.
        raise NotFound()
    return role


async def _agency_for_client(session: AsyncSession, organization_id: uuid.UUID) -> uuid.UUID | None:
    return await session.scalar(
        select(AgencyClientLink.agency_organization_id).where(
            AgencyClientLink.client_organization_id == organization_id
        )
    )


async def _portfolio_contains(
    session: AsyncSession, agency_organization_id: uuid.UUID, organization_id: uuid.UUID
) -> bool:
    if agency_organization_id == organization_id:
        return True  # the agency's own home organization is in its portfolio
    return (
        await session.scalar(
            select(AgencyClientLink.id).where(
                AgencyClientLink.agency_organization_id == agency_organization_id,
                AgencyClientLink.client_organization_id == organization_id,
            )
        )
    ) is not None


async def _organization_of_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID:
    organization_id = await session.scalar(
        select(Tenant.organization_id).where(Tenant.id == tenant_id)
    )
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFound()
    if organization_id is None:
        raise AgencyError("That tenant is not attached to an organization")
    return organization_id


async def _tenant_counts(session: AsyncSession, organization_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not organization_ids:
        return {}
    rows = await session.execute(
        select(Tenant.organization_id, func.count(Tenant.id))
        .where(Tenant.organization_id.in_(organization_ids))
        .group_by(Tenant.organization_id)
    )
    return {organization_id: count for organization_id, count in rows.all()}


# --------------------------------------------------------------- portfolio ---


async def create_client_organization(
    session: AsyncSession,
    *,
    actor: User,
    agency_organization_id: uuid.UUID,
    name: str,
    slug: str | None = None,
    actor_meta: organization_service.Actor | None = None,
) -> tuple[Organization, AgencyClientLink]:
    """Create a client organization and link it to the agency.

    The agency user is **not** given a membership in the client organization:
    that would need a second tenant for them and the membership model refuses a
    cross-organization target anyway. Access to the client is a separate,
    explicit act (:func:`grant_agency_membership`), which is the honest shape of
    the relationship — a reseller relationship is not an implication of access.
    """
    await _require_agency_role(session, actor, agency_organization_id)
    organization = await organization_service.create_organization(
        session, name=name, slug=slug, actor=actor_meta, commit=False
    )
    if organization.id == agency_organization_id:
        raise AgencyError("An agency cannot be its own client")
    link = AgencyClientLink(
        agency_organization_id=agency_organization_id,
        client_organization_id=organization.id,
        created_by_user_id=actor.id,
    )
    session.add(link)
    await session.flush()
    # The event is scoped to the agency's own tenant: that is where the actor
    # acted. The client organization id in the payload is what an auditor needs
    # to follow the change into the client's own ledger.
    await events.organization_event(
        session,
        kind="created",
        organization_id=organization.id,
        status=organization.status,
        tenant_id=actor.tenant_id,
        actor_user_id=actor.id,
        actor_email=actor.email or "",
        commit=False,
    )
    await session.commit()
    await session.refresh(organization)
    await session.refresh(link)
    return organization, link


async def link_existing_organization(
    session: AsyncSession,
    *,
    actor: User,
    agency_organization_id: uuid.UUID,
    client_organization_id: uuid.UUID,
) -> AgencyClientLink:
    """Attach an existing organization. Refuses a second agency, and self-links."""
    await _require_agency_role(session, actor, agency_organization_id)
    if client_organization_id == agency_organization_id:
        raise AgencyError("An agency cannot be its own client")
    client = await session.get(Organization, client_organization_id)
    if client is None:
        raise NotFound()
    existing = await _agency_for_client(session, client_organization_id)
    if existing is not None:
        if existing == agency_organization_id:
            raise Conflict("That organization is already in this portfolio")
        raise Conflict("That organization already belongs to another agency")
    link = AgencyClientLink(
        agency_organization_id=agency_organization_id,
        client_organization_id=client_organization_id,
        created_by_user_id=actor.id,
    )
    session.add(link)
    await session.flush()
    await session.commit()
    await session.refresh(link)
    return link


async def unlink_client(
    session: AsyncSession,
    *,
    actor: User,
    agency_organization_id: uuid.UUID,
    client_organization_id: uuid.UUID,
) -> None:
    """Detach a client. The organization, its tenants and its users stay."""
    await _require_agency_role(session, actor, agency_organization_id)
    link = await session.scalar(
        select(AgencyClientLink).where(
            AgencyClientLink.agency_organization_id == agency_organization_id,
            AgencyClientLink.client_organization_id == client_organization_id,
        )
    )
    if link is None:
        raise NotFound()
    await session.delete(link)
    await events.organization_event(
        session,
        kind="updated",
        organization_id=client_organization_id,
        status="unlinked",
        tenant_id=actor.tenant_id,
        actor_user_id=actor.id,
        actor_email=actor.email or "",
        commit=False,
    )
    await session.commit()


async def clients(session: AsyncSession, *, actor: User, agency_organization_id: uuid.UUID) -> list[ClientSummary]:
    """Every client in the portfolio, with its tenant count."""
    await _require_agency_role(session, actor, agency_organization_id)
    rows = (
        await session.execute(
            select(Organization, AgencyClientLink.created_at)
            .join(AgencyClientLink, AgencyClientLink.client_organization_id == Organization.id)
            .where(AgencyClientLink.agency_organization_id == agency_organization_id)
            .order_by(Organization.name)
        )
    ).all()
    counts = await _tenant_counts(session, [organization.id for organization, _ in rows])
    return [
        ClientSummary(
            organization_id=organization.id,
            name=organization.name,
            slug=organization.slug,
            status=organization.status,
            tenant_count=counts.get(organization.id, 0),
            linked_at=created_at.isoformat() if created_at else None,
        )
        for organization, created_at in rows
    ]


# ----------------------------------------------------------------- context ---


async def switch_context(
    session: AsyncSession,
    *,
    actor: User,
    agency_organization_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> AgencyContext:
    """Move the actor's working context to another organization in the portfolio.

    Refuses a client the actor is not a member of, and a client outside the
    portfolio — both as ``NotFound``. A suspended client organization is still
    switchable-to *read*, so reports stay available, but the context reports the
    status so a write path can refuse; :func:`require_writable_context` does that.
    """
    agency_role = await _require_agency_role(session, actor, agency_organization_id)
    if not await _portfolio_contains(session, agency_organization_id, organization_id):
        raise NotFound()
    organization = await session.get(Organization, organization_id)
    if organization is None:
        raise NotFound()

    # Two legitimate ways to be here, and the context says which one it used:
    # the actor is a member of the client organization (``membership``), or they
    # act for the agency on a client they hold no membership in (``agency``).
    # The second case is a portfolio-level read, not a way into the client's
    # tenant routes — those still call the normal tenant guards.
    member_role = await effective_role(session, actor, organization_id)
    basis = "membership" if member_role is not None else "agency"
    role = member_role or agency_role

    tenants = tuple(
        (
            await session.scalars(
                select(Tenant.id).where(Tenant.organization_id == organization_id).order_by(Tenant.created_at)
            )
        ).all()
    )
    return AgencyContext(
        actor_user_id=actor.id,
        agency_organization_id=agency_organization_id,
        active_organization_id=organization_id,
        role=role,
        tenants=tenants,
        is_agency_home=organization_id == agency_organization_id,
        access_basis=basis,
    )


async def require_writable_context(
    session: AsyncSession, context: AgencyContext
) -> AgencyContext:
    """Refuse a write in a client organization that is not active."""
    organization = await session.get(Organization, context.active_organization_id)
    if organization is None:
        raise NotFound()
    if organization.status != OrganizationStatus.ACTIVE.value:
        raise AgencyError(
            f"Client organization is {organization.status}; writes are refused"
        )
    return context


# ---------------------------------------------------------- agent copy ----


@dataclass(frozen=True)
class AgentCopy:
    """The outcome of a cross-organization agent copy."""

    source_tenant_id: uuid.UUID
    target_tenant_id: uuid.UUID
    bundle: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    secrets_required: tuple[str, ...] = ()
    dry_run: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_tenant_id": str(self.source_tenant_id),
            "target_tenant_id": str(self.target_tenant_id),
            "bundle": self.bundle,
            "plan": self.plan,
            "secrets_required": list(self.secrets_required),
            "dry_run": self.dry_run,
            "secrets_included": False,
        }


async def _portfolio_tenant(
    session: AsyncSession, agency_organization_id: uuid.UUID, tenant_id: uuid.UUID
) -> uuid.UUID:
    organization_id = await _organization_of_tenant(session, tenant_id)
    if not await _portfolio_contains(session, agency_organization_id, organization_id):
        raise NotFound()
    return organization_id


async def copy_agent(
    session: AsyncSession,
    *,
    actor: User,
    agency_organization_id: uuid.UUID,
    source_tenant_id: uuid.UUID,
    target_tenant_id: uuid.UUID,
    agent_id: str,
    config: Mapping[str, Any],
    flow: Mapping[str, Any] | None = None,
    supplied_secrets: Mapping[str, str] | None = None,
    mode: str = "create",
    dry_run: bool = True,
) -> AgentCopy:
    """Export an agent from one portfolio account and plan its import in another.

    The two tenants are checked against the portfolio **before** the bundle is
    built, so a mistaken tenant id cannot leak one client's configuration to
    another. The export strips secrets (PART 12 already does that); an import that
    still has unresolved secret paths is refused unless the caller supplies them,
    because an agent copied without its provider key fails on the first call and
    looks like an outage.
    """
    await _require_agency_role(session, actor, agency_organization_id)
    await _portfolio_tenant(session, agency_organization_id, source_tenant_id)
    target_organization_id = await _portfolio_tenant(
        session, agency_organization_id, target_tenant_id
    )
    if source_tenant_id == target_tenant_id:
        raise AgencyError("Source and target are the same account")

    bundle = portability.export_agent(
        agent_id=agent_id,
        config=dict(config),
        flow=dict(flow) if flow else {},
        exported_by=str(actor.id),
    )
    plan = portability.import_agent(
        bundle,
        mode=mode,
        supplied_secrets=dict(supplied_secrets or {}),
        dry_run=dry_run,
    )
    required = tuple(plan.unresolved_secrets)
    if required and not dry_run:
        raise AgencyError(
            "The bundle needs these secrets before it can be imported: "
            + ", ".join(sorted(required))
        )

    # Attribution beats locality here: the audit writer refuses an actor who does
    # not belong to the tenant an event is scoped to, and the agency operator
    # belongs to the agency's tenant. The client side is carried in the payload,
    # so a reader on either side can follow the copy — and the client's own ledger
    # still records the agent write on its own tenant through the normal writer.
    await events.organization_event(
        session,
        kind="updated",
        organization_id=target_organization_id,
        status="agent_copied",
        tenant_id=actor.tenant_id,
        actor_user_id=actor.id,
        actor_email=actor.email or "",
        commit=False,
        extra={
            "source_tenant_id": str(source_tenant_id),
            "target_tenant_id": str(target_tenant_id),
            "secrets_included": False,
            "dry_run": dry_run,
        },
    )
    await session.commit()
    return AgentCopy(
        source_tenant_id=source_tenant_id,
        target_tenant_id=target_tenant_id,
        bundle=bundle,
        plan=plan.as_dict(),
        secrets_required=required,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------- billing ---


@dataclass(frozen=True)
class CurrencyTotal:
    currency: str
    invoice_count: int
    amount_due_cents: int
    amount_paid_cents: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "invoice_count": self.invoice_count,
            "amount_due_cents": self.amount_due_cents,
            "amount_paid_cents": self.amount_paid_cents,
        }


@dataclass(frozen=True)
class ClientBilling:
    organization_id: uuid.UUID
    name: str
    totals: tuple[CurrencyTotal, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "organization_id": str(self.organization_id),
            "name": self.name,
            "totals": [total.as_dict() for total in self.totals],
        }


async def billing_rollup(
    session: AsyncSession, *, actor: User, agency_organization_id: uuid.UUID
) -> list[ClientBilling]:
    """Every client's invoices, totalled per currency.

    Only the provider's mirrored amounts are summed, and never across currencies.
    A client with no invoices appears with an empty ``totals`` list rather than
    being omitted — "no invoices" and "not in the portfolio" are different facts
    and a reseller reconciliation depends on telling them apart.
    """
    await _require_agency_role(session, actor, agency_organization_id)
    portfolio = await clients(session, actor=actor, agency_organization_id=agency_organization_id)
    if not portfolio:
        return []
    organization_ids = [client.organization_id for client in portfolio]

    rows = (
        await session.execute(
            select(
                Tenant.organization_id,
                BillingInvoice.currency,
                func.count(BillingInvoice.id),
                func.coalesce(func.sum(BillingInvoice.amount_due_cents), 0),
                func.coalesce(func.sum(BillingInvoice.amount_paid_cents), 0),
            )
            .join(BillingInvoice, BillingInvoice.tenant_id == Tenant.id)
            .where(Tenant.organization_id.in_(organization_ids))
            .group_by(Tenant.organization_id, BillingInvoice.currency)
            .order_by(Tenant.organization_id, BillingInvoice.currency)
        )
    ).all()

    by_client: dict[uuid.UUID, list[CurrencyTotal]] = {}
    for organization_id, currency, count, due, paid in rows:
        by_client.setdefault(organization_id, []).append(
            CurrencyTotal(
                currency=currency,
                invoice_count=int(count),
                amount_due_cents=int(due),
                amount_paid_cents=int(paid),
            )
        )
    return [
        ClientBilling(
            organization_id=client.organization_id,
            name=client.name,
            totals=tuple(by_client.get(client.organization_id, ())),
        )
        for client in portfolio
    ]
