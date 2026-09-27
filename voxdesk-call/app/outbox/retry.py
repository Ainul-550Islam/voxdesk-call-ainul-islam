"""Outbox delivery retry policy — an adapter, not a second framework.

The classification and the delay curve already exist in
``app.webhooks.retry`` (the module that has always governed outbound
webhook redelivery: 429/5xx/408 retryable, other 4xx permanent, bounded
exponential delay with a deterministic hash-based spread). Outbox delivery
*is* webhook delivery, so this module delegates every decision to it and
adds only the outbox-side mapping:

* ``app.webhooks.delivery.HttpResult`` kinds → the job platform's
  ``FailureClass`` vocabulary, so a delivery outcome and a handler exception
  classify identically downstream;
* the attempt budget check for *events* (an event exhausts when its attempt
  count reaches ``max_attempts`` — the same ceiling the webhook ledger uses,
  ``MAX_ATTEMPTS = 8``);
* the delay used to push ``OutboxEvent.available_at`` forward between
  dispatch rounds.

What is retryable (per the existing provider contract this batch must not
rewrite): HTTP timeouts, network/transport failures, 408, 425-style
rate-limit responses (429) and 5xx — temporary conditions where a later
attempt can genuinely succeed. What is never retried: permanent 4xx
validation/authentication failures, redirects (rejected by the SSRF guard)
and invalid endpoints — retrying those would burn attempts against a
condition that cannot change by itself.
"""

from __future__ import annotations

from app.jobs.types import FailureClass
from app.outbox.models import MAX_DELIVERY_ATTEMPTS
from app.webhooks import retry as webhook_retry

#: Re-exported so the outbox and the webhook ledger exhaust together
#: (``outbox.models.MAX_DELIVERY_ATTEMPTS`` is set to the same ceiling).
MAX_ATTEMPTS = webhook_retry.MAX_ATTEMPTS


def classify_http(status_code: int) -> str:
    """``'success' | 'retryable' | 'permanent'`` for one HTTP status."""
    return webhook_retry.classify_status(status_code)


def classify_network(category: str) -> str:
    """Classification for a transport-level failure category."""
    return webhook_retry.classify_network(category)


def classify_kind(kind: str) -> FailureClass | None:
    """Map an ``HttpResult.kind`` onto the job failure vocabulary.

    ``None`` for success — a delivered event has no failure class.
    """
    if kind == "success":
        return None
    if kind == "retryable":
        return FailureClass.TRANSIENT
    return FailureClass.PERMANENT


def next_delivery_delay(attempt: int, *, key: str = "") -> int:
    """Seconds until the next delivery round for one event.

    Delegates to the existing bounded exponential curve (30s doubling to a
    3600s cap) with the deterministic per-key spread, so two dispatchers
    compute the same schedule for the same event.
    """
    return webhook_retry.next_delay(attempt, key=key)


def should_retry_delivery(kind: str, attempt: int, *, max_attempts: int = MAX_ATTEMPTS) -> bool:
    """May this event have another delivery round? Bounded — never a loop."""
    return webhook_retry.should_retry(kind, attempt) and attempt < max(1, int(max_attempts))


def is_exhausted(attempt_count: int, *, max_attempts: int = MAX_DELIVERY_ATTEMPTS) -> bool:
    """True when the event's attempt budget is spent (→ dead-letter)."""
    return attempt_count >= max(1, int(max_attempts))
