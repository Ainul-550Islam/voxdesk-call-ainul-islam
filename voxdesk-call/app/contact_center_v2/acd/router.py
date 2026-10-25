"""Pure CC2 routing and overflow decisions over canonical ACD primitives."""
from __future__ import annotations

from typing import Any

from app.contact_center.models import Candidate, RouteChoice
from app.contact_center.routing import choose, cursor_after
from app.contact_center_v2.errors import Validation

from .queue_manager import route_queue

OVERFLOW_ACTIONS = frozenset({"callback", "queue", "escalate", "voicemail", "ai_agent"})


def choose_agent(
    candidates: list[Candidate],
    *,
    strategy: str,
    required_skills: list[str],
    cursor: str = "",
) -> RouteChoice:
    """Use the canonical skill/priority/longest-idle deterministic chooser."""
    return choose(
        candidates,
        strategy=strategy,
        required_skills=required_skills,
        cursor=cursor,
    )


def _threshold_due(
    *,
    elapsed_seconds: float,
    depth: int,
    after_seconds: int,
    waiting: int,
) -> bool:
    timed = after_seconds > 0 and elapsed_seconds >= after_seconds
    deep = waiting > 0 and depth > waiting
    return timed or deep


def select_overflow_action(
    *,
    elapsed_seconds: float,
    depth: int,
    default_kind: str = "none",
    default_after_seconds: int = 0,
    default_waiting: int = 0,
    max_wait_seconds: int = 300,
    overflow_chain: list[dict[str, Any]] | None = None,
    fallback_kind: str = "callback",
) -> dict[str, Any] | None:
    """Return the first due overflow action, or ``None`` while still queued.

    The function has no database or clock dependency. Ordered chain steps are
    considered before the max-wait fallback, and every returned queue/escalate
    action retains its explicit destination for the durable executor.
    """
    if elapsed_seconds < 0 or depth < 0:
        raise Validation("Overflow measurements cannot be negative")
    if default_kind != "none" and default_kind not in OVERFLOW_ACTIONS:
        raise Validation("Unknown overflow action")
    if fallback_kind not in OVERFLOW_ACTIONS:
        raise Validation("Unknown queue fallback")
    if default_kind != "none" and _threshold_due(
        elapsed_seconds=elapsed_seconds,
        depth=depth,
        after_seconds=default_after_seconds,
        waiting=default_waiting,
    ):
        return {"kind": default_kind}
    for raw in list(overflow_chain or [])[:10]:
        kind = str(raw.get("kind") or "")
        after = int(raw.get("after_seconds", 0))
        waiting = int(raw.get("waiting", 0))
        if kind not in OVERFLOW_ACTIONS or after < 0 or waiting < 0 or not (after or waiting):
            raise Validation("Each overflow-chain step needs a valid kind and positive threshold")
        if _threshold_due(
            elapsed_seconds=elapsed_seconds,
            depth=depth,
            after_seconds=after,
            waiting=waiting,
        ):
            action = dict(raw)
            action["kind"] = kind
            return action
    if _threshold_due(
        elapsed_seconds=elapsed_seconds,
        depth=depth,
        after_seconds=max_wait_seconds,
        waiting=default_waiting,
    ):
        return {"kind": fallback_kind}
    return None


async def route_next(*args: Any, **kwargs: Any):
    """Route through the canonical durable queue and softphone bridge."""
    return await route_queue(*args, **kwargs)


__all__ = [
    "OVERFLOW_ACTIONS",
    "choose",
    "choose_agent",
    "cursor_after",
    "route_next",
    "route_queue",
    "select_overflow_action",
]
