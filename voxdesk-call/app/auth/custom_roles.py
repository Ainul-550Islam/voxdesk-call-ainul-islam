"""Custom roles: modules, grants, and the privilege-escalation guard (PART 16).

A custom role is a *named bundle of existing permissions*. It cannot invent a
capability, and it cannot take one away:

    effective = built-in role permissions (rbac.ROLE_PERMISSIONS)
              ∪ permissions of every active custom role assigned to the subject

Deny-by-default falls out of that union: a subject with no custom role is
exactly as capable as its built-in role, and a custom role with no permissions
grants nothing at all.

There is no third role system here. `/api/auth/roles` already exposes the
built-in roles; this module adds one more *kind* of grant on top, using the same
`app.auth.permissions.Permission` vocabulary that every route already asks for.
`app/tenancy/roles.py` and `app/organization/roles.py` stay what they are —
aliases for the `UserRole` enum — and are not duplicated.

Two rules keep the feature from becoming a hole:

1. **Nobody grants what they lack.** ``assert_grantable`` refuses a role whose
   permissions are not a subset of the actor's own effective permissions, and
   refuses platform-only permissions (``tenant:create``/``tenant:delete``)
   outright — those are operator actions, not customer actions.
2. **A grant is tenant-scoped twice.** The role row carries ``tenant_id`` and so
   does the assignment, and ``load_grants`` filters on both, so a grant id from
   another tenant resolves to nothing rather than to a role.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permission
from app.auth.rbac import is_platform_permission, permissions_for
from app.db.models import UserRole

#: A module is the part of a permission id before the colon: `call:read` lives in
#: `call`. The registry makes the dashboard's editor organise itself, and the
#: guard below fails a test when a new permission appears without a module.
MODULES: dict[str, dict[str, str]] = {
    "tenant": {"label": "Workspace", "description": "Business profile, voice tuning, hours."},
    "user": {"label": "Team", "description": "People, roles and invitations."},
    "call": {"label": "Calls", "description": "Call control and call records."},
    "transcript": {"label": "Transcripts", "description": "Reading what was said."},
    "recording": {"label": "Recordings", "description": "Audio access."},
    "lead": {"label": "Leads", "description": "Pipeline records."},
    "appointment": {"label": "Appointments", "description": "The booking calendar."},
    "campaign": {"label": "Campaigns", "description": "Outbound campaigns and spending."},
    "knowledge": {"label": "Knowledge", "description": "Documents behind retrieval."},
    "billing": {"label": "Billing", "description": "Invoices and plan changes."},
    "analytics": {"label": "Analytics", "description": "Dashboards and exports."},
    "audit": {"label": "Audit", "description": "The audit trail."},
    "compliance": {"label": "Compliance", "description": "Retention, residency, erasure."},
    "qa": {"label": "Quality", "description": "Scoring, review and finalisation."},
    "governance": {"label": "Governance", "description": "Model and policy approval."},
    "integration": {"label": "Integrations", "description": "CRM and third-party connections."},
    "identity": {"label": "Identity", "description": "SSO, SCIM and MFA configuration."},
    "queue": {"label": "Queues", "description": "Routing queues and members."},
    "skill": {"label": "Skills", "description": "Routing skills."},
    "routing": {"label": "Routing", "description": "How calls are matched to people."},
    "supervisor": {"label": "Supervision", "description": "Live monitor, whisper, takeover."},
    "agent": {"label": "Agents", "description": "Agent configuration and versions."},
    "agent_state": {"label": "Agent states", "description": "Availability and break reasons."},
    "assist": {"label": "Agent assist", "description": "Suggestions and compliance scripts."},
    "dialer": {"label": "Dialer", "description": "Pacing policy and campaigns in progress."},
    "softphone": {"label": "Softphone", "description": "The browser phone."},
    "voice": {"label": "Voice", "description": "Voice profiles and cloning."},
    "connector": {"label": "Connectors", "description": "Tool connectors."},
    "mcp": {"label": "MCP", "description": "Model-context tool servers."},
    "api_tool": {"label": "API tools", "description": "Custom HTTP tools."},
    "email": {"label": "Email", "description": "Outbound email."},
    "approval": {"label": "Approvals", "description": "The approval queue."},
    "secret": {"label": "Secrets", "description": "Stored credentials."},
    "api_key": {"label": "API keys", "description": "Machine credentials."},
    "service_account": {"label": "Service accounts", "description": "Non-human identities."},
    "workflow": {"label": "Workflows", "description": "Post-call and pre-call automation."},
    "tool": {"label": "Tools", "description": "Agent tool bindings."},
    "number": {"label": "Phone numbers", "description": "Numbers and their policies."},
    "transfer": {"label": "Transfers", "description": "Warm and cold transfer."},
    "export": {"label": "Exports", "description": "Bulk data out of the product."},
    "notification": {"label": "Notifications", "description": "Alert delivery settings."},
    "widget": {"label": "Web widget", "description": "The embeddable widget."},
    "share": {"label": "Share links", "description": "Public read-only links."},
    "platform": {"label": "Platform", "description": "Operator-only actions."},
    "brand": {"label": "White-label", "description": "Domains and branding."},
    "domain": {"label": "Domains", "description": "Custom hostnames."},
    "messaging": {"label": "Messaging", "description": "SMS and A2P registration."},
    "mobile": {"label": "Mobile", "description": "The mobile SDK surface."},
    "security": {"label": "Security", "description": "Security settings."},
    "telephony": {"label": "Telephony", "description": "Carrier and trunk configuration."},
    "wfm": {"label": "Workforce", "description": "Forecast, schedule and publish."},
}

#: Prefixes that belong to an existing module instead of getting one of their own.
#: `calls:debug` is a call permission, `voice_clone:read` a voice permission, and
#: `apikey:manage` has been spelled `API_KEY_MANAGE` since before the prefix
#: convention existed. Aliasing them keeps the editor to ~45 readable groups
#: instead of ~85 one-permission rows.
MODULE_ALIASES: dict[str, str] = {
    "apikey": "api_key",
    "calls": "call",
    "voice_profile": "voice",
    "voice_clone": "voice",
    "compliance_access_review": "compliance",
    "compliance_audit": "compliance",
    "compliance_break_glass": "compliance",
    "compliance_dsar": "compliance",
    "compliance_evidence": "compliance",
    "compliance_incident": "compliance",
    "compliance_kms": "compliance",
    "compliance_phi_policy": "compliance",
    "compliance_residency": "compliance",
    "compliance_siem": "compliance",
}

#: The catch-all bucket. A permission whose prefix has no entry lands here, and
#: ``ungrouped_permissions()`` names it rather than hiding it — a test asserts
#: this list is empty, so a new permission cannot ship without a module.
FALLBACK_MODULE = "other"


class CustomRoleError(ValueError):
    """A role definition or grant that policy refuses."""


def module_of(permission: Permission) -> str:
    prefix = str(permission.value).split(":", 1)[0]
    prefix = MODULE_ALIASES.get(prefix, prefix)
    return prefix if prefix in MODULES else FALLBACK_MODULE


def ungrouped_permissions() -> list[str]:
    """Permissions whose module is missing from ``MODULES``. A test keeps this empty."""
    return sorted(p.value for p in Permission if module_of(p) == FALLBACK_MODULE)


def catalogue() -> list[dict[str, object]]:
    """Permissions grouped by module, for the editor and for previews."""
    grouped: dict[str, list[Permission]] = {}
    for permission in Permission:
        grouped.setdefault(module_of(permission), []).append(permission)
    rows: list[dict[str, object]] = []
    for key in sorted(grouped, key=lambda name: (name == FALLBACK_MODULE, name)):
        meta = MODULES.get(key, {"label": key.title(), "description": ""})
        rows.append(
            {
                "module": key,
                "label": meta["label"],
                "description": meta["description"],
                "permissions": sorted(p.value for p in grouped[key]),
            }
        )
    return rows


def parse_permissions(values: Iterable[str]) -> frozenset[Permission]:
    """Turn permission ids into `Permission`s, refusing anything unknown.

    An unknown id is an error, not a silently dropped entry: a role that saves
    with a typo and then grants nothing is a role that lies about what it does.
    """
    parsed: set[Permission] = set()
    unknown: list[str] = []
    for raw in values:
        text = str(raw).strip()
        if not text:
            continue
        try:
            parsed.add(Permission(text))
        except ValueError:
            unknown.append(text)
    if unknown:
        raise CustomRoleError(f"unknown permission(s): {', '.join(sorted(set(unknown)))}")
    return frozenset(parsed)


def platform_permissions_requested(values: Iterable[str]) -> list[str]:
    """Platform-only ids in a requested set. Never grantable to a customer role."""
    blocked: list[str] = []
    for raw in values:
        try:
            permission = Permission(str(raw).strip())
        except ValueError:
            continue
        if is_platform_permission(permission):
            blocked.append(permission.value)
    return sorted(set(blocked))


def assert_grantable(
    actor_permissions: Iterable[Permission],
    requested: Iterable[str],
) -> frozenset[Permission]:
    """Refuse a role (or grant) that hands out something the actor does not hold.

    Self-escalation is the whole risk of a custom-role feature: an admin who can
    write a role can otherwise mint one with `tenant:update` and assign it to
    themselves. Two checks, both cheap and both deterministic:

    1. no platform-only permission, ever;
    2. every requested permission must already be held by the actor.
    """
    platform = platform_permissions_requested(requested)
    if platform:
        raise CustomRoleError(
            "platform permissions cannot be granted to a tenant role: " + ", ".join(platform)
        )
    held = frozenset(actor_permissions)
    requested_set = parse_permissions(requested)
    missing = sorted(p.value for p in requested_set - held)
    if missing:
        raise CustomRoleError(
            "cannot grant a permission you do not hold: " + ", ".join(missing)
        )
    return requested_set


def effective_permissions(
    role: UserRole,
    extra: Iterable[Permission] = (),
) -> frozenset[Permission]:
    """Built-in role ∪ custom-role grants. Additive by design."""
    return frozenset(permissions_for(role)) | frozenset(extra)


def describe_effective(
    role: UserRole,
    extra: Iterable[Permission] = (),
    *,
    blocked_by_scope: Iterable[Permission] = (),
) -> list[str]:
    """Sorted ids the subject holds, minus anything a machine scope removed."""
    granted = effective_permissions(role, extra)
    return sorted(p.value for p in granted - frozenset(blocked_by_scope))


# --------------------------------------------------------------------- grants ---


@dataclass
class GrantedRole:
    role_id: str
    name: str
    permissions: frozenset[Permission] = field(default_factory=frozenset)

    def as_dict(self) -> dict[str, object]:
        return {
            "role_id": self.role_id,
            "name": self.name,
            "permissions": sorted(p.value for p in self.permissions),
        }


#: Why a cache at all: `get_context` runs on every authenticated request, and
#: reading two tables there would be a tax on the whole product to serve a
#: feature most tenants never switch on. The cache is keyed by tenant and holds
#: the *assignments*, not the answer, so a per-subject lookup is a dict read.
#: Invalidation is explicit on the write path in this process, and every entry
#: expires after ``CACHE_TTL_SECONDS`` so a change made by another worker is
#: visible within that window rather than never.
CACHE_TTL_SECONDS = 30.0
_CACHE: dict[str, tuple[float, dict[tuple[str, str], list[GrantedRole]]]] = {}


def invalidate_cache(tenant_id: object | None = None) -> None:
    """Drop cached grants. Called by every write in `role_routes`."""
    if tenant_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(str(tenant_id), None)


def _cache_get(tenant_id: object) -> dict[tuple[str, str], list[GrantedRole]] | None:
    entry = _CACHE.get(str(tenant_id))
    if entry is None:
        return None
    stamp, value = entry
    if time.monotonic() - stamp > CACHE_TTL_SECONDS:
        _CACHE.pop(str(tenant_id), None)
        return None
    return value


async def load_grant_map(
    session: AsyncSession,
    tenant_id: object,
) -> dict[tuple[str, str], list[GrantedRole]]:
    """(subject_type, subject_id) → granted roles, for one tenant."""
    cached = _cache_get(tenant_id)
    if cached is not None:
        return cached

    from app.db.role_models import CustomRole, CustomRoleAssignment

    rows = (
        await session.execute(
            select(CustomRoleAssignment, CustomRole)
            .join(CustomRole, CustomRole.id == CustomRoleAssignment.role_id)
            .where(
                CustomRoleAssignment.tenant_id == tenant_id,
                CustomRole.tenant_id == tenant_id,
                CustomRole.is_active.is_(True),
            )
        )
    ).all()

    grants: dict[tuple[str, str], list[GrantedRole]] = {}
    for assignment, role in rows:
        key = (assignment.subject_type, assignment.subject_id)
        grants.setdefault(key, []).append(
            GrantedRole(
                role_id=str(role.id),
                name=role.name,
                permissions=parse_permissions(role.permission_ids()),
            )
        )
    _CACHE[str(tenant_id)] = (time.monotonic(), grants)
    return grants


async def grants_for_user(
    session: AsyncSession,
    tenant_id: object,
    *,
    user_id: object | None,
    service_account_id: object | None = None,
) -> tuple[list[GrantedRole], frozenset[Permission]]:
    """Roles a subject holds directly, plus any held through a SCIM group.

    Group membership is read from `scim_group_mappings.member_user_ids`, which is
    the row SCIM itself writes. A suspended membership keeps no grants: the read
    path checks that before it asks.
    """
    grants = await load_grant_map(session, tenant_id)
    held: list[GrantedRole] = []
    permissions: set[Permission] = set()

    if user_id is not None:
        held.extend(grants.get(("user", str(user_id)), []))
        # Group membership costs a query, so it is only read when this tenant
        # actually has a group-shaped grant — the common case is no grant at all.
        if any(kind == "scim_group" for kind, _ in grants):
            held.extend(await _group_roles_for_user(session, tenant_id, user_id, grants))

    if service_account_id is not None:
        held.extend(grants.get(("service_account", str(service_account_id)), []))

    for role in held:
        permissions |= role.permissions
    return held, frozenset(permissions)


async def _group_roles_for_user(
    session: AsyncSession,
    tenant_id: object,
    user_id: object,
    grants: Mapping[tuple[str, str], list[GrantedRole]] | None = None,
) -> list[GrantedRole]:
    """Custom roles granted to the SCIM groups this user belongs to."""
    from app.auth.identity.models import SCIMGroupMapping

    group_rows = (
        await session.scalars(
            select(SCIMGroupMapping).where(SCIMGroupMapping.tenant_id == tenant_id)
        )
    ).all()
    if not group_rows:
        return []
    grants = grants if grants is not None else await load_grant_map(session, tenant_id)
    roles: list[GrantedRole] = []
    wanted = str(user_id)
    for group in group_rows:
        members = {str(member) for member in (group.member_user_ids or [])}
        if wanted in members:
            roles.extend(grants.get(("scim_group", str(group.id)), []))
    return roles


def summarise(roles: Sequence[GrantedRole]) -> list[dict[str, object]]:
    return [role.as_dict() for role in roles]


def permission_owner_modules(values: Iterable[str]) -> Mapping[str, int]:
    """Count of requested permissions per module — used by the preview endpoint."""
    counts: dict[str, int] = {}
    for raw in values:
        try:
            permission = Permission(str(raw))
        except ValueError:
            continue
        key = module_of(permission)
        counts[key] = counts.get(key, 0) + 1
    return counts
