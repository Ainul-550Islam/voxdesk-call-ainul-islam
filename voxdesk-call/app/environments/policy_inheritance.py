"""Effective policy from organization, tenant and environment.

The base is the existing identity policy for the tenant. Overlays may only
make mandatory controls stricter. A child that tries to turn a required
control off is refused at write time, and a read still clamps the result so a
stored weaker value cannot win.

Suspended or deleted parents invalidate child authorization. This does not
replace the identity policy evaluator.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.policies import ResolvedPolicy, load_policy
from app.db.models import Environment, Organization, ScopePolicy, Tenant
from app.tenancy.isolation import LifecycleDenied

_TRUE_STRICTER = (
    "mfa_required",
    "mfa_required_for_admins",
    "sso_required",
)
_FALSE_STRICTER = (
    "password_login_allowed",
    "api_keys_allowed",
    "service_accounts_allowed",
)
_LOWER_STRICTER = ("session_idle_minutes", "session_max_active")


@dataclass(frozen=True)
class EffectivePolicy:
    tenant_id: uuid.UUID
    organization_id: uuid.UUID | None
    environment_id: uuid.UUID | None
    authorization_valid: bool
    invalid_reason: str
    mfa_required: bool
    mfa_required_for_admins: bool
    sso_required: bool
    password_login_allowed: bool
    api_keys_allowed: bool
    service_accounts_allowed: bool
    session_idle_minutes: int
    session_max_active: int
    source: str


def _clamp_bool_true(parent: bool, child: bool | None) -> bool:
    if child is None:
        return parent
    return parent or child


def _clamp_bool_false(parent: bool, child: bool | None) -> bool:
    """False is the stricter value for an allow-flag."""
    if child is None:
        return parent
    return parent and child


def _clamp_lower(parent: int, child: int | None) -> int:
    if child is None:
        return parent
    return min(parent, child)


def _overlay_values(row: ScopePolicy | None) -> dict:
    if row is None:
        return {}
    return {
        "mfa_required": row.mfa_required,
        "mfa_required_for_admins": row.mfa_required_for_admins,
        "sso_required": row.sso_required,
        "password_login_allowed": row.password_login_allowed,
        "api_keys_allowed": row.api_keys_allowed,
        "service_accounts_allowed": row.service_accounts_allowed,
        "session_idle_minutes": row.session_idle_minutes,
        "session_max_active": row.session_max_active,
    }


def merge(base: ResolvedPolicy, *overlays: dict) -> dict:
    """Fold overlays onto the identity policy. Stricter parent values win."""
    current = {
        "mfa_required": base.mfa_required,
        "mfa_required_for_admins": base.mfa_required_for_admins,
        "sso_required": base.sso_required,
        "password_login_allowed": base.password_login_allowed,
        "api_keys_allowed": base.api_keys_allowed,
        "service_accounts_allowed": base.service_accounts_allowed,
        "session_idle_minutes": base.session_idle_minutes,
        "session_max_active": base.session_max_active,
    }
    for overlay in overlays:
        for name in _TRUE_STRICTER:
            current[name] = _clamp_bool_true(current[name], overlay.get(name))
        for name in _FALSE_STRICTER:
            current[name] = _clamp_bool_false(current[name], overlay.get(name))
        for name in _LOWER_STRICTER:
            current[name] = _clamp_lower(current[name], overlay.get(name))
    return current


def weakens(parent: dict, proposed: dict) -> bool:
    """True when ``proposed`` would loosen a mandatory control already set."""
    for name in _TRUE_STRICTER:
        if name in proposed and proposed[name] is False and parent[name] is True:
            return True
    for name in _FALSE_STRICTER:
        if name in proposed and proposed[name] is True and parent[name] is False:
            return True
    for name in _LOWER_STRICTER:
        value = proposed.get(name)
        if value is not None and value > parent[name]:
            return True
    return False


async def _row(
    session: AsyncSession, kind: str, scope_id: uuid.UUID | None
) -> ScopePolicy | None:
    if scope_id is None:
        return None
    return (
        await session.execute(
            select(ScopePolicy).where(
                ScopePolicy.scope_kind == kind,
                ScopePolicy.scope_id == scope_id,
            )
        )
    ).scalar_one_or_none()


async def resolve_effective(
    session: AsyncSession,
    tenant: Tenant,
    environment: Environment | None = None,
) -> EffectivePolicy:
    base = await load_policy(session, tenant.id)
    organization = None
    if tenant.organization_id is not None:
        organization = await session.get(Organization, tenant.organization_id)
    org_row = await _row(session, "organization", tenant.organization_id)
    tenant_row = await _row(session, "tenant", tenant.id)
    env_row = await _row(
        session, "environment", environment.id if environment is not None else None
    )
    merged = merge(
        base,
        _overlay_values(org_row),
        _overlay_values(tenant_row),
        _overlay_values(env_row),
    )
    invalid_reason = ""
    if organization is not None and organization.status in ("suspended", "deleted"):
        invalid_reason = "organization_" + organization.status
    elif tenant.lifecycle_status in ("suspended", "deleted"):
        invalid_reason = "tenant_" + tenant.lifecycle_status
    elif environment is not None and environment.status in ("suspended", "archived"):
        invalid_reason = "environment_" + environment.status
    return EffectivePolicy(
        tenant_id=tenant.id,
        organization_id=tenant.organization_id,
        environment_id=environment.id if environment is not None else None,
        authorization_valid=invalid_reason == "",
        invalid_reason=invalid_reason,
        source="identity+hierarchy",
        **merged,
    )


async def set_scope_policy(
    session: AsyncSession,
    *,
    kind: str,
    scope_id: uuid.UUID,
    tenant: Tenant,
    environment: Environment | None = None,
    values: dict,
) -> ScopePolicy:
    """Persist an overlay only when it does not weaken the parent."""
    parent = await resolve_effective(
        session, tenant, None if kind == "environment" else environment
    )
    parent_values = {
        "mfa_required": parent.mfa_required,
        "mfa_required_for_admins": parent.mfa_required_for_admins,
        "sso_required": parent.sso_required,
        "password_login_allowed": parent.password_login_allowed,
        "api_keys_allowed": parent.api_keys_allowed,
        "service_accounts_allowed": parent.service_accounts_allowed,
        "session_idle_minutes": parent.session_idle_minutes,
        "session_max_active": parent.session_max_active,
    }
    if weakens(parent_values, values):
        raise LifecycleDenied("A child policy cannot weaken a mandatory parent control")
    row = await _row(session, kind, scope_id)
    if row is None:
        row = ScopePolicy(scope_kind=kind, scope_id=scope_id)
        session.add(row)
    for name in (*_TRUE_STRICTER, *_FALSE_STRICTER, *_LOWER_STRICTER):
        if name in values:
            setattr(row, name, values[name])
    await session.commit()
    await session.refresh(row)
    return row
