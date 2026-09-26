"""Output checks. Unusual text is kept unless a configured rule rejects it."""

from __future__ import annotations

from dataclasses import dataclass

SAFE_FAILURE = "I can't complete that request."


@dataclass(frozen=True)
class OutputDecision:
    allowed: bool
    reason: str
    text: str

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "reason": self.reason}


def enforce(
    text: str,
    *,
    required_keys: tuple[str, ...] = (),
    blocked_substrings: tuple[str, ...] = (),
    tool_call: dict | None = None,
) -> OutputDecision:
    """Runtime entry. Blocked text is replaced; it is not returned to the caller."""
    return check(
        text,
        required_keys=required_keys,
        blocked_substrings=blocked_substrings,
        tool_call=tool_call,
    )


def check(
    text: str,
    *,
    required_keys: tuple[str, ...] = (),
    blocked_substrings: tuple[str, ...] = (),
    tool_call: dict | None = None,
) -> OutputDecision:
    if tool_call is not None:
        name = tool_call.get("name")
        args = tool_call.get("args")
        if not isinstance(name, str) or not name or not isinstance(args, dict):
            return OutputDecision(False, "malformed_tool_call", SAFE_FAILURE)
    if required_keys:
        if not isinstance(text, str):
            return OutputDecision(False, "schema", SAFE_FAILURE)
    lowered = (text or "").lower()
    for needle in blocked_substrings:
        if needle and needle.lower() in lowered:
            return OutputDecision(False, "blocked_category", SAFE_FAILURE)
    return OutputDecision(True, "accepted", text or "")
