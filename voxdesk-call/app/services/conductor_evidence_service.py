"""Conductor Evidence Service (Prompt 4).

Persists immutable ``ConductorEvidence`` rows linking a ``ConductorProposal`` to
its authoritative source artifacts (AgentVersion, Call, TestRun, EvaluationResult,
QA Scorecard, Tool Registry, Knowledge Base, Workflow, or Validation Report).
All payloads are scrubbed of credentials before storage.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.db.models import ConductorEvidence, ConductorEvidenceSourceEnum
from app.auth.identity.events import scrub


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_uuid(val: uuid.UUID | str, name: str = "id") -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError) as exc:
        raise BadRequestError(f"Invalid UUID for {name}: {val!r}") from exc


async def record_proposal_evidence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
    source_type: ConductorEvidenceSourceEnum | str,
    source_id: str,
    evidence_summary: str,
    evidence_payload: dict[str, Any] | None = None,
) -> ConductorEvidence:
    """Create and flush a credential-scrubbed ``ConductorEvidence`` record."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    p_id = _ensure_uuid(proposal_id, "proposal_id")
    src_val = (
        source_type.value
        if hasattr(source_type, "value")
        else str(source_type).split(".")[-1].lower()
    )

    scrubbed_payload = scrub(dict(evidence_payload or {}))
    row = ConductorEvidence(
        id=uuid.uuid4(),
        proposal_id=p_id,
        tenant_id=t_id,
        source_type=src_val,
        source_id=str(source_id)[:120],
        evidence_summary=str(evidence_summary or "").strip(),
        evidence_payload=scrubbed_payload if isinstance(scrubbed_payload, dict) else {},
        created_at=_now(),
    )
    session.add(row)
    await session.flush()
    return row


async def list_proposal_evidence(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    proposal_id: uuid.UUID | str,
) -> list[ConductorEvidence]:
    """Return all persisted ``ConductorEvidence`` rows for ``proposal_id`` in creation order."""
    t_id = _ensure_uuid(tenant_id, "tenant_id")
    p_id = _ensure_uuid(proposal_id, "proposal_id")
    stmt = (
        select(ConductorEvidence)
        .where(
            ConductorEvidence.tenant_id == t_id,
            ConductorEvidence.proposal_id == p_id,
        )
        .order_by(ConductorEvidence.created_at.asc())
    )
    return list((await session.execute(stmt)).scalars().all())
