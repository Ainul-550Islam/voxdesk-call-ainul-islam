"""Bridge governed execution review requirements into durable human cases."""
from __future__ import annotations

import uuid

from app.governance.context import GovernanceScope
from app.review.service import create_case, decide_case
from .executor import SpecializedExecutionRecord


_CASE_TYPES = {"legal": "legal", "translation": "translation", "insight": "insight", "forecasting": "forecast", "anomaly": "anomaly"}


async def ensure_review_case(session, scope: GovernanceScope, *, execution: SpecializedExecutionRecord, actor_user_id: uuid.UUID | None = None, requested_controls: list[str] | None = None):
    if not execution.review_required and execution.review_state not in {"required", "pending"}:
        return None
    return await create_case(session, scope, actor_user_id=actor_user_id, case_type=_CASE_TYPES.get(execution.agent_type, "specialized_execution"), execution_id=execution.id, reason=f"{execution.agent_type} execution requires human review", requested_controls=requested_controls or ["human_review"], metadata={"agent_type": execution.agent_type, "execution_review_state": execution.review_state})


async def persist_review_decision(session, scope: GovernanceScope, *, case_id: uuid.UUID, reviewer_id: uuid.UUID, decision: str, rationale: str, evidence: dict | None = None):
    return await decide_case(session, scope, case_id=case_id, reviewer_id=reviewer_id, decision=decision, rationale=rationale, evidence=evidence)
