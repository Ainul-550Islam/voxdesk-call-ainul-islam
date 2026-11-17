# File: app/ai/guardrails/categories.py — harm taxonomy, per-agent policy, block decision
"""Harmful-content categories and the policy that decides what to do (P15-09).

The taxonomy is a fixed list so a tenant's toggles are comparable across
tenants, and so an eval set can report per-category recall instead of one
averaged number that hides the category that regressed.

Detection here is lexical: families of terms plus a few structural checks
(``"how do I make a bomb"`` is an *instruction request*, not a mention of the
word ``bomb``). That is honest about what it can do -- the false-positive guard
is that a bare mention without a request shape is a ``mention`` and does not
block. The eval sets in ``tests/evals/guardrails/`` are what keep this tuneable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

CATEGORIES: tuple[str, ...] = (
    "self_harm",
    "violence",
    "weapons",
    "illegal_activity",
    "sexual_content",
    "child_safety",
    "gambling",
    "hate_speech",
    "harassment",
    "regulated_advice",
)

SEVERITY_BLOCK = "block"
SEVERITY_MENTION = "mention"

#: Categories where a mention *is* the problem. Nobody asks a clinic hotline to
#: "discuss a dosage" and means self-harm, but an expression of self-harm is not
#: an instruction request -- there is no "how" to wait for.
ALWAYS_BLOCK: frozenset[str] = frozenset({"self_harm", "child_safety"})

#: Terms that name the category. Lowercase; matched as substrings after the
#: text is normalised, because callers say "bomb-making" and "bomb making".
TERMS: dict[str, tuple[str, ...]] = {
    "self_harm": (
        "kill myself",
        "suicide",
        "end my life",
        "hurt myself",
        "self harm",
        "want to die",
        "hurt myself without anyone noticing",
    ),
    "violence": (
        "kill you",
        "hurt someone",
        "beat him up",
        "shoot them",
        "stab",
        "murder",
        "make her disappear",
        "make him disappear",
        "threaten someone",
        "threaten them",
    ),
    "weapons": (
        "make a bomb",
        "build a bomb",
        "pipe bomb",
        "ghost gun",
        "silencer",
        "explosive device",
        "untraceable firearm",
    ),
    "illegal_activity": (
        "launder money",
        "tax evasion",
        "buy stolen",
        "sell stolen",
        "hack into",
        "crack the password",
        "fake id",
        "counterfeit",
        "avoid paying tax",
        "sell stolen phones",
    ),
    "sexual_content": (
        "explicit sex",
        "pornographic",
        "pornographic about",
        "sexual roleplay",
        "nude photo",
    ),
    "child_safety": (
        "sexualize a minor",
        "child sexual",
        "meet a child alone",
        "underage nude",
        "grooming a child",
        "groom a child",
    ),
    "gambling": (
        "place a bet for me",
        "guaranteed betting system",
        "rig a slot machine",
        "unlicensed casino",
        "sportsbook arbitrage guarantee",
    ),
    "hate_speech": (
        "ethnic cleansing",
        "subhuman",
        "go back to your country",
        "inferior race",
    ),
    "harassment": (
        "i will find you",
        "i know where you live",
        "keep calling until",
        "harass them",
    ),
    "regulated_advice": (
        "prescribe me",
        "what dosage should i take",
        "guaranteed investment return",
        "insider trading tip",
        "legal advice for my case",
        "legal advice for my",
        "as if you were my lawyer",
    ),
}

#: Request shapes. A term without one of these is a mention, not a request:
#: "my doctor said the dosage" must not be blocked with the same confidence as
#: "what dosage should I take to overdose".
_INSTRUCTION_MARKERS: tuple[str, ...] = (
    "give me",
    "tell me",
    "show me",
    "find me",
    "where can i",
    "help me find",
    "write something",
    "write me",
    "i want to",
    "i need to",
    "can you help me",
    "how do i",
    "how to",
    "how can i",
    "teach me",
    "give me instructions",
    "write me a guide",
    "steps to",
    "help me make",
    "help me build",
    "what dosage",
    "prescribe me",
    "best way to",
    "i want to make",
    "tell me how",
    "instructions for",
    "recipe for",
)

_WORD = re.compile(r"[a-z']+")


def _normalise(text: str) -> str:
    from app.ai.guardrails.jailbreak import normalise

    return normalise(text)


def has_instruction_shape(text: str) -> bool:
    """Does the text ask *how*, rather than merely contain the term?"""
    lowered = _normalise(text)
    return any(marker in lowered for marker in _INSTRUCTION_MARKERS)


@dataclass(frozen=True, slots=True)
class CategoryHit:
    """One category the text touched, and how hard."""

    category: str
    severity: str  # block | mention
    matched: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "severity": self.severity,
            "matched": self.matched,
            "reason": self.reason,
        }


def detect(text: str) -> list[CategoryHit]:
    """All categories the text touches, strongest first.

    A hit is a ``mention`` unless the text also has an instruction shape (or the
    term is inherently an action, like "make a bomb").
    """
    lowered = _normalise(text)
    if not lowered:
        return []
    shaped = has_instruction_shape(lowered)
    hits: list[CategoryHit] = []
    for category, terms in TERMS.items():
        matched = next((term for term in terms if term in lowered), None)
        if matched is None:
            continue
        # Terms that already read as an action do not need a request phrase.
        action_term = matched.split()[0] in {"make", "build", "buy", "sell", "hack", "launder"}
        always = category in ALWAYS_BLOCK
        severity = SEVERITY_BLOCK if (shaped or action_term or always) else SEVERITY_MENTION
        hits.append(
            CategoryHit(
                category=category,
                severity=severity,
                matched=matched,
                reason="instruction_request" if severity == SEVERITY_BLOCK else "term_mention",
            )
        )
    hits.sort(key=lambda hit: (hit.severity != SEVERITY_BLOCK, hit.category))
    return hits


@dataclass(frozen=True, slots=True)
class GuardrailPolicyView:
    """Effective policy for one agent: the tenant default merged with an override."""

    enabled_categories: tuple[str, ...] = CATEGORIES
    block_jailbreak: bool = True
    block_prompt_extraction: bool = True
    hard_stop_phrase: str = "I can't help with that, but I can connect you to a person."
    judge_fallback: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled_categories": list(self.enabled_categories),
            "block_jailbreak": self.block_jailbreak,
            "block_prompt_extraction": self.block_prompt_extraction,
            "hard_stop_phrase": self.hard_stop_phrase,
            "judge_fallback": self.judge_fallback,
        }


def default_policy() -> GuardrailPolicyView:
    return GuardrailPolicyView()


def resolve_policy(
    tenant_row: Mapping[str, Any] | Any | None,
    agent_row: Mapping[str, Any] | Any | None = None,
) -> GuardrailPolicyView:
    """Merge the tenant default with an agent override.

    An agent row that leaves a field empty inherits the tenant's value; an agent
    that sets it wins. ``agent_id == '*'`` deliberately wins over the bare
    tenant row, because that is what the UI's "tenant default" toggle writes.
    """
    policy = default_policy()

    def value(row: Any, key: str, fallback: Any) -> Any:
        if row is None:
            return fallback
        if isinstance(row, Mapping):
            raw = row.get(key)
        else:
            raw = getattr(row, key, None)
        return fallback if raw is None else raw

    sources = [row for row in (tenant_row, agent_row) if row is not None]
    for row in sources:
        raw_categories = value(row, "enabled_categories", None)
        if raw_categories:
            chosen = [str(c) for c in raw_categories if str(c) in CATEGORIES]
            policy = GuardrailPolicyView(
                enabled_categories=tuple(chosen) or policy.enabled_categories,
                block_jailbreak=bool(value(row, "block_jailbreak", policy.block_jailbreak)),
                block_prompt_extraction=bool(
                    value(row, "block_prompt_extraction", policy.block_prompt_extraction)
                ),
                hard_stop_phrase=str(
                    value(row, "hard_stop_phrase", policy.hard_stop_phrase) or policy.hard_stop_phrase
                ),
                judge_fallback=bool(value(row, "judge_fallback", policy.judge_fallback)),
            )
        else:
            policy = GuardrailPolicyView(
                enabled_categories=policy.enabled_categories,
                block_jailbreak=bool(value(row, "block_jailbreak", policy.block_jailbreak)),
                block_prompt_extraction=bool(
                    value(row, "block_prompt_extraction", policy.block_prompt_extraction)
                ),
                hard_stop_phrase=str(
                    value(row, "hard_stop_phrase", policy.hard_stop_phrase) or policy.hard_stop_phrase
                ),
                judge_fallback=bool(value(row, "judge_fallback", policy.judge_fallback)),
            )
    return policy


def blocking_hit(hits: Sequence[CategoryHit], policy: GuardrailPolicyView) -> CategoryHit | None:
    """First blocking hit inside the policy's enabled categories, if any.

    A mention is never blocking; a disabled category is never blocking even when
    the detector is sure. That is the point of a per-agent toggle: a clinic that
    legitimately discusses dosage turns ``regulated_advice`` off and accepts the
    trade.
    """
    enabled = set(policy.enabled_categories)
    for hit in hits:
        if hit.severity != SEVERITY_BLOCK:
            continue
        if hit.category in enabled:
            return hit
    return None


def enabled_categories_validate(values: Iterable[str]) -> list[str]:
    """Filter to known categories, preserving order and dropping duplicates."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        name = str(value).strip().lower()
        if name in CATEGORIES and name not in seen:
            seen.add(name)
            out.append(name)
    return out


@dataclass(frozen=True, slots=True)
class CategoryVerdict:
    """Result of screening one text against the taxonomy."""

    blocked: bool
    hit: CategoryHit | None
    hits: tuple[CategoryHit, ...] = field(default_factory=tuple)
    policy: GuardrailPolicyView = field(default_factory=default_policy)

    def as_dict(self) -> dict[str, Any]:
        return {
            "blocked": self.blocked,
            "hit": self.hit.as_dict() if self.hit else None,
            "hits": [hit.as_dict() for hit in self.hits],
            "policy": self.policy.as_dict(),
        }


def screen(text: str, policy: GuardrailPolicyView | None = None) -> CategoryVerdict:
    """Detect, then apply the policy."""
    effective = policy or default_policy()
    hits = detect(text)
    return CategoryVerdict(
        blocked=blocking_hit(hits, effective) is not None,
        hit=blocking_hit(hits, effective),
        hits=tuple(hits),
        policy=effective,
    )
