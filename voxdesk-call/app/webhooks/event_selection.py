"""Tenant-selectable webhook events (PART 14, P14-09).

A subscription stores a list of event names. `matches_subscription` already
handles the aliases; what was missing is the *validation* at the point a tenant
saves a selection, and the *fan-out* decision at the point an event is emitted.
Both live here so the two can never disagree:

* `validate_selection` refuses an unknown event instead of storing it and
  silently never delivering it;
* `select_endpoints` decides which of a tenant's endpoints want this event,
  including the legacy aliases and status-dependent ones;
* `describe` produces the list the dashboard shows, with examples.

An endpoint subscribed to `["*"]` gets everything, but the wildcard is expanded
to the real event list before it is stored, so "everything" means the events
that existed when it was saved -- a new event type cannot silently start
arriving at a customer's URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from app.webhooks.call_event_catalog import (
    EVENT_NAMES,
    VERSION,
    matches_subscription,
    schema_for,
)

WILDCARD = "*"
TOPICS = {
    "calls": ("call_started", "call_ended", "call_analyzed"),
    "transfers": ("transfer_started", "transfer_completed", "transfer_failed"),
    "recordings": ("recording_ready",),
    "media": ("voicemail_detected", "dtmf_received"),
    "batches": ("batch_started", "batch_completed"),
    "agents": ("agent_published",),
}


class SelectionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def expand(filters: Iterable[str]) -> list[str]:
    """Resolve topics and the wildcard into canonical event names."""
    chosen: list[str] = []
    for value in filters:
        if value == WILDCARD:
            chosen.extend(sorted(EVENT_NAMES))
            continue
        if value in TOPICS:
            chosen.extend(TOPICS[value])
            continue
        chosen.append(value)
    # Preserve order, drop duplicates: a stable list makes the stored JSON stable
    # and therefore diffable in an audit log.
    return list(dict.fromkeys(chosen))


def validate_selection(filters: Sequence[str]) -> list[str]:
    """Canonicalise a proposed subscription or say exactly what is wrong."""
    if not filters:
        raise SelectionError("empty_selection", "select at least one event")
    unknown = [value for value in filters if value not in EVENT_NAMES and value not in TOPICS and value != WILDCARD]
    if unknown:
        raise SelectionError(
            "unknown_events",
            f"unknown event(s): {', '.join(sorted(unknown)[:5])}",
        )
    resolved = expand(filters)
    if not resolved:
        raise SelectionError("empty_selection", "the selection resolved to no events")
    return resolved


def accepts(filters: Sequence[str], event: str, payload: Mapping[str, Any] | None = None) -> bool:
    """Whether this subscription wants `event`.

    Topics and the wildcard are expanded first: a stored `["calls"]` must behave
    exactly like the three event names it stands for, or an endpoint subscribed
    by topic would silently receive nothing.
    """
    if event not in EVENT_NAMES:
        return False
    return matches_subscription(event, expand(filters), dict(payload or {}))


@dataclass(frozen=True)
class Endpoint:
    id: str
    url: str
    events: tuple[str, ...]

    def wants(self, event: str, payload: Mapping[str, Any] | None = None) -> bool:
        return accepts(self.events, event, payload)


def select_endpoints(
    endpoints: Iterable[Endpoint], event: str, payload: Mapping[str, Any] | None = None
) -> list[Endpoint]:
    return [endpoint for endpoint in endpoints if endpoint.wants(event, payload)]


def describe() -> dict[str, Any]:
    """The event catalogue as the dashboard renders it."""
    return {
        "version": VERSION,
        "events": sorted(EVENT_NAMES),
        "topics": {name: list(events) for name, events in TOPICS.items()},
        "documents": {event: schema_for(event) for event in sorted(EVENT_NAMES)},
    }
