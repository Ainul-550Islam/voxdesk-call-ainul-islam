"""Interactive handoff assistant (PART 13, P13-09).

When the AI transfers a call, the human who picks up is not a machine: they ask
questions before taking the call. "What does this person want?", "Have they
already paid?", "Is this a callback or a first contact?" If the answer is a
transfer that lands on a person who says "I can't take this", the caller hears
dead air and the business loses the lead.

So the transfer leg gets a *short-lived* assistant whose only job is to brief
the human and take a yes/no:

* it **answers from facts it was given** -- the call summary, the transcript and
  the CRM fields on the call. It never invents an answer; an unknown question
  gets ``None`` and the human is told to hold on;
* it **applies the tenant's accept criteria** (required skill, language,
  business hours, capacity) and the human's own reject reason;
* it **times out into a reject with a reason**, because a silent transfer is
  indistinguishable from a broken one.

Nothing in here calls a model. The assistant's replies are composed from a
small, reviewable rule set -- the same reviewability the rest of the handoff
path is held to. An LLM can be slotted in behind `answer()` later without the
decision logic changing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

ACCEPT = "accept"
REJECT = "reject"

REJECT_REASONS = (
    "timeout",
    "wrong_skill",
    "wrong_language",
    "unavailable",
    "caller_requested_human_refused",
    "policy_blocked",
)

#: Questions the assistant answers without a model, keyed by the words that
#: identify them. Order matters: the first match wins.
QUESTION_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("reason", ("calling about", "reason", "why", "what do they want", "purpose")),
    ("identity", ("who", "name", "caller")),
    ("history", ("called before", "previous", "history", "last time")),
    ("account", ("account", "plan", "customer")),
    ("sentiment", ("angry", "upset", "frustrated", "sentiment", "mood")),
    ("summary", ("summary", "brief", "happen", "context", "catch me up")),
)

#: Spoken when the answer is not in the facts we have. Honest beats fluent.
UNKNOWN_ANSWER = "I don't have that detail -- the caller is on the line, you can ask them directly."

MAX_SUMMARY_CHARS = 320


@dataclass(frozen=True)
class HandoffContext:
    """Everything the assistant is allowed to know about the call."""

    call_id: str
    tenant_id: str = ""
    caller_number: str | None = None
    caller_name: str | None = None
    intent: str | None = None
    summary: str | None = None
    transcript: tuple[str, ...] = ()
    facts: Mapping[str, Any] = field(default_factory=dict)
    sentiment: str | None = None
    previous_calls: int = 0
    requested_human: bool = False

    @classmethod
    def from_call(cls, call: Any, *, transcript: Iterable[str] = ()) -> "HandoffContext":
        facts = dict(getattr(call, "transfer_context", None) or {})
        return cls(
            call_id=str(getattr(call, "call_sid", "") or getattr(call, "id", "")),
            tenant_id=str(getattr(call, "tenant_id", "")),
            caller_number=getattr(call, "from_number", None),
            caller_name=facts.get("caller_name"),
            intent=getattr(call, "intent", None),
            summary=getattr(call, "summary", None),
            transcript=tuple(transcript),
            facts=facts,
            sentiment=facts.get("sentiment"),
            previous_calls=int(facts.get("previous_calls", 0) or 0),
            requested_human=bool(facts.get("requested_human", False)),
        )


@dataclass(frozen=True)
class Criteria:
    """The tenant's rule for whether this human may take the call."""

    required_skills: tuple[str, ...] = ()
    required_languages: tuple[str, ...] = ()
    within_hours: bool = True
    has_capacity: bool = True
    timeout_seconds: int = 25

    def check(self, human: Mapping[str, Any]) -> tuple[bool, str | None]:
        skills = {str(skill).lower() for skill in human.get("skills", ())} if human else set()
        languages = {
            str(language).lower() for language in human.get("languages", ())
        } if human else set()
        missing = [skill for skill in self.required_skills if skill.lower() not in skills]
        if missing:
            return False, "wrong_skill"
        missing_languages = [
            language for language in self.required_languages if language.lower() not in languages
        ]
        if missing_languages:
            return False, "wrong_language"
        if not self.within_hours:
            return False, "policy_blocked"
        if not self.has_capacity:
            return False, "unavailable"
        return True, None


@dataclass(frozen=True)
class Decision:
    accepted: bool
    reason: str | None
    answer: str
    brief: str
    criteria_ok: bool
    matched_skill: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "answer": self.answer,
            "brief": self.brief,
            "matched_skill": self.matched_skill,
        }


def topic_of(question: str) -> str | None:
    text = (question or "").lower()
    for topic, keywords in QUESTION_TOPICS:
        if any(keyword in text for keyword in keywords):
            return topic
    return None


def brief(context: HandoffContext) -> str:
    """The sentence read to the human before they decide."""
    if context.caller_name:
        opener = f"{context.caller_name} is on the line"
    elif context.caller_number:
        opener = f"The caller is {context.caller_number}"
    else:
        opener = "A caller is on the line"

    clauses = []
    if context.intent:
        clauses.append(f"about {context.intent.replace('_', ' ')}")
    if context.sentiment:
        clauses.append(f"and sounds {context.sentiment}")
    sentence = opener + (" " + " ".join(clauses) if clauses else "") + "."
    if context.previous_calls:
        sentence += f" They have called {context.previous_calls} time(s) before."
    if context.summary:
        remaining = MAX_SUMMARY_CHARS - len(sentence) - 1
        if remaining > 40:
            sentence = f"{sentence} {context.summary[:remaining].strip()}"
    return sentence


def answer(context: HandoffContext, question: str) -> str | None:
    """Answer a human's question from the facts on the call, or ``None``.

    Returning ``None`` is a feature: the caller hears "I don't have that
    detail", which is true, instead of a confidently wrong account balance.
    """
    topic = topic_of(question)
    if topic is None:
        return None
    facts = context.facts
    if topic == "reason":
        return (
            str(facts.get("reason"))
            if facts.get("reason")
            else (f"They're calling about {context.intent.replace('_', ' ')}." if context.intent else None)
        )
    if topic == "identity":
        if context.caller_name:
            return f"The caller is {context.caller_name}."
        return None
    if topic == "history":
        if context.previous_calls:
            return f"They have called {context.previous_calls} time(s) before."
        return "This is their first call with us."
    if topic == "account":
        for key in ("account_tier", "plan", "account_status"):
            if facts.get(key):
                return f"{key.replace('_', ' ').capitalize()}: {facts[key]}."
        return None
    if topic == "sentiment":
        if context.sentiment:
            return f"The caller sounds {context.sentiment}."
        return None
    if topic == "summary":
        return context.summary or brief(context)
    return None


class HandoffAssistant:
    """Applies the criteria, answers questions and returns accept/reject."""

    def __init__(self, criteria: Criteria | None = None) -> None:
        self.criteria = criteria or Criteria()

    def evaluate(
        self,
        context: HandoffContext,
        *,
        human: Mapping[str, Any] | None = None,
        question: str | None = None,
        elapsed_seconds: float = 0.0,
        human_decision: str | None = None,
        reject_reason: str | None = None,
    ) -> Decision:
        brief_text = brief(context)
        spoken = answer(context, question) if question else None
        reply = spoken or (UNKNOWN_ANSWER if question else brief_text)

        if elapsed_seconds > self.criteria.timeout_seconds:
            return Decision(False, "timeout", reply, brief_text, False)
        if human_decision == REJECT:
            reason = reject_reason if reject_reason in REJECT_REASONS else "caller_requested_human_refused"
            return Decision(False, reason, reply, brief_text, False)

        ok, reason = self.criteria.check(human or {})
        if not ok:
            return Decision(False, reason, reply, brief_text, False)

        skills = {str(skill).lower() for skill in (human or {}).get("skills", ())}
        matched = next(
            (skill for skill in self.criteria.required_skills if skill.lower() in skills), None
        )
        if human_decision == ACCEPT:
            return Decision(True, None, reply, brief_text, True, matched)
        # No answer yet: the human is still listening. The assistant keeps the
        # line up and does not treat silence as consent.
        return Decision(False, "timeout" if elapsed_seconds >= self.criteria.timeout_seconds else None, reply, brief_text, True, matched)


def script(questions: Iterable[str], context: HandoffContext) -> list[dict[str, str | None]]:
    """Preview tool for the dashboard: what each question would be answered with."""
    return [{"question": q, "answer": answer(context, q)} for q in questions]
