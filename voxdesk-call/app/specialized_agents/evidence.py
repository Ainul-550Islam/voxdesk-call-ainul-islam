"""Evidence bridge from specialized executions to Prompt-1 governance evidence."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.governance.context import GovernanceScope
from app.governance.evidence import append_event

from .enums import EvidenceStatus
from .schemas import EvidenceReference


async def emit_execution_evidence(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    context: dict[str, Any],
    status: str,
    input_fingerprint: str,
    output_fingerprint: str | None,
    policy_decision_id: uuid.UUID | None,
    lineage_root_id: uuid.UUID | None,
    review_required: bool,
    review_state: str,
    source_fingerprints: list[str],
    actor_user_id: uuid.UUID,
) -> EvidenceReference:
    """Append only non-sensitive execution metadata to the governance chain."""
    payload = {
        "agent_type": context["agent_type"],
        "agent_version": context["agent_version"],
        "model_version_id": context["model_version_id"],
        "policy_decision_id": str(policy_decision_id) if policy_decision_id else None,
        "request_id": context["request_id"],
        "trace_id": context["trace_id"],
        "execution_status": status,
        "input_fingerprint": input_fingerprint,
        "output_fingerprint": output_fingerprint,
        "source_fingerprints": list(source_fingerprints),
        "lineage_root_id": str(lineage_root_id) if lineage_root_id else None,
        "review_required": review_required,
        "review_state": review_state,
    }
    event = await append_event(
        session,
        scope,
        event_type="specialized_agent_execution",
        payload=payload,
        actor_user_id=actor_user_id,
        correlation_id=context["trace_id"],
        subject_type="specialized_agent_execution",
        subject_id=context.get("execution_id"),
    )
    return EvidenceReference(
        event_id=event.id,
        event_hash=event.event_hash,
        status=EvidenceStatus.RECORDED.value,
        event_type=event.event_type,
    )
