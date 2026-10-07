"""Deterministic routing.

Tie-break is always a stable user id. Nothing here uses random choice, and
nothing here writes a row. The service commits the choice.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.contact_center.exceptions import InvalidSkill
from app.contact_center.models import STRATEGIES, Candidate, RouteChoice


def choose(
    candidates: list[Candidate],
    *,
    strategy: str,
    required_skills: list[str],
    cursor: str = "",
) -> RouteChoice:
    name = (strategy or "least_loaded").strip().lower()
    if name not in STRATEGIES:
        raise InvalidSkill(f"Unknown routing strategy {strategy}")
    rejected = [item.as_rejected() for item in candidates if not item.eligible]
    eligible = [item for item in candidates if item.eligible]
    selected = _pick(eligible, name, cursor)
    return RouteChoice(
        selected=selected,
        rejected=rejected,
        strategy=name,
        matched_skills=list(required_skills),
    )


def _pick(eligible: list[Candidate], strategy: str, cursor: str) -> Candidate | None:
    if not eligible:
        return None
    if strategy == "round_robin":
        ordered = sorted(eligible, key=lambda item: str(item.user_id))
        nxt = [item for item in ordered if str(item.user_id) > cursor]
        return (nxt or ordered)[0]
    return sorted(eligible, key=lambda item: _key(item, strategy))[0]


def _idle(value: datetime | None) -> tuple:
    if value is None:
        return (0, 0.0)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (1, value.timestamp())


def _key(item: Candidate, strategy: str) -> tuple:
    user = str(item.user_id)
    if strategy == "skill_first":
        return (-item.proficiency, -item.skill_count, -item.member_priority, _idle(item.last_assigned_at), user)
    if strategy == "priority_first":
        return (-item.member_priority, -item.skill_count, -item.proficiency, user)
    if strategy == "longest_idle":
        return (_idle(item.last_assigned_at), user)
    return (item.active_count, _idle(item.last_assigned_at), user)


def cursor_after(selected: uuid.UUID | None) -> str:
    return "" if selected is None else str(selected)
