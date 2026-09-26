"""High-risk action decisions on top of the existing permission enum.

This does not grant anything ``has_permission`` does not already grant. A
model principal is never an actor.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.models import UserRole

ALLOW = "allow"
DENY = "deny"
CONFIRM = "require_confirmation"
PRIVILEGED = "require_privileged_actor"
FRESH_MFA = "require_fresh_mfa"

# Actions the product does not let a model decide. Permission is the server check.
_ACTIONS = {
    "refund_payment": Permission.BILLING_WRITE,
    "change_billing": Permission.BILLING_WRITE,
    "delete_data": Permission.KNOWLEDGE_DELETE,
    "transfer_call": Permission.CALL_READ_ALL,
    "escalate_to_human": Permission.CALL_READ,
    "send_sms": Permission.CAMPAIGN_RUN,
}


@dataclass(frozen=True)
class SafetyDecision:
    action: str
    decision: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW

    def as_dict(self) -> dict:
        return {"action": self.action, "decision": self.decision, "reason": self.reason}


def enforce(
    action: str,
    *,
    role: UserRole | None,
    principal: str = "user",
    fresh_mfa: bool = False,
) -> SafetyDecision:
    """Runtime entry. A model principal is still never an actor."""
    return evaluate(action, role=role, principal=principal, fresh_mfa=fresh_mfa)


def evaluate(
    action: str,
    *,
    role: UserRole | None,
    principal: str = "user",
    fresh_mfa: bool = False,
) -> SafetyDecision:
    name = (action or "").strip()
    if principal == "model":
        return SafetyDecision(name, DENY, "model_cannot_authorize")
    permission = _ACTIONS.get(name)
    if permission is None:
        return SafetyDecision(name, DENY, "unknown_action")
    if role is None or not has_permission(role, permission):
        return SafetyDecision(name, DENY, "permission_denied")
    if name in {"refund_payment", "change_billing", "delete_data"}:
        if not fresh_mfa:
            return SafetyDecision(name, FRESH_MFA, "fresh_mfa_required")
        return SafetyDecision(name, PRIVILEGED, "privileged_actor_confirmed")
    if name == "transfer_call":
        return SafetyDecision(name, CONFIRM, "confirmation_required")
    return SafetyDecision(name, ALLOW, "permission_granted")
