"""Environment lifecycle inside one tenant.

Kind is fixed at creation. ``tenant_id`` is taken from the authorized tenant,
never from a request body. Production cannot be duplicated or archived.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Environment, Organization, Tenant
from app.environments import events
from app.environments.deployment import DeploymentState, apply_record, view_from_environment
from app.environments.models import (
    DEFAULT_NAME,
    DEFAULT_SLUG,
    EnvironmentKind,
    EnvironmentStatus,
    InputError,
    normalize_slug,
    parse_kind,
    slug_from_name,
    validate_name,
)
from app.environments.policies import (
    assert_can_archive,
    assert_default_allowed,
    assert_kind_available,
    assert_transition,
    baseline_for,
)
from app.organization.policies import assert_structure_writable
from app.organization.service import Actor
from app.tenancy.isolation import Conflict, LifecycleDenied, NotFound, ValidationFailed
from app.tenancy.lifecycle import assert_writable


def _actor_kwargs(actor: Actor | None, tenant_id: uuid.UUID) -> dict:
    actor = actor or Actor()
    return {
        "tenant_id": tenant_id,
        "actor_user_id": actor.user_id,
        "actor_email": actor.email,
        "ip_address": actor.ip_address,
        "user_agent": actor.user_agent,
    }


def _clean_name(value: str) -> str:
    try:
        return validate_name(value)
    except InputError as exc:
        raise ValidationFailed(str(exc)) from exc


def _clean_kind(value: str) -> str:
    try:
        return parse_kind(value)
    except InputError as exc:
        raise ValidationFailed(str(exc)) from exc


async def list_environments(session: AsyncSession, tenant_id: uuid.UUID) -> list[Environment]:
    rows = await session.scalars(
        select(Environment).where(Environment.tenant_id == tenant_id).order_by(Environment.created_at)
    )
    return list(rows)


async def get_environment(
    session: AsyncSession, environment_id: uuid.UUID
) -> Environment | None:
    return await session.get(Environment, environment_id)


async def require_environment(
    session: AsyncSession, environment_id: uuid.UUID, tenant_id: uuid.UUID
) -> Environment:
    row = await get_environment(session, environment_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def _existing_kinds(session: AsyncSession, tenant_id: uuid.UUID) -> set[str]:
    rows = await session.scalars(
        select(Environment.kind).where(Environment.tenant_id == tenant_id)
    )
    return set(rows)


async def create_environment(
    session: AsyncSession,
    tenant: Tenant,
    organization: Organization,
    *,
    name: str,
    kind: str,
    slug: str | None = None,
    actor: Actor | None = None,
    commit: bool = False,
) -> Environment:
    assert_structure_writable(organization.status)
    assert_writable(tenant)
    clean_kind = _clean_kind(kind)
    assert_kind_available(clean_kind, await _existing_kinds(session, tenant.id))
    clean_name = _clean_name(name)
    if slug:
        try:
            clean_slug = normalize_slug(slug)
        except InputError as exc:
            raise ValidationFailed(str(exc)) from exc
    else:
        clean_slug = slug_from_name(clean_name)
        if clean_kind == EnvironmentKind.PRODUCTION.value:
            clean_slug = DEFAULT_SLUG
    taken = await session.scalar(
        select(Environment.id).where(
            Environment.tenant_id == tenant.id,
            Environment.slug == clean_slug,
        )
    )
    if taken is not None:
        raise Conflict("That environment slug is already used in this tenant")
    is_production = clean_kind == EnvironmentKind.PRODUCTION.value
    row = Environment(
        tenant_id=tenant.id,
        name=clean_name if not is_production else (clean_name or DEFAULT_NAME),
        slug=clean_slug,
        kind=clean_kind,
        status=EnvironmentStatus.ACTIVE.value,
        is_default=False,
        production_guard=tenant.id if is_production else None,
        default_guard=None,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("That environment already exists for this tenant") from exc
    await events.environment_event(
        session,
        kind="created",
        environment_id=row.id,
        status=row.status,
        environment_kind=row.kind,
        commit=False,
        **_actor_kwargs(actor, tenant.id),
    )
    if commit:
        await session.commit()
        await session.refresh(row)
    return row


async def update_environment(
    session: AsyncSession,
    environment: Environment,
    tenant: Tenant,
    *,
    name: str,
    actor: Actor | None = None,
    commit: bool = False,
) -> Environment:
    """Rename only. Kind and tenant are not updatable fields."""
    assert_writable(tenant)
    if environment.status == EnvironmentStatus.ARCHIVED.value:
        raise LifecycleDenied("An archived environment cannot be renamed")
    environment.name = _clean_name(name)
    await session.flush()
    await events.environment_event(
        session,
        kind="updated",
        environment_id=environment.id,
        status=environment.status,
        environment_kind=environment.kind,
        commit=False,
        **_actor_kwargs(actor, tenant.id),
    )
    if commit:
        await session.commit()
        await session.refresh(environment)
    return environment


async def transition_environment(
    session: AsyncSession,
    environment: Environment,
    tenant: Tenant,
    target: str,
    *,
    actor: Actor | None = None,
    commit: bool = False,
) -> Environment:
    try:
        from app.environments.models import parse_status
        desired = parse_status(target)
    except InputError as exc:
        raise ValidationFailed(str(exc)) from exc
    assert_transition(environment.status, desired)
    if desired == EnvironmentStatus.ARCHIVED.value:
        assert_can_archive(environment.kind)
    if environment.status == desired:
        return environment
    if desired == EnvironmentStatus.ARCHIVED.value and environment.is_default:
        raise LifecycleDenied("The default environment cannot be archived; choose another default first")
    environment.status = desired
    await session.flush()
    kind = {
        EnvironmentStatus.SUSPENDED.value: "suspended",
        EnvironmentStatus.ACTIVE.value: "restored",
        EnvironmentStatus.ARCHIVED.value: "archived",
    }[desired]
    await events.environment_event(
        session,
        kind=kind,
        environment_id=environment.id,
        status=environment.status,
        environment_kind=environment.kind,
        commit=False,
        **_actor_kwargs(actor, tenant.id),
    )
    if commit:
        await session.commit()
        await session.refresh(environment)
    return environment


async def set_default(
    session: AsyncSession,
    environment: Environment,
    tenant: Tenant,
    *,
    actor: Actor | None = None,
    commit: bool = False,
) -> Environment:
    assert_writable(tenant)
    assert_default_allowed(environment.status)
    if environment.is_default:
        return environment
    current = (
        await session.scalars(
            select(Environment).where(
                Environment.tenant_id == tenant.id,
                Environment.is_default.is_(True),
            )
        )
    ).all()
    for other in current:
        other.is_default = False
        other.default_guard = None
    await session.flush()
    environment.is_default = True
    environment.default_guard = tenant.id
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict("Could not change the default environment") from exc
    await events.environment_event(
        session,
        kind="default_changed",
        environment_id=environment.id,
        status=environment.status,
        environment_kind=environment.kind,
        commit=False,
        **_actor_kwargs(actor, tenant.id),
    )
    if commit:
        await session.commit()
        await session.refresh(environment)
    return environment


async def record_deployment(
    session: AsyncSession,
    environment: Environment,
    tenant: Tenant,
    *,
    release_version: str,
    source: str = "",
    status: str = "recorded",
    health_state: str = "unknown",
    deployed_at: datetime | None = None,
    actor: Actor | None = None,
    commit: bool = False,
) -> DeploymentState:
    """Record a deployment label. Does not roll anything out."""
    from datetime import datetime

    assert_writable(tenant)
    if environment.status == EnvironmentStatus.ARCHIVED.value:
        raise LifecycleDenied("An archived environment cannot record a deployment")
    state = apply_record(
        environment,
        release_version=release_version,
        source=source,
        status=status,
        health_state=health_state,
        deployed_at=deployed_at,
    )
    await session.flush()
    await events.environment_event(
        session,
        kind="updated",
        environment_id=environment.id,
        status=environment.status,
        environment_kind=environment.kind,
        commit=False,
        **_actor_kwargs(actor, tenant.id),
    )
    if commit:
        await session.commit()
        await session.refresh(environment)
    return state


def deployment_of(environment: Environment) -> DeploymentState:
    return view_from_environment(environment)


def describe_baseline(kind: str) -> dict:
    baseline = baseline_for(kind)
    return {
        "kind": baseline.kind,
        "production_constraints": baseline.production_constraints,
        "allows_debug_data": baseline.allows_debug_data,
        "isolated_from_production_identity": baseline.isolated_from_production_identity,
        "summary": baseline.summary,
        "executes_deployment": False,
    }
