"""Bridge legal durable results to the shared review workflow."""
from __future__ import annotations

import uuid

from app.governance.context import GovernanceScope
from app.review.service import create_case, decide_case
from app.specialized_agents.executor import SpecializedExecutionRecord

from .persistence import persist_review


async def persist_and_open_review(session, scope: GovernanceScope, *, execution: SpecializedExecutionRecord, output: dict, actor_user_id: uuid.UUID | None = None):
    await persist_review(session, scope, execution_id=execution.id, output=output)
    if output.get("review_required"):
        return await create_case(session, scope, actor_user_id=actor_user_id, case_type="legal", execution_id=execution.id, reason="Legal clause findings require qualified human review", requested_controls=["qualified_human_review"], metadata={"document_id": output.get("document_id", "")})
    return None


async def apply_legal_outcome(session, scope: GovernanceScope, *, case_id: uuid.UUID, reviewer_id: uuid.UUID, decision: str, rationale: str, evidence: dict | None = None):
    return await decide_case(session, scope, case_id=case_id, reviewer_id=reviewer_id, decision=decision, rationale=rationale, evidence=evidence)
