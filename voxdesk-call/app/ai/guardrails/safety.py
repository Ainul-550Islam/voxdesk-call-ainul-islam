"""High-risk action decisions on top of the existing permission enum.

This does not grant anything ``has_permission`` does not already grant. A
model principal is never an actor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.models import UserRole

if TYPE_CHECKING:  # pragma: no cover - annotation-only, avoids a cycle through categories
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.ai.guardrails.categories import GuardrailPolicyView

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


# ---------------------------------------------------------------------------
# Combined turn screening (PART 15, P15-09)
#
# `enforce` above answers "may this *actor* take this action". The functions
# below answer a different question: "may this *text* reach the caller, and if
# not, which category stopped it". Keeping them apart matters -- permission and
# content safety fail for different reasons and are fixed by different people.
# ---------------------------------------------------------------------------

import hashlib  # noqa: E402  (kept beside the code that uses it)

from app.ai.guardrails import categories as _categories  # noqa: E402
from app.ai.guardrails import jailbreak as _jailbreak  # noqa: E402

STAGE_INPUT = "input"
STAGE_OUTPUT = "output"


@dataclass(frozen=True)
class GuardrailOutcome:
    """What screening decided about one piece of text.

    The text itself is not kept: only its SHA-256 and its length, so the event
    table can count repeat offenders without becoming a copy of the abuse.
    """

    allowed: bool
    stage: str
    reason: str
    category: str | None = None
    phrase: str = ""
    text_hash: str = ""
    char_count: int = 0
    jailbreak_score: float = 0.0

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "stage": self.stage,
            "reason": self.reason,
            "category": self.category,
            "phrase": self.phrase,
            "text_hash": self.text_hash,
            "char_count": self.char_count,
            "jailbreak_score": round(self.jailbreak_score, 4),
        }


def hash_text(text: str) -> str:
    """SHA-256 of the normalised text, for dedupe and repeat-offender counts."""
    return hashlib.sha256(_jailbreak.normalise(text).encode("utf-8")).hexdigest()


def screen(
    text: str,
    *,
    stage: str = STAGE_OUTPUT,
    policy: _categories.GuardrailPolicyView | None = None,
    channel: str = "text",
) -> GuardrailOutcome:
    """Screen one turn against input limits, jailbreaks and the harm taxonomy.

    Order is deliberate: size/shape first (cheap and unambiguous), then
    jailbreak, then content categories. The first block wins, so the recorded
    reason is the one a reviewer would have found first too.
    """
    from app.ai.guardrails.input import check as check_input

    effective = policy or _categories.default_policy()
    digest = hash_text(text or "")
    length = len(text or "")

    def blocked(reason: str, category: str | None = None, score: float = 0.0) -> GuardrailOutcome:
        return GuardrailOutcome(
            allowed=False,
            stage=stage,
            reason=reason,
            category=category,
            phrase=effective.hard_stop_phrase,
            text_hash=digest,
            char_count=length,
            jailbreak_score=score,
        )

    shape = check_input(text, channel=channel)
    if not shape.allowed:
        return blocked(f"input_{shape.reason}")

    verdict = _jailbreak.detect(text or "")
    if effective.block_jailbreak and verdict.is_jailbreak:
        return blocked("jailbreak", "prompt_injection", verdict.score)
    if effective.block_prompt_extraction and _jailbreak.extraction_attempt(text or ""):
        return blocked("prompt_extraction", "prompt_injection", verdict.score)

    content = _categories.screen(text or "", effective)
    if content.blocked and content.hit is not None:
        return blocked("category", content.hit.category, verdict.score)

    return GuardrailOutcome(
        allowed=True,
        stage=stage,
        reason="allowed",
        text_hash=digest,
        char_count=length,
        jailbreak_score=verdict.score,
    )


# ---------------------------------------------------------------------------
# Policy loading (PART 15, P15-09)
#
# The row lives in the tenant database, so the loader takes a session. Callers
# that have no session (a unit test, a CLI) get the default policy rather than a
# crash: guardrails must never be the reason a call cannot start.
# ---------------------------------------------------------------------------


async def load_policy(
    session: "AsyncSession | None", tenant_id, agent_id: str | None = None
) -> GuardrailPolicyView:
    """Effective guardrail policy for one agent.

    The tenant default is the row with ``agent_id = '*'``; a row for the specific
    agent overrides it field by field. A missing database (or a query error)
    falls back to the built-in defaults and logs once -- a safety feature that
    takes the phone line down when it cannot read its own settings is worse than
    the risk it mitigates.
    """
    from app.ai.guardrails import categories as _categories

    if session is None or tenant_id is None:
        return _categories.default_policy()

    try:
        from sqlalchemy import select

        from app.db.monitoring_models import GuardrailPolicy

        rows = (
            await session.scalars(
                select(GuardrailPolicy).where(GuardrailPolicy.tenant_id == tenant_id)
            )
        ).all()
    except Exception as exc:  # pragma: no cover - defensive
        from app.core.logging import log as _log

        _log.warning("guardrails.policy_load_failed", error_type=type(exc).__name__)
        return _categories.default_policy()

    tenant_row = next((row for row in rows if row.agent_id == "*"), None)
    agent_row = next((row for row in rows if agent_id and row.agent_id == agent_id), None)
    return _categories.resolve_policy(tenant_row, agent_row)


async def record_event(
    session,
    *,
    tenant_id,
    call_id=None,
    agent_id: str | None = None,
    stage: str = STAGE_OUTPUT,
    outcome: GuardrailOutcome,
):
    """Persist one block. Only the hash, the length and the category are stored.

    The matched text is deliberately excluded: an audit trail of harmful output
    must not become a second copy of that output, subject to a different
    retention rule than the transcript it came from.
    """
    from app.db.monitoring_models import GuardrailEvent

    row = GuardrailEvent(
        tenant_id=tenant_id,
        call_id=call_id if call_id is not None else None,
        agent_id=agent_id,
        stage=stage,
        reason=outcome.reason[:40],
        category=(outcome.category or None),
        text_hash=outcome.text_hash,
        char_count=int(outcome.char_count),
    )
    session.add(row)
    await session.flush()
    return row
