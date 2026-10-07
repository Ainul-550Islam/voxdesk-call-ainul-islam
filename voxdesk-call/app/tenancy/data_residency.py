"""Residency policy. A label is not a physical guarantee.

This evaluator remains deliberately non-persistent: it validates a label and
reports the deployment's configured semantics. The additive enterprise
``app.governance.residency`` service stores a tenant-scoped intent and requires
an authoritative external verification before marking physical residency
proven. ``applied`` is always false here, and a label alone never proves
physical residency.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit
from app.db.models import AuditAction
from app.organization.service import Actor
from app.tenancy import regions
from app.tenancy.exceptions import ResidencyDenied, ValidationFailed


@dataclass(frozen=True)
class ResidencyDecision:
    requested: str
    placement: str
    supported: bool
    restricted: bool
    applied: bool
    physical_residency_proven: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "requested": self.requested,
            "placement": self.placement,
            "supported": self.supported,
            "restricted": self.restricted,
            "applied": self.applied,
            "physical_residency_proven": self.physical_residency_proven,
            "reason": self.reason,
        }


def placement_view(organization_id: uuid.UUID) -> dict:
    """What this process can say about an organization. It cannot place data."""
    return {
        "organization_id": str(organization_id),
        "placement": regions.PLACEMENT,
        "supported_regions": sorted(regions.supported_regions()),
        "physical_residency_proven": False,
    }


def evaluate(
    requested: str,
    *,
    catalog: frozenset[str] | None = None,
    restricted: frozenset[str] | None = None,
) -> ResidencyDecision:
    """Decide a label. Supported does not mean stored or physically resident."""
    try:
        label = regions.normalize_label(requested)
    except ValueError as exc:
        raise ValidationFailed(str(exc)) from exc
    banned = regions.is_restricted(label, restricted=restricted)
    allowed = regions.is_supported(label, catalog=catalog) and not banned
    if banned:
        reason = "restricted_region"
    elif not allowed:
        reason = "unsupported_region"
    else:
        reason = "label_recognized_not_a_physical_guarantee"
    return ResidencyDecision(
        requested=label,
        placement=regions.PLACEMENT,
        supported=allowed,
        restricted=banned,
        applied=False,
        physical_residency_proven=False,
        reason=reason,
    )


def require_applicable(decision: ResidencyDecision) -> ResidencyDecision:
    """Refuse a label that must not be treated as a placement."""
    if decision.restricted or not decision.supported:
        raise ResidencyDenied("Region is not supported")
    return decision


def require_physical_proof(decision: ResidencyDecision) -> ResidencyDecision:
    """Fail closed unless an authoritative verifier has changed the decision.

    The ordinary evaluator can never satisfy this function. It exists as a
    named guard so a future provider-backed verifier cannot accidentally reuse
    ``supported`` as proof of physical placement.
    """
    if not decision.physical_residency_proven:
        raise ResidencyDenied("Authoritative physical residency verification is required")
    return decision


async def audit_rejection(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor: Actor | None,
    requested: str,
    reason: str,
    operation: str = "residency_change",
) -> None:
    """Record a refused change. The detail has no secret and no proof."""
    who = actor or Actor()
    await emit(
        session,
        AuditAction.AUTHZ_DENIED,
        tenant_id=tenant_id,
        actor_user_id=who.user_id,
        actor_email=who.email,
        ip_address=who.ip_address,
        user_agent=who.user_agent,
        detail={
            "operation": operation[:64],
            "reason": reason[:80],
            "requested_region": (requested or "")[:64],
            "applied": False,
            "physical_residency_proven": False,
        },
        commit=False,
    )
