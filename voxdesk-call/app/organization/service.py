"""Organization lifecycle.

Creating an organization does not create a tenant. Attaching a tenant is
``app.tenancy.service``. Nothing here deletes users, calls, invoices or the
tenant row.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Organization, Tenant
from app.organization import events
from app.organization.models import InputError, OrganizationStatus, normalize_slug, slug_from_name, validate_name
from app.organization.policies import (
    assert_metadata_writable,
    assert_transition,
    parse_status,
)
from app.tenancy.isolation import Conflict, NotFound, ValidationFailed


class Actor:
    """Who performed a hierarchy change. Built by a route, never from a body claim."""

    def __init__(
        self,
        *,
        user_id: uuid.UUID | None = None,
        email: str = "",
        tenant_id: uuid.UUID | None = None,
        ip_address: str = "",
        user_agent: str = "",
    ) -> None:
        self.user_id = user_id
        self.email = email
        self.tenant_id = tenant_id
        self.ip_address = ip_address
        self.user_agent = user_agent


def _actor_kwargs(actor: Actor | None) -> dict:
    actor = actor or Actor()
    return {
        "tenant_id": actor.tenant_id,
        "actor_user_id": actor.user_id,
        "actor_email": actor.email,
        "ip_address": actor.ip_address,
        "user_agent": actor.user_agent,
    }


async def _slug_available(session: AsyncSession, slug: str) -> bool:
    found = await session.scalar(select(Organization.id).where(Organization.slug == slug))
    return found is None


async def allocate_slug(session: AsyncSession, name: str, requested: str | None) -> str:
    if requested:
        slug = _clean_slug(requested)
        if not await _slug_available(session, slug):
            raise Conflict("That slug is not available")
        return slug
    base = slug_from_name(name)
    candidate = base
    for _ in range(6):
        if await _slug_available(session, candidate):
            return candidate
        candidate = f"{base[:58].rstrip('-')}-{uuid.uuid4().hex[:4]}"
    raise Conflict("Could not allocate a unique organization slug")


def _clean_slug(value: str) -> str:
    try:
        return normalize_slug(value)
    except InputError as exc:
        raise ValidationFailed(str(exc)) from exc


def _clean_name(value: str) -> str:
    try:
        return validate_name(value)
    except InputError as exc:
        raise ValidationFailed(str(exc)) from exc


async def create_organization(
    session: AsyncSession,
    *,
    name: str,
    slug: str | None = None,
    actor: Actor | None = None,
    commit: bool = False,
) -> Organization:
    clean_name = _clean_name(name)
    clean_slug = await allocate_slug(session, clean_name, slug)
    row = Organization(
        name=clean_name,
        slug=clean_slug,
        status=OrganizationStatus.ACTIVE.value,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("That slug is not available") from exc
    await events.organization_event(
        session,
        kind="created",
        organization_id=row.id,
        status=row.status,
        commit=False,
        **_actor_kwargs(actor),
    )
    if commit:
        await session.commit()
        await session.refresh(row)
    return row


async def get_organization(
    session: AsyncSession, organization_id: uuid.UUID
) -> Organization | None:
    return await session.get(Organization, organization_id)


async def require_organization(
    session: AsyncSession, organization_id: uuid.UUID
) -> Organization:
    row = await get_organization(session, organization_id)
    if row is None:
        raise NotFound()
    return row


async def update_organization(
    session: AsyncSession,
    organization: Organization,
    *,
    name: str,
    actor: Actor | None = None,
    commit: bool = False,
) -> Organization:
    assert_metadata_writable(organization.status)
    organization.name = _clean_name(name)
    await session.flush()
    await events.organization_event(
        session,
        kind="updated",
        organization_id=organization.id,
        status=organization.status,
        commit=False,
        **_actor_kwargs(actor),
    )
    if commit:
        await session.commit()
        await session.refresh(organization)
    return organization


async def transition_organization(
    session: AsyncSession,
    organization: Organization,
    target: str,
    *,
    actor: Actor | None = None,
    commit: bool = False,
) -> Organization:
    """Move status. The same status twice is a no-op and writes no second audit row."""
    desired = parse_status(target)
    assert_transition(organization.status, desired)
    if organization.status == desired:
        return organization
    organization.status = desired
    await session.flush()
    kind = {
        OrganizationStatus.SUSPENDED.value: "suspended",
        OrganizationStatus.ACTIVE.value: "restored",
        OrganizationStatus.READ_ONLY.value: "read_only",
        OrganizationStatus.DELETED.value: "updated",
    }[desired]
    # Deletion is a lifecycle write, recorded as an update with the new status
    # so we do not invent a hard-delete event. The status in the detail is
    # ``deleted``.
    if desired == OrganizationStatus.DELETED.value:
        kind = "updated"
    await events.organization_event(
        session,
        kind=kind,
        organization_id=organization.id,
        status=organization.status,
        commit=False,
        **_actor_kwargs(actor),
    )
    if commit:
        await session.commit()
        await session.refresh(organization)
    return organization


async def list_tenants(session: AsyncSession, organization_id: uuid.UUID) -> list[Tenant]:
    """Every tenant in the organization. HTTP routes must filter this by membership."""
    rows = await session.scalars(
        select(Tenant).where(Tenant.organization_id == organization_id).order_by(Tenant.created_at)
    )
    return list(rows)
