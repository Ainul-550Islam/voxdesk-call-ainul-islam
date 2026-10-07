"""Tenant operations inside an organization.

The insert hook in ``app.db.models`` is the compatibility path for every
existing ``Tenant(...)`` caller. This module is the explicit path: it sets
``organization_id`` itself so the hook does not invent a second organization,
then records the production environment the hook created.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit
from app.db.models import AuditAction, Environment, Organization, Tenant
from app.environments import events as environment_events
from app.organization.models import InputError, validate_name
from app.organization.policies import assert_can_add_tenant
from app.organization.service import Actor
from app.tenancy.isolation import Conflict, LifecycleDenied, NotFound, ValidationFailed
from app.tenancy.lifecycle import apply_status, assert_writable


def _actor_kwargs(actor: Actor | None) -> dict:
    actor = actor or Actor()
    return {
        "actor_user_id": actor.user_id,
        "actor_email": actor.email,
        "ip_address": actor.ip_address,
        "user_agent": actor.user_agent,
    }


async def open_legacy_tenant(session: AsyncSession, fields: dict) -> Tenant:
    """Operator/seed-shaped create. Ignores any client organization or environment id.

    The before-insert hook attaches one organization and one production
    environment. This function does not trust a caller who stuffed those ids
    into the payload.
    """
    payload = dict(fields)
    payload.pop("organization_id", None)
    payload.pop("environment_id", None)
    payload.pop("lifecycle_status", None)
    tenant = Tenant(**payload)
    session.add(tenant)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("That tenant could not be created") from exc
    return tenant


async def create_tenant_under_organization(
    session: AsyncSession,
    organization: Organization,
    *,
    name: str,
    twilio_number: str,
    actor: Actor | None = None,
    commit: bool = False,
    industry: str = "general",
) -> Tenant:
    """Create a tenant that already belongs to ``organization``.

    Refuses a suspended, read-only or deleted parent. Does not accept an
    organization id from the caller beyond the organization object the route
    already authorized — and the HTTP route that exposes this is platform-only.
    """
    assert_can_add_tenant(organization.status)
    try:
        clean_name = validate_name(name)
    except InputError as exc:
        raise ValidationFailed(str(exc)) from exc
    number = (twilio_number or "").strip()
    if not number or len(number) > 32:
        raise ValidationFailed("A tenant phone number is required")
    tenant = Tenant(
        name=clean_name,
        twilio_number=number,
        industry=industry or "general",
        organization_id=organization.id,
        lifecycle_status="active",
    )
    session.add(tenant)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("That phone number is not available") from exc
    await emit(
        session,
        AuditAction.TENANT_LIFECYCLE_CHANGED,
        tenant_id=tenant.id,
        detail={
            "tenant_id": str(tenant.id),
            "organization_id": str(organization.id),
            "status": tenant.lifecycle_status,
            "operation": "created",
            "event": "tenant.created",
        },
        commit=False,
        **_actor_kwargs(actor),
    )
    production = await session.scalar(
        select(Environment).where(
            Environment.tenant_id == tenant.id,
            Environment.kind == "production",
        )
    )
    if production is not None:
        await environment_events.environment_event(
            session,
            kind="created",
            environment_id=production.id,
            status=production.status,
            environment_kind=production.kind,
            commit=False,
            **_actor_kwargs(actor),
            tenant_id=tenant.id,
        )
    if commit:
        await session.commit()
        await session.refresh(tenant)
    return tenant


async def require_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> Tenant:
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFound()
    return tenant


async def organization_for(session: AsyncSession, tenant: Tenant) -> Organization:
    if tenant.organization_id is None:
        raise NotFound()
    organization = await session.get(Organization, tenant.organization_id)
    if organization is None:
        raise NotFound()
    return organization


def visible_tenants(actor_tenant: Tenant, organization_id: uuid.UUID) -> list[Tenant]:
    """The tenants this principal may see: their own, and only inside this org.

    Sibling tenants in the same organization are not listed. Membership is
    still one user, one tenant.
    """
    if actor_tenant.organization_id != organization_id:
        raise NotFound()
    return [actor_tenant]


async def update_profile(
    session: AsyncSession,
    tenant: Tenant,
    *,
    name: str,
    actor: Actor | None = None,
    commit: bool = False,
) -> Tenant:
    organization = await organization_for(session, tenant)
    if organization.status == "deleted":
        raise LifecycleDenied("Organization is deleted")
    assert_writable(tenant)
    try:
        tenant.name = validate_name(name)
    except InputError as exc:
        raise ValidationFailed(str(exc)) from exc
    await session.flush()
    await emit(
        session,
        AuditAction.TENANT_LIFECYCLE_CHANGED,
        tenant_id=tenant.id,
        detail={
            "tenant_id": str(tenant.id),
            "organization_id": str(tenant.organization_id),
            "status": tenant.lifecycle_status,
            "operation": "profile_updated",
            "event": "tenant.updated",
        },
        commit=False,
        **_actor_kwargs(actor),
    )
    if commit:
        await session.commit()
        await session.refresh(tenant)
    return tenant


async def transition_tenant(
    session: AsyncSession,
    tenant: Tenant,
    target: str,
    *,
    actor: Actor | None = None,
    commit: bool = False,
) -> Tenant:
    organization = await organization_for(session, tenant)
    if organization.status == "deleted":
        raise LifecycleDenied("Organization is deleted")
    changed = apply_status(tenant, target)
    if not changed:
        return tenant
    await session.flush()
    await emit(
        session,
        AuditAction.TENANT_LIFECYCLE_CHANGED,
        tenant_id=tenant.id,
        detail={
            "tenant_id": str(tenant.id),
            "organization_id": str(tenant.organization_id),
            "status": tenant.lifecycle_status,
            "operation": "transition",
            "event": "tenant.lifecycle_changed",
        },
        commit=False,
        **_actor_kwargs(actor),
    )
    if commit:
        await session.commit()
        await session.refresh(tenant)
    return tenant


def public_settings(tenant: Tenant) -> dict:
    """Redacted settings. Secrets stay on the row and out of this dict."""
    from app.tenancy.settings import public_view

    return public_view(tenant)
