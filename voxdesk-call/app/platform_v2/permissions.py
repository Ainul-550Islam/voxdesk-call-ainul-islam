"""Permission bundles for the platform surfaces (Batch 07).

The permissions themselves live in :mod:`app.auth.permissions` — one enum, one
place a reviewer looks. This module is only the *policy*: which bundle a role
receives, written as a document rather than scattered through ``rbac.py``.

Two deliberate boundaries:

* **An agent schedules nobody.** They read the rota they are on, request leave and
  swap shifts, and register their own phone for push. Everything else is a
  supervisor action.
* **Publishing is an admin action, not a manager one.** A manager builds and
  adjusts the week; making it official — the point at which staff are notified and
  payroll starts trusting it — belongs to the role that owns the tenant's
  configuration. ``wfm:approve`` (leave, swaps, adherence exceptions) stays with
  the manager, because those decisions are day-to-day.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import TYPE_CHECKING

from app.auth.permissions import Permission

if TYPE_CHECKING:
    from app.db.models import UserRole

#: Everything needed to see your own week and take part in it.
WFM_SELF_SERVICE: frozenset[Permission] = frozenset(
    {
        Permission.WFM_READ,
        Permission.MOBILE_READ,
        Permission.MOBILE_WRITE,
    }
)

#: A supervisor: builds the rota, approves leave and swaps, reads adherence.
WFM_SUPERVISOR: frozenset[Permission] = WFM_SELF_SERVICE | frozenset(
    {
        Permission.WFM_WRITE,
        Permission.WFM_SCHEDULE,
        Permission.WFM_APPROVE,
        Permission.BRAND_READ,
        Permission.DOMAIN_READ,
    }
)

#: A tenant administrator: publishes, rebrands, claims domains, sends push.
WFM_ADMIN: frozenset[Permission] = WFM_SUPERVISOR | frozenset(
    {
        Permission.WFM_PUBLISH,
        Permission.BRAND_WRITE,
        Permission.DOMAIN_WRITE,
        Permission.DOMAIN_VERIFY,
        Permission.MOBILE_PUSH_SEND,
    }
)

#: A read-only observer sees the numbers and nothing else.
WFM_OBSERVER: frozenset[Permission] = frozenset({Permission.WFM_READ})

#: Roles that receive each bundle. ``rbac.ROLE_PERMISSIONS`` is the union.
BUNDLES_BY_ROLE_NAME: dict[str, frozenset[Permission]] = {
    "MANAGER": WFM_SUPERVISOR,
    "ADMIN": WFM_ADMIN,
    "AGENT": WFM_SELF_SERVICE,
    "VIEWER": WFM_OBSERVER,
}


def extend(role_permissions: MutableMapping[UserRole, frozenset[Permission]]) -> None:
    """Union the bundles into an existing role table, in place.

    Called once from :mod:`app.auth.rbac`. ``OWNER`` is derived from the enum, so
    it already holds every permission and is skipped here.
    """
    from app.db.models import UserRole

    for role in UserRole:
        bundle = BUNDLES_BY_ROLE_NAME.get(role.name)
        if bundle:
            role_permissions[role] = role_permissions[role] | bundle


__all__ = [
    "BUNDLES_BY_ROLE_NAME",
    "WFM_ADMIN",
    "WFM_OBSERVER",
    "WFM_SELF_SERVICE",
    "WFM_SUPERVISOR",
    "extend",
]
