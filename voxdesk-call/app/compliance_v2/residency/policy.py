"""Tenant region pinning and the cross-region guard.

A pin is a data-placement promise, so this module is deliberately conservative
about what it claims:

* the pin is read from the tenant's **verified** ``GovernanceResidencyIntent``
  rows (``app.governance.residency.verify_intent`` marks those). An intent that
  was requested but never verified is reported as ``pending`` and is *not*
  treated as a pin;
* when no verified intent exists, the deployment default region applies and the
  source is recorded as ``default``, so an operator can tell a promise from a
  fallback;
* with no ``DATA_REGIONS`` at all, every decision is NOT_CONFIGURED. There is no
  silent default, because a defaulted region is exactly the wrong answer for a
  residency question.

``enforce_region`` is the guard: it compares a requested region with the pin and
raises :class:`CrossRegionViolation`. Every refusal writes an audit event, so a
cross-region attempt appears in the SIEM feed even though nothing happened.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.compliance_v2.residency.regions import (
    RegionNotConfigured,
    RegionRegistry,
    build_registry,
    registry as default_registry,
)
from app.compliance_v2.residency.storage_keys import (
    CrossRegionKey,
    assert_region,
    classify,
    region_of,
)
from app.governance.models import ResidencyIntent

STATUS_CONFIGURED = "configured"
STATUS_NOT_CONFIGURED = "not_configured"


class ResidencyError(RuntimeError):
    code = "residency_error"


class CrossRegionViolation(ResidencyError):
    """A request would place or read data outside the tenant's pinned region."""

    code = "cross_region_blocked"


@dataclasses.dataclass(frozen=True)
class ResidencyDecision:
    """What the guard decided, with the reason it decided it."""

    tenant_id: str
    status: str
    pinned_region: str = ""
    requested_region: str = ""
    source: str = "none"
    allowed: bool = False
    reason: str = ""

    @property
    def configured(self) -> bool:
        return self.status == STATUS_CONFIGURED

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "status": self.status,
            "pinned_region": self.pinned_region,
            "requested_region": self.requested_region,
            "source": self.source,
            "allowed": self.allowed,
            "reason": self.reason,
        }

    def require_allowed(self) -> None:
        if not self.allowed:
            raise CrossRegionViolation(self.reason or "region check failed")


def _active_registry(registry: RegionRegistry | None) -> RegionRegistry:
    return registry or build_registry()


@dataclasses.dataclass(frozen=True)
class PinRecord:
    region: str
    source: str

    @property
    def verified(self) -> bool:
        return self.source == "intent"


async def resolve_pin(
    session: AsyncSession, tenant_id: uuid.UUID
) -> PinRecord | None:
    """The tenant's verified region pin, newest verification first."""
    row = await session.scalar(
        select(ResidencyIntent)
        .where(
            ResidencyIntent.tenant_id == tenant_id,
            ResidencyIntent.status == "verified",
            ResidencyIntent.physical_residency_proven.is_(True),
        )
        .order_by(ResidencyIntent.version.desc())
        .limit(1)
    )
    if row is None:
        return None
    return PinRecord(region=(row.requested_region or "").strip().lower(), source="intent")


async def pin_document(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    registry: RegionRegistry | None = None,
) -> dict[str, Any]:
    """The pin as an operator-facing document, without enforcing anything."""
    active = _active_registry(registry)
    if not active.configured:
        return {
            "tenant_id": str(tenant_id),
            "status": STATUS_NOT_CONFIGURED,
            "region": "",
            "source": "none",
            "regions": active.as_dict(),
        }
    pin = await resolve_pin(session, tenant_id)
    if pin is not None and pin.region:
        return {
            "tenant_id": str(tenant_id),
            "status": STATUS_CONFIGURED,
            "region": pin.region,
            "source": pin.source,
            "verified": pin.verified,
            "regions": active.as_dict(),
        }
    upcoming = await session.scalar(
        select(ResidencyIntent)
        .where(ResidencyIntent.tenant_id == tenant_id)
        .order_by(ResidencyIntent.version.desc())
        .limit(1)
    )
    fallback = active.default_region()
    return {
        "tenant_id": str(tenant_id),
        "status": STATUS_CONFIGURED,
        "region": fallback.code,
        "source": "pending_intent" if upcoming is not None else "default",
        "verified": False,
        "requested_region": (upcoming.requested_region if upcoming is not None else ""),
        "regions": active.as_dict(),
    }


async def check_region(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    requested_region: str,
    registry: RegionRegistry | None = None,
) -> ResidencyDecision:
    """Decide whether ``requested_region`` is acceptable for this tenant."""
    active = _active_registry(registry)
    if not active.configured:
        return ResidencyDecision(
            tenant_id=str(tenant_id),
            status=STATUS_NOT_CONFIGURED,
            requested_region=requested_region,
            allowed=False,
            reason="no DATA_REGIONS are configured, so no region can be authorised",
        )
    document = await pin_document(session, tenant_id, registry=active)
    pinned = str(document.get("region") or "")
    if not pinned:
        return ResidencyDecision(
            tenant_id=str(tenant_id),
            status=STATUS_CONFIGURED,
            requested_region=requested_region,
            allowed=False,
            reason="no region could be resolved for this tenant",
        )
    if not active.is_known(requested_region):
        return ResidencyDecision(
            tenant_id=str(tenant_id),
            status=STATUS_CONFIGURED,
            pinned_region=pinned,
            requested_region=requested_region,
            source=str(document.get("source") or "none"),
            allowed=False,
            reason=f"{requested_region!r} is not a configured region",
        )
    allowed = requested_region.strip().lower() == pinned
    return ResidencyDecision(
        tenant_id=str(tenant_id),
        status=STATUS_CONFIGURED,
        pinned_region=pinned,
        requested_region=requested_region,
        source=str(document.get("source") or "none"),
        allowed=allowed,
        reason="" if allowed else f"tenant is pinned to {pinned!r}",
    )


async def enforce_region(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    requested_region: str,
    operation: str,
    actor_user_id: uuid.UUID | None = None,
    resource: str = "",
    registry: RegionRegistry | None = None,
) -> ResidencyDecision:
    """Check the region, audit a refusal, and raise when the answer is no."""
    decision = await check_region(
        session, tenant_id=tenant_id, requested_region=requested_region, registry=registry
    )
    if decision.allowed:
        return decision
    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        event_type="residency_cross_region_blocked",
        resource_type="residency",
        resource_id=tenant_id,
        result="failure",
        detail={
            "operation": operation[:120],
            "resource": resource[:200],
            "pinned_region": decision.pinned_region,
            "requested_region": decision.requested_region,
            "status": decision.status,
            "reason": decision.reason[:200],
        },
    )
    await session.flush()
    raise CrossRegionViolation(decision.reason)


async def enforce_key(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    key: str,
    operation: str,
    actor_user_id: uuid.UUID | None = None,
    registry: RegionRegistry | None = None,
) -> str:
    """Guard an object key: regional keys are checked, legacy keys are refused.

    A legacy key has no region, so there is nothing to check. Serving it while a
    tenant is pinned would be an unverified placement, so the guard refuses and
    reports ``legacy_object_key`` — the operator can then run the relocation
    path (:func:`app.compliance_v2.residency.storage_keys.rewrite_to_region`).
    """
    if classify(key) != "regional":
        await record_event(
            session,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            event_type="residency_legacy_key_refused",
            resource_type="residency",
            resource_id=tenant_id,
            result="failure",
            detail={"operation": operation[:120], "key_prefix": (key or "")[:60]},
        )
        await session.flush()
        raise CrossRegionViolation("object key carries no region and cannot be verified")

    document = await pin_document(session, tenant_id, registry=registry)
    pinned = str(document.get("region") or "")
    try:
        assert_region(key, expected_region=pinned, tenant_id=tenant_id)
    except CrossRegionKey as exc:
        await record_event(
            session,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            event_type="residency_cross_region_blocked",
            resource_type="residency",
            resource_id=tenant_id,
            result="failure",
            detail={"operation": operation[:120], "object_region": region_of(key)},
        )
        await session.flush()
        raise CrossRegionViolation(str(exc)) from exc
    return pinned


def region_catalogue(registry: RegionRegistry | None = None) -> dict[str, Any]:
    """The configured regions, for the API and the evidence collector."""
    active = registry if registry is not None else default_registry()
    try:
        default = active.default_region().code
    except RegionNotConfigured:
        default = ""
    return {
        "configured": active.configured,
        "default_region": default,
        "regions": [region.as_dict() for region in active.regions],
    }


__all__ = [
    "STATUS_CONFIGURED",
    "STATUS_NOT_CONFIGURED",
    "CrossRegionViolation",
    "PinRecord",
    "ResidencyDecision",
    "ResidencyError",
    "check_region",
    "enforce_key",
    "enforce_region",
    "pin_document",
    "region_catalogue",
    "resolve_pin",
]
