import uuid
from datetime import datetime, timezone

import pytest

from app.contact_center.models import Candidate
from app.contact_center_v2.acd.router import choose_agent, cursor_after, select_overflow_action
from app.contact_center_v2.errors import Validation


def candidate(
    number: int,
    *,
    priority: int = 0,
    proficiency: int = 0,
    idle: str | None = None,
    active: int = 0,
    eligible: bool = True,
) -> Candidate:
    return Candidate(
        user_id=uuid.UUID(int=number),
        member_priority=priority,
        proficiency=proficiency,
        skill_count=2,
        active_count=active,
        last_assigned_at=None if idle is None else datetime.fromisoformat(idle).replace(tzinfo=timezone.utc),
        eligible=eligible,
        reason="" if eligible else "offline",
    )


def test_skill_priority_longest_idle_and_round_robin_are_deterministic():
    skilled = candidate(1, proficiency=9, priority=1, idle="2026-10-25T11:00:00")
    priority = candidate(2, proficiency=2, priority=10, idle="2026-10-25T10:00:00")
    idle = candidate(3, proficiency=1, priority=1, idle=None)
    rows = [skilled, priority, idle]
    assert choose_agent(rows, strategy="skill_first", required_skills=["billing"]).selected == skilled
    assert choose_agent(rows, strategy="priority_first", required_skills=[]).selected == priority
    assert choose_agent(rows, strategy="longest_idle", required_skills=[]).selected == idle
    assert choose_agent(rows, strategy="least_loaded", required_skills=[]).selected == idle

    ordered = sorted(rows, key=lambda row: str(row.user_id))
    first = choose_agent(rows, strategy="round_robin", required_skills=[]).selected
    assert first == ordered[0]
    second = choose_agent(
        rows, strategy="round_robin", required_skills=[], cursor=cursor_after(first.user_id)
    ).selected
    assert second == ordered[1]


def test_overflow_thresholds_keep_destinations_and_reject_bad_measurements():
    chain = [
        {"kind": "escalate", "after_seconds": 30, "queue_id": "queue-escalation"},
        {"kind": "callback", "waiting": 2, "after_seconds": 0},
    ]
    assert select_overflow_action(elapsed_seconds=29, depth=0, overflow_chain=chain) is None
    assert select_overflow_action(elapsed_seconds=30, depth=0, overflow_chain=chain) == {
        "kind": "escalate", "after_seconds": 30, "queue_id": "queue-escalation",
    }
    assert select_overflow_action(elapsed_seconds=1, depth=3, overflow_chain=chain)["kind"] == "callback"
    assert select_overflow_action(
        elapsed_seconds=300, depth=0, max_wait_seconds=300, fallback_kind="voicemail"
    ) == {"kind": "voicemail"}
    with pytest.raises(Validation):
        select_overflow_action(elapsed_seconds=-1, depth=0)
    with pytest.raises(Validation):
        select_overflow_action(elapsed_seconds=1, depth=0, overflow_chain=[{"kind": "unknown", "after_seconds": 1}])
