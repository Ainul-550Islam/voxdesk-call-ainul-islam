"""A2P 10DLC and toll-free messaging registration (PART 13, P13-08).

Registration is not a form, it is a state machine the carrier owns and we
mirror. Two things follow from that:

* **order matters** -- a campaign cannot be submitted before its brand is
  approved, so the wizard is a *checklist with a server-side gate*, not four
  independent forms;
* **events arrive out of order** -- carriers retry webhooks, so a late
  ``PENDING`` must never drag an ``APPROVED`` brand backwards. The transition
  table below encodes "forward only, terminal states are final".

Every validation here mirrors a real rejection reason (EIN format, sample
messages containing a STOP instruction, opt-in description that is vague). We
would rather fail the request in our own UI, where we can explain it, than
after two weeks in the carrier's queue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

KINDS = ("a2p_brand", "a2p_campaign", "toll_free_verification")

DRAFT = "draft"
SUBMITTED = "submitted"
IN_REVIEW = "in_review"
APPROVED = "approved"
REJECTED = "rejected"
SUSPENDED = "suspended"

STATES = (DRAFT, SUBMITTED, IN_REVIEW, APPROVED, REJECTED, SUSPENDED)
TERMINAL = frozenset({APPROVED, REJECTED})

#: Forward-only edges. A retried webhook that asks for an edge not in the
#: table is *ignored*, and the caller gets ``applied=False`` plus the reason.
TRANSITIONS: dict[str, frozenset[str]] = {
    DRAFT: frozenset({SUBMITTED}),
    SUBMITTED: frozenset({IN_REVIEW, APPROVED, REJECTED}),
    IN_REVIEW: frozenset({APPROVED, REJECTED}),
    APPROVED: frozenset({SUSPENDED}),
    REJECTED: frozenset(),
    SUSPENDED: frozenset({APPROVED}),
}

#: Which registration must be approved before the next one may be submitted.
PREREQUISITES = {
    "a2p_brand": (),
    "a2p_campaign": ("a2p_brand",),
    "toll_free_verification": (),
}

#: Carrier status strings, normalised. Twilio sends upper case; the toll-free
#: API sends lower case with different words for the same thing.
CARRIER_STATUS_MAP = {
    "PENDING": IN_REVIEW,
    "IN_REVIEW": IN_REVIEW,
    "SUBMITTED": SUBMITTED,
    "APPROVED": APPROVED,
    "VERIFIED": APPROVED,
    "ACCEPTED": APPROVED,
    "REJECTED": REJECTED,
    "FAILED": REJECTED,
    "DECLINED": REJECTED,
    "SUSPENDED": SUSPENDED,
}

EIN_PATTERN = re.compile(r"^\d{2}-?\d{7}$")
#: Short codes are space-limited; a sample that exceeds this is auto-rejected.
MAX_SAMPLE_CHARS = 320
OPT_OUT_WORDS = ("stop", "unsubscribe", "opt out", "opt-out", "cancel")


class RegistrationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Transition:
    applied: bool
    state: str
    previous: str
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "state": self.state,
            "previous": self.previous,
            "reason": self.reason,
        }


def normalise_status(raw: str) -> str:
    key = (raw or "").strip().upper().replace(" ", "_").replace("-", "_")
    if key not in CARRIER_STATUS_MAP:
        raise RegistrationError("unknown_status", f"carrier status {raw!r} is not recognised")
    return CARRIER_STATUS_MAP[key]


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


def apply_carrier_status(current: str, raw_status: str) -> Transition:
    """Mirror one carrier status onto our state, forward only."""
    target = normalise_status(raw_status)
    if target == current:
        return Transition(False, current, current, reason="duplicate event")
    if not can_transition(current, target):
        reason = (
            "terminal state is final" if current in TERMINAL and target != SUSPENDED else
            f"{current} cannot move to {target}"
        )
        return Transition(False, current, current, reason=reason)
    return Transition(True, target, current)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)

    def raise_if_invalid(self, kind: str) -> None:
        if not self.ok:
            raise RegistrationError("invalid_payload", f"{kind}: " + "; ".join(self.errors))


def _present(payload: Mapping[str, Any], *keys: str) -> list[str]:
    return [key for key in keys if not str(payload.get(key, "")).strip()]


def _result(errors: list[str]) -> ValidationResult:
    return ValidationResult(ok=not errors, errors=errors)


def validate_business_profile(payload: Mapping[str, Any]) -> ValidationResult:
    errors = []
    missing = _present(
        payload, "legal_business_name", "business_type", "website", "contact_email", "ein"
    )
    errors += [f"{key} is required" for key in missing]
    ein = str(payload.get("ein", "")).strip()
    if ein and not EIN_PATTERN.match(ein):
        errors.append("ein must be 9 digits (XX-XXXXXXX)")
    state = str(payload.get("state", "")).strip()
    if payload.get("country", "US") == "US" and state and len(state) != 2:
        errors.append("state must be a two-letter US code")
    return _result(errors)


def validate_brand(payload: Mapping[str, Any]) -> ValidationResult:
    errors = []
    errors += [f"{key} is required" for key in _present(payload, "brand_name", "business_profile_id")]
    vertical = payload.get("vertical")
    if vertical is None:
        errors.append("vertical is required")
    elif vertical not in ("TECHNOLOGY", "HEALTHCARE", "FINANCE", "RETAIL", "OTHER"):
        errors.append(f"vertical {vertical!r} is not a 10DLC vertical")
    return _result(errors)


def validate_campaign(payload: Mapping[str, Any]) -> ValidationResult:
    errors = []
    errors += [
        f"{key} is required"
        for key in _present(payload, "brand_id", "description", "message_flow", "opt_in_flow")
    ]
    samples = payload.get("sample_messages") or []
    if len(samples) < 2:
        errors.append("at least two sample messages are required")
    for index, sample in enumerate(samples):
        text = str(sample)
        if len(text) > MAX_SAMPLE_CHARS:
            errors.append(f"sample {index} exceeds {MAX_SAMPLE_CHARS} characters")
        if not any(word in text.lower() for word in OPT_OUT_WORDS):
            errors.append(f"sample {index} must show an opt-out word such as STOP")
    if payload.get("contains_embedded_links") and not payload.get("link_domain"):
        errors.append("embedded links need a link_domain")
    use_cases = payload.get("use_cases") or []
    if not use_cases:
        errors.append("at least one use case is required")
    return _result(errors)


def validate_toll_free(payload: Mapping[str, Any]) -> ValidationResult:
    errors = []
    errors += [f"{key} is required" for key in _present(payload, "phone_number", "business_name")]
    number = str(payload.get("phone_number", ""))
    if number and not (number.startswith("+1") and len(number) == 12):
        errors.append("a toll-free verification needs a +1 NXX-NXX-XXXX number")
    samples = payload.get("sample_messages") or []
    if not samples:
        errors.append("at least one sample message is required")
    return _result(errors)


VALIDATORS = {
    "a2p_brand": validate_brand,
    "a2p_campaign": validate_campaign,
    "toll_free_verification": validate_toll_free,
}


def validate(kind: str, payload: Mapping[str, Any]) -> ValidationResult:
    if kind not in KINDS:
        raise RegistrationError("invalid_kind", f"unknown registration kind {kind!r}")
    return VALIDATORS[kind](payload)


def blocked_by(kind: str, states: Mapping[str, str]) -> list[str]:
    """Which prerequisite registrations are not approved yet."""
    return [
        prerequisite
        for prerequisite in PREREQUISITES.get(kind, ())
        if states.get(prerequisite) != APPROVED
    ]


def next_step(kind: str, state: str, states: Mapping[str, str] | None = None) -> str:
    """One sentence the wizard shows the operator. No carrier jargon."""
    states = states or {}
    if state == DRAFT:
        waiting = blocked_by(kind, states)
        if waiting:
            return f"Waiting for {', '.join(waiting)} to be approved before this can be submitted."
        return "Ready to submit; fill in the remaining fields and send it to the carrier."
    if state == SUBMITTED:
        return "Sent to the carrier. Nothing to do until the review comes back."
    if state == IN_REVIEW:
        return "The carrier is reviewing this. It usually takes 1-3 business days."
    if state == APPROVED:
        return "Approved. You can send messages on this registration."
    if state == REJECTED:
        return "Rejected. Fix the reason the carrier gave and submit a new registration."
    if state == SUSPENDED:
        return "Suspended by the carrier. Contact support before sending more messages."
    raise RegistrationError("invalid_state", f"unknown state {state!r}")


@dataclass(frozen=True)
class RegistrationPlan:
    """What the wizard should do next, computed from the current rows."""

    kind: str
    allowed: bool
    blockers: list[str]
    next_step: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "allowed": self.allowed,
            "blockers": list(self.blockers),
            "next_step": self.next_step,
        }


def plan_submission(kind: str, states: Mapping[str, str], payload: Mapping[str, Any]) -> RegistrationPlan:
    blockers = blocked_by(kind, states)
    validation = validate(kind, payload)
    blockers += validation.errors
    return RegistrationPlan(kind, not blockers, blockers, next_step(kind, states.get(kind, DRAFT), states))


def sms_send_permitted(states: Mapping[str, str], *, toll_free: bool = False) -> tuple[bool, str]:
    """Gate one outbound SMS on registration state.

    Carriers fine the *sender* for traffic on an unregistered brand, so this is
    a hard block rather than a warning.
    """
    required = "toll_free_verification" if toll_free else "a2p_campaign"
    state = states.get(required, DRAFT)
    if state == APPROVED:
        return True, "approved"
    return False, f"{required} is {state}; messaging is blocked until it is approved"


def summarise(rows: Iterable[Any]) -> dict[str, Any]:
    """Roll the registrations up for the dashboard header."""
    counts: dict[str, int] = {}
    states: dict[str, str] = {}
    for row in rows:
        counts[row.state] = counts.get(row.state, 0) + 1
        states[row.kind] = row.state
    return {"counts": counts, "states": states, "kinds": list(PREREQUISITES)}
