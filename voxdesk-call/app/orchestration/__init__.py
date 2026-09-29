"""Compatibility exports for orchestration helpers.

The durable workflow domain, repository, and service are the sole production
workflow architecture. In particular, ``WorkflowEngine`` below is retained
only for older pure/in-process callers and tests; it is not used by the API
and does not provide durable persistence or cross-process idempotency. New
workflow code must use ``app.services.workflow_service`` and its repository.

Campaign helpers remain pure, database-independent utilities.
"""

from app.orchestration.campaign import (
    CHANNEL_SMS,
    CHANNEL_VOICE,
    CHANNEL_WHATSAPP,
    Campaign,
    CampaignMetrics,
    ComplianceGate,
    DayState,
    Intent,
    REASON_ATTEMPT_LIMIT,
    REASON_DAILY_LIMIT,
    REASON_DNC,
    REASON_WINDOW,
    Schedule,
    Throttle,
    can_transition,
    dispatch,
    intent_key,
    is_within_window,
)
from app.orchestration.conditions import CONDITION_OPERATORS, compare, evaluate
from app.orchestration.workflow import (
    CONTROLLED_ACTIONS,
    FORBIDDEN_ACTION_MARKERS,
    Action,
    Condition,
    Node,
    RunResult,
    Step,
    Workflow,
    WorkflowEngine,
)

__all__ = [
    "CONTROLLED_ACTIONS",
    "FORBIDDEN_ACTION_MARKERS",
    "Action",
    "CHANNEL_SMS",
    "CHANNEL_VOICE",
    "CHANNEL_WHATSAPP",
    "CONDITION_OPERATORS",
    "Campaign",
    "CampaignMetrics",
    "ComplianceGate",
    "Condition",
    "DayState",
    "Intent",
    "Node",
    "REASON_ATTEMPT_LIMIT",
    "REASON_DAILY_LIMIT",
    "REASON_DNC",
    "REASON_WINDOW",
    "RunResult",
    "Schedule",
    "Step",
    "Throttle",
    "Workflow",
    "WorkflowEngine",
    "can_transition",
    "compare",
    "dispatch",
    "evaluate",
    "intent_key",
    "is_within_window",
]
