"""Role bundles for the Batch 03 permissions.

The permission *names* live in ``app.auth.permissions`` (one vocabulary for the
whole product); this module only decides which roles hold them. It is separate
from ``app.auth.rbac`` because ``rbac`` is shared with the other batches and a
marker-guarded call at its end is a smaller change than editing its role sets.

Rules followed here, so the result reads like the rest of the product:

* an **owner** already holds everything in the enum (``rbac`` derives that), so
  nothing is listed for it;
* an **admin** runs the tenant's compliance programme: read plus the operational
  writes — SIEM sinks, evidence runs, access-review campaigns, incidents, DSARs,
  residency pins and the PHI policy;
* an admin may **not** trigger a KMS rekey, approve a break-glass grant, or
  verify/erase on a DSAR without a second pair of eyes; those sit with the owner;
* **managers** read evidence, incidents and DSARs — they must know what is open —
  and may raise an incident, but not close one or run an export;
* agents and viewers get nothing beyond what their existing roles grant.
"""

from __future__ import annotations

from app.auth.permissions import Permission
from app.db.models import UserRole

#: Permissions that are read-only and safe for a manager's operational view.
_MANAGER_READ: frozenset[Permission] = frozenset(
    {
        Permission.COMPLIANCE_SIEM_READ,
        Permission.COMPLIANCE_AUDIT_VERIFY,
        Permission.COMPLIANCE_EVIDENCE_READ,
        Permission.COMPLIANCE_ACCESS_REVIEW_READ,
        Permission.COMPLIANCE_INCIDENT_READ,
        Permission.COMPLIANCE_INCIDENT_WRITE,
        Permission.COMPLIANCE_DSAR_READ,
        Permission.COMPLIANCE_RESIDENCY_READ,
        Permission.COMPLIANCE_PHI_POLICY_READ,
    }
)

#: Everything an admin may do with the compliance surface.
_ADMIN: frozenset[Permission] = _MANAGER_READ | frozenset(
    {
        Permission.COMPLIANCE_SIEM_WRITE,
        Permission.COMPLIANCE_AUDIT_EXPORT,
        Permission.COMPLIANCE_EVIDENCE_RUN,
        Permission.COMPLIANCE_ACCESS_REVIEW_WRITE,
        Permission.COMPLIANCE_DSAR_WRITE,
        Permission.COMPLIANCE_RESIDENCY_WRITE,
        Permission.COMPLIANCE_PHI_POLICY_MANAGE,
        Permission.COMPLIANCE_BREAK_GLASS_REQUEST,
    }
)


def compliance_v2_role_bundles() -> dict[UserRole, frozenset[Permission]]:
    """Extra permissions to add to each role's existing bundle."""
    return {
        UserRole.MANAGER: _MANAGER_READ,
        UserRole.ADMIN: _ADMIN,
    }


def role_bundle_summary() -> dict[str, list[str]]:
    """The bundles as sorted strings, for the runbook and the docs check."""
    return {
        role.value: sorted(permission.value for permission in permissions)
        for role, permissions in compliance_v2_role_bundles().items()
    }


__all__ = ["compliance_v2_role_bundles", "role_bundle_summary"]
