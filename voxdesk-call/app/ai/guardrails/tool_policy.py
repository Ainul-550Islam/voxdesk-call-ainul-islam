"""Server-side tool authorization.

The model does not get a vote. Existing voice tools stay available to the
agent runtime so the live call path does not change. A human caller still
needs the existing permission.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.models import UserRole
from app.ai.guardrails.safety import enforce as evaluate_safety

# Runtime tools are read lazily from the canonical dispatcher contract in
# ``app.agent.functions``. Keeping a second allowlist here caused drift between
# policy, the advertised schemas, and the only executor.
_TOOL_PERMISSION = {
    "check_availability": Permission.APPOINTMENT_READ,
    "answer_question": Permission.KNOWLEDGE_READ,
    "book_appointment": Permission.APPOINTMENT_WRITE,
    "reschedule_appointment": Permission.APPOINTMENT_WRITE,
    "cancel_appointment": Permission.APPOINTMENT_WRITE,
    "confirm_appointment": Permission.APPOINTMENT_WRITE,
    "take_message": Permission.LEAD_CREATE,
    "qualify_lead": Permission.LEAD_CREATE,
    "mark_do_not_call": Permission.COMPLIANCE_WRITE,
    "escalate_to_human": Permission.CALL_READ,
    "send_sms": Permission.CAMPAIGN_RUN,
    "transfer_call": Permission.CALL_READ_ALL,
    "refund_payment": Permission.BILLING_WRITE,
    "change_billing": Permission.BILLING_WRITE,
    "delete_data": Permission.KNOWLEDGE_DELETE,
}


@dataclass(frozen=True)
class ToolDecision:
    tool: str
    allowed: bool
    reason: str
    confirmation_required: bool = False

    def as_dict(self) -> dict:
        return {
            "tool": self.tool,
            "allowed": self.allowed,
            "reason": self.reason,
            "confirmation_required": self.confirmation_required,
        }


def enforce(
    tool: str,
    *,
    principal: str = "user",
    role: UserRole | None = None,
    fresh_mfa: bool = False,
) -> ToolDecision:
    """Runtime entry. The model still does not get a vote."""
    return authorize(tool, principal=principal, role=role, fresh_mfa=fresh_mfa)


def authorize(
    tool: str,
    *,
    principal: str = "user",
    role: UserRole | None = None,
    fresh_mfa: bool = False,
) -> ToolDecision:
    name = (tool or "").strip()
    if principal == "model":
        return ToolDecision(name, False, "model_cannot_self_authorize")
    if principal == "agent_runtime":
        # Import at the boundary to avoid an import cycle while the agent
        # package loads. The model can only name tools the canonical dispatcher
        # will actually execute.
        from app.agent.functions import DISPATCHABLE_TOOLS, tool_contract

        contract = tool_contract(name)
        if name in DISPATCHABLE_TOOLS and contract.get("agent_runtime") is True:
            return ToolDecision(name, True, "existing_agent_tool")
        return ToolDecision(name, False, "not_an_agent_tool")
    permission = _TOOL_PERMISSION.get(name)
    if permission is None:
        return ToolDecision(name, False, "unknown_tool")
    if role is None or not has_permission(role, permission):
        return ToolDecision(name, False, "permission_denied")
    if name in {"refund_payment", "change_billing", "delete_data", "transfer_call", "send_sms"}:
        safety = evaluate_safety(name, role=role, principal="user", fresh_mfa=fresh_mfa)
        if safety.decision == "allow":
            return ToolDecision(name, True, safety.reason)
        if safety.decision == "require_confirmation":
            return ToolDecision(name, False, safety.reason, confirmation_required=True)
        return ToolDecision(name, False, safety.reason)
    return ToolDecision(name, True, "permission_granted")


def agent_runtime_decision(tool: str) -> ToolDecision:
    return authorize(tool, principal="agent_runtime")
