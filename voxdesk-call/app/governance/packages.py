"""Reproducible, hash-addressed evidence packages."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .context import GovernanceScope
from .evidence import verify_chain
from .exceptions import GovernanceValidation
from .hashing import merkle_root, sha256_hex
from .models import EvidenceEvent, EvidencePackage


async def create_package(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    from_sequence: int | None,
    to_sequence: int | None,
    actor_user_id: uuid.UUID,
) -> EvidencePackage:
    rows = list(
        (
            await session.execute(
                select(EvidenceEvent)
                .where(
                    EvidenceEvent.tenant_id == scope.tenant_id,
                    EvidenceEvent.organization_id == scope.organization_id,
                )
                .order_by(EvidenceEvent.sequence.asc())
            )
        ).scalars()
    )
    verify_chain(rows)
    chosen = [
        row for row in rows
        if (from_sequence is None or row.sequence >= from_sequence)
        and (to_sequence is None or row.sequence <= to_sequence)
    ]
    if not chosen:
        raise GovernanceValidation("At least one evidence event is required")
    if from_sequence is not None and to_sequence is not None and from_sequence > to_sequence:
        raise GovernanceValidation("from_sequence must not exceed to_sequence")
    event_hashes = [row.event_hash for row in chosen]
    root = merkle_root(event_hashes)
    manifest = {
        "format": "governance-evidence-v1",
        "tenant_id": str(scope.tenant_id),
        "organization_id": str(scope.organization_id),
        "environment_id": str(scope.environment_id) if scope.environment_id else None,
        "from_sequence": chosen[0].sequence,
        "to_sequence": chosen[-1].sequence,
        "event_count": len(chosen),
        "event_hashes": event_hashes,
        "evidence_root": root,
    }
    package_hash = sha256_hex(manifest)
    row = EvidencePackage(
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        environment_id=scope.environment_id,
        from_sequence=chosen[0].sequence,
        to_sequence=chosen[-1].sequence,
        event_count=len(chosen),
        evidence_root=root,
        package_hash=package_hash,
        manifest=manifest,
        created_by=actor_user_id,
    )
    session.add(row)
    await session.flush()
    return row


def verify_package(row: EvidencePackage) -> bool:
    manifest = row.manifest or {}
    if row.package_hash != sha256_hex(manifest):
        raise GovernanceValidation("Evidence package hash does not verify")
    hashes = manifest.get("event_hashes")
    if not isinstance(hashes, list) or row.evidence_root != merkle_root(hashes):
        raise GovernanceValidation("Evidence package root does not verify")
    if row.event_count != len(hashes):
        raise GovernanceValidation("Evidence package event count does not verify")
    return True
