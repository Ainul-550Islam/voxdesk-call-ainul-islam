"""Durable residency intent and explicitly verifiable status.

The existing tenancy residency evaluator remains the source of deployment
semantics. This module stores intent, never turns a supported label into a
physical claim, and only marks an intent verified when an authoritative
external verification receipt is supplied.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tenancy import data_residency

from .context import GovernanceScope
from .attestation import verify_attestation
from .evidence import append_event
from .exceptions import GovernanceNotFound, ResidencyVerificationRequired
from .models import Attestation, ResidencyIntent


async def request_intent(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    region: str,
    actor_user_id: uuid.UUID,
) -> ResidencyIntent:
    decision = data_residency.evaluate(region)
    current = await session.scalar(
        select(func.max(ResidencyIntent.version)).where(
            ResidencyIntent.tenant_id == scope.tenant_id,
            ResidencyIntent.requested_region == decision.requested,
        )
    )
    row = ResidencyIntent(
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        environment_id=scope.environment_id,
        requested_region=decision.requested,
        status="requested" if decision.supported and not decision.restricted else "rejected",
        version=(current or 0) + 1,
        physical_residency_proven=False,
        requested_by=actor_user_id,
        verification_metadata={
            "label_supported": decision.supported,
            "restricted": decision.restricted,
            "deployment_placement": decision.placement,
            "reason": decision.reason,
        },
    )
    session.add(row)
    await session.flush()
    await append_event(
        session,
        scope,
        event_type="residency_requested",
        payload={
            "intent_id": str(row.id),
            "requested_region": row.requested_region,
            "status": row.status,
            "physical_residency_proven": False,
        },
        actor_user_id=actor_user_id,
    )
    return row


async def verify_intent(
    session: AsyncSession,
    scope: GovernanceScope,
    intent_id: uuid.UUID,
    *,
    verifier: str,
    verification_reference: str,
    verification_metadata: dict[str, Any],
    actor_user_id: uuid.UUID,
) -> ResidencyIntent:
    row = await session.scalar(
        select(ResidencyIntent).where(
            ResidencyIntent.id == intent_id,
            ResidencyIntent.tenant_id == scope.tenant_id,
            ResidencyIntent.organization_id == scope.organization_id,
        )
    )
    if row is None:
        raise GovernanceNotFound()
    if not verifier.strip() or not verification_reference.strip():
        raise ResidencyVerificationRequired("Verifier and reference are required")
    if row.status == "rejected":
        raise ResidencyVerificationRequired("A rejected residency intent cannot be verified")
    try:
        attestation_id = uuid.UUID(verification_reference.strip())
    except ValueError:
        raise ResidencyVerificationRequired(
            "Verification must reference an issued authoritative attestation"
        ) from None
    attestation = await session.scalar(
        select(Attestation).where(
            Attestation.id == attestation_id,
            Attestation.tenant_id == scope.tenant_id,
            Attestation.organization_id == scope.organization_id,
        )
    )
    if attestation is None:
        raise ResidencyVerificationRequired("Verification attestation was not found")
    await verify_attestation(session, scope, attestation.id)
    claims = attestation.claims or {}
    if (
        attestation.issuer != verifier.strip()
        or claims.get("physical_residency_proven") is not True
        or claims.get("region") != row.requested_region
    ):
        raise ResidencyVerificationRequired(
            "The attestation does not authoritatively verify this residency intent"
        )
    row.status = "verified"
    row.physical_residency_proven = True
    row.authoritative_verifier = attestation.issuer[:200]
    row.verification_reference = str(attestation.id)
    row.verification_metadata = {
        **verification_metadata,
        "attestation_id": str(attestation.id),
        "authoritative": True,
    }
    row.verified_at = dt.datetime.now(dt.timezone.utc)
    await session.flush()
    await append_event(
        session,
        scope,
        event_type="residency_verified",
        payload={
            "intent_id": str(row.id),
            "requested_region": row.requested_region,
            "authoritative_verifier": row.authoritative_verifier,
            "verification_reference": row.verification_reference,
            "physical_residency_proven": True,
        },
        actor_user_id=actor_user_id,
    )
    return row
