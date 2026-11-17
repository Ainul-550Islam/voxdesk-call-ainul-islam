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

import structlog

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit
from app.db.models import AuditAction
from app.organization.service import Actor
from app.tenancy import regions
from app.tenancy.exceptions import ResidencyDenied, ValidationFailed

log = structlog.get_logger()


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


# ------------------------------------------------- artifact write enforcement ---
#
# PART 16 / P16-07. The evaluator above answers "is this label allowed". The
# functions below answer the question a buyer actually asks: "does the recording
# I just made, the transcript I just stored and tonight's backup land where I
# pinned them". Three artifacts, one guard, and a receipt that states what was
# proven — which today is "nothing", because no region is configured.

#: The artifacts a pin has to cover. A residency claim that skipped recordings
#: would be the one that matters least and gets audited most.
ARTIFACTS: tuple[str, ...] = ("recording", "transcript", "backup", "export")


@dataclass(frozen=True)
class WriteVerdict:
    """The receipt for one attempted artifact write."""

    artifact: str
    pinned: str | None
    target: str | None
    allowed: bool
    reason: str

    def as_dict(self) -> dict:
        return {
            "artifact": self.artifact,
            "pinned_region": self.pinned,
            "target_region": self.target,
            "allowed": self.allowed,
            "reason": self.reason,
            # Always false here: an allowed write is not a physical guarantee.
            "physical_residency_proven": False,
        }


def check_artifact_write(
    *,
    artifact: str,
    pinned: str | None,
    target: str | None,
    catalog: frozenset[str] | None = None,
) -> WriteVerdict:
    """Evaluate one write. Never raises; returns the verdict with its reason."""
    if artifact not in ARTIFACTS:
        raise ValidationFailed(f"Unknown artifact type {artifact!r}")
    try:
        reason = regions.require_write_region(pinned, target, catalog=catalog)
        allowed = True
    except regions.CrossRegionWrite as exc:
        reason = exc.reason
        allowed = False
        if exc.reason in (regions.WRITE_FOREIGN, regions.WRITE_UNCONFIGURED):
            # A refused write is an incident-shaped event: somebody asked for
            # data in one place and it was about to go somewhere else.
            log.warning(
                "residency.write_refused",
                artifact=artifact,
                pinned=pinned,
                target=target,
                reason=exc.reason,
            )
    return WriteVerdict(
        artifact=artifact,
        pinned=pinned,
        target=target,
        allowed=allowed,
        reason=reason,
    )


def enforce_artifact_write(
    *,
    artifact: str,
    pinned: str | None,
    target: str | None,
    catalog: frozenset[str] | None = None,
) -> WriteVerdict:
    """Same as :func:`check_artifact_write`, but a refusal raises.

    Callers that write bytes (recordings, transcripts, backups) use this one so a
    refusal stops the write instead of being logged after the fact.
    """
    verdict = check_artifact_write(
        artifact=artifact, pinned=pinned, target=target, catalog=catalog
    )
    if not verdict.allowed:
        raise ResidencyDenied(
            f"refusing to write a {artifact} outside the pinned region ({verdict.reason})"
        )
    return verdict


async def pinned_region(session: AsyncSession, tenant_id: uuid.UUID) -> str | None:
    """The tenant's current region pin, or ``None``.

    Read from the newest *live* `ResidencyIntent`; a `rejected` intent is an
    intent the deployment refused, so it must not silently become the pin. A
    `verified` intent is returned too — verification is about physical proof,
    which is carried separately and never implied by the pin itself.
    """
    from sqlalchemy import select

    from app.governance.models import ResidencyIntent

    row = await session.scalar(
        select(ResidencyIntent)
        .where(
            ResidencyIntent.tenant_id == tenant_id,
            ResidencyIntent.status.in_(("requested", "verified")),
        )
        .order_by(ResidencyIntent.version.desc(), ResidencyIntent.created_at.desc())
    )
    return row.requested_region if row is not None else None


async def enforce_tenant_write(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    artifact: str,
    target: str | None,
    catalog: frozenset[str] | None = None,
) -> WriteVerdict:
    """Guard an artifact write for a tenant, reading its pin first.

    One extra `SELECT` on a write path that already writes is cheap; a residency
    promise that is checked "somewhere else" is not a promise.
    """
    return enforce_artifact_write(
        artifact=artifact,
        pinned=await pinned_region(session, tenant_id),
        target=target,
        catalog=catalog,
    )
