# File: app/alerts/notifier.py — deliver a fired alert to its channels
"""Alert delivery (PART 15, P15-04).

Delivery is separated from evaluation on purpose. The evaluation already
happened and is already stored; a SMTP timeout afterwards must not make the
metric look unevaluated. So this module *annotates* the event with what each
channel did, and downgrades the event to ``delivery_failed`` only when every
configured channel refused or errored.

Channel entries in ``alert_rules.channels`` accept two shapes::

    ["in_app", "webhook"]
    [{"channel": "webhook", "recipient": "https://ops.example/hook"}]

A bare channel name with no recipient is delivered only when the channel needs
none (``in_app``); otherwise the result is ``skipped_missing_recipient`` -- not
a silent success.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.evaluator import STATE_DELIVERY_FAILED, STATE_FIRED, AlertRuleView, describe
from app.core.logging import log
from app.db.monitoring_models import AlertEvent
from app.notifications.delivery import dispatch

#: Channels that need no recipient.
RECIPIENTLESS: frozenset[str] = frozenset({"in_app", "internal_alert"})

STATUS_ACCEPTED = "accepted"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped_missing_recipient"


def parse_channels(channels: Sequence[Any] | None) -> list[tuple[str, str]]:
    """Normalise channel entries into ``(channel, recipient)`` pairs.

    Malformed entries are dropped rather than guessed at; the route validates
    them on write, so anything malformed here was written by an older client.
    """
    pairs: list[tuple[str, str]] = []
    for entry in channels or ():
        if isinstance(entry, str):
            name = entry.strip().lower()
            if name:
                pairs.append((name, ""))
            continue
        if isinstance(entry, Mapping):
            name = str(entry.get("channel") or entry.get("name") or "").strip().lower()
            recipient = str(entry.get("recipient") or entry.get("target") or "").strip()
            if name:
                pairs.append((name, recipient))
    return pairs


def build_body(event: AlertEvent, rule: AlertRuleView) -> str:
    """The message text. Contains the metric and the rule, never call content."""
    observed = event.observed_value
    return (
        f"[VoxDesk alert] {rule.name}\n"
        f"{describe(rule)}\n"
        f"observed: {observed:g} at threshold {rule.comparator} {rule.threshold:g}\n"
        f"rule: {rule.id}  event: {event.id}"
    )


async def deliver(
    session: AsyncSession,
    event: AlertEvent,
    rule: AlertRuleView,
    *,
    adapters: dict | None = None,
) -> list[dict[str, Any]]:
    """Send one event to every configured channel and record what happened."""
    body = build_body(event, rule)
    deliveries: list[dict[str, Any]] = []
    configured = 0
    successful = 0

    for channel, recipient in parse_channels(rule.channels):
        if channel in RECIPIENTLESS and not recipient:
            # The stored event *is* the in-app notification.
            deliveries.append(
                {"channel": channel, "status": STATUS_ACCEPTED, "recipient": "", "detail": "stored"}
            )
            configured += 1
            successful += 1
            continue
        if not recipient:
            deliveries.append(
                {
                    "channel": channel,
                    "status": STATUS_SKIPPED,
                    "recipient": "",
                    "detail": "no recipient configured",
                }
            )
            continue

        configured += 1
        try:
            result = await dispatch(channel, recipient=recipient, body=body, adapters=adapters)
        except Exception as exc:  # a transport crash is a delivery failure, not a crash
            log.warning(
                "alerts.delivery_failed",
                event_id=str(event.id),
                channel=channel,
                error_type=type(exc).__name__,
            )
            deliveries.append(
                {
                    "channel": channel,
                    "status": STATUS_FAILED,
                    "recipient": _mask(recipient),
                    "detail": f"{type(exc).__name__}",
                }
            )
            continue

        outcome = str(getattr(result, "outcome", "") or "")
        category = str(getattr(result, "category", "") or "")
        if outcome == STATUS_ACCEPTED:
            successful += 1
        deliveries.append(
            {
                "channel": channel,
                "status": outcome or STATUS_FAILED,
                "recipient": _mask(recipient),
                "detail": category,
            }
        )

    event.deliveries = deliveries
    if configured and successful == 0 and event.state == STATE_FIRED:
        # Every configured channel refused. The event stays in the table so the
        # breach is not lost, but it no longer claims to have been delivered.
        event.state = STATE_DELIVERY_FAILED

    log.info(
        "alerts.delivered",
        event_id=str(event.id),
        rule_id=str(rule.id),
        channels=len(deliveries),
        successful=successful,
    )
    await session.flush()
    return deliveries


def _mask(recipient: str) -> str:
    """Keep a recipient identifiable in the audit trail without storing secrets.

    A webhook URL contains its own token; an email address is personal data. In
    both cases the host (or the domain) is enough to see *where* it went.
    """
    text = (recipient or "").strip()
    if "://" in text:
        host = text.split("://", 1)[1].split("/", 1)[0]
        return f"webhook:{host}"
    if "@" in text:
        return f"email:@{text.rsplit('@', 1)[1]}"
    if text.startswith("+"):
        return f"sms:+**{text[-2:]}" if len(text) > 2 else "sms:+**"
    return "redacted"
