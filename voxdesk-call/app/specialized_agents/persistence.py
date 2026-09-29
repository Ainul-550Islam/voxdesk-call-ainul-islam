"""Shared adapter for the existing SpecializedExecutionRecord.

Specialized domains call this adapter after governed execution rather than
creating parallel execution tables or bypassing the executor.  Domain-specific
projections live in their own persistence modules.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.governance.context import GovernanceScope
from .executor import SpecializedExecutionRecord


async def get_execution(session: AsyncSession, scope: GovernanceScope, execution_id: uuid.UUID, *, agent_type: str | None = None, lock: bool = False) -> SpecializedExecutionRecord | None:
    query = select(SpecializedExecutionRecord).where(SpecializedExecutionRecord.id == execution_id, SpecializedExecutionRecord.tenant_id == scope.tenant_id, SpecializedExecutionRecord.organization_id == scope.organization_id, SpecializedExecutionRecord.environment_id == scope.environment_id)
    if agent_type:
        query = query.where(SpecializedExecutionRecord.agent_type == agent_type)
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


async def update_review_state(session: AsyncSession, scope: GovernanceScope, *, execution_id: uuid.UUID, review_state: str, status: str | None = None) -> SpecializedExecutionRecord | None:
    row = await get_execution(session, scope, execution_id, lock=True)
    if row is None:
        return None
    row.review_state = review_state
    if status is not None:
        row.status = status
    await session.flush()
    return row


def response_metadata(row: SpecializedExecutionRecord) -> dict[str, Any]:
    """Return metadata only; result is already executor-scrubbed at rest."""
    return {
        "execution_id": row.id,
        "tenant_id": row.tenant_id,
        "organization_id": row.organization_id,
        "environment_id": row.environment_id,
        "agent_type": row.agent_type,
        "status": row.status,
        "review_required": row.review_required,
        "review_state": row.review_state,
        "input_fingerprint": row.input_fingerprint,
        "output_fingerprint": row.output_fingerprint,
        "policy_decision_id": row.policy_decision_id,
        "lineage_root_id": row.lineage_root_id,
        "evidence_root_hash": row.evidence_root_hash,
    }
