"""Per-tenant collaboration notification rules with safe templating, dedupe window, and rate limiting."""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.integrations_v2_models import IntegrationConnection, NotificationRule
from app.integrations_v2.collab.slack import SlackProvider, build_slack_call_blocks
from app.integrations_v2.collab.teams import (
    TeamsProvider,
    build_teams_call_adaptive_card,
)
from app.integrations_v2.core.errors import IntegrationV2Error, RateLimited, redact_secrets
from app.integrations_v2.register_all import instantiate_provider_v2

SUPPORTED_NOTIFICATION_EVENTS: frozenset[str] = frozenset(
    {
        "call_failed",
        "escalation",
        "voicemail",
        "negative_sentiment",
        "sla_breach",
        "transfer_requested",
        # Legacy dotted aliases
        "call.missed",
        "call.escalated",
        "call.completed",
        "lead.hot",
        "voicemail.received",
        "qa.alert",
    }
)

_TEMPLATE_TOKEN_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}|\{([a-zA-Z0-9_]+)\}")

# Monotonic state for dedupe and per-rule 60s sliding-window rate limits
_DEDUPE_CACHE: dict[str, float] = {}
_RATE_WINDOWS: dict[str, list[float]] = {}


def reset_notification_rule_windows() -> None:
    _DEDUPE_CACHE.clear()
    _RATE_WINDOWS.clear()


def render_notification_template(template: str, context: dict[str, Any]) -> str:
    """Safely substitute ``{var}`` or ``{{var}}`` placeholders without ``str.format`` attribute traversal."""
    if not template:
        summary = str(context.get("summary") or context.get("message") or "Event triggered")
        event = str(context.get("event_type") or "alert")
        caller = str(context.get("caller_phone") or "")
        suffix = f" (caller: {caller})" if caller else ""
        return redact_secrets(f"[{event}] {summary}{suffix}", max_len=2000)

    def _repl(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2) or ""
        val = context.get(key, "")
        return redact_secrets(str(val if val is not None else ""), max_len=500)

    return redact_secrets(_TEMPLATE_TOKEN_RE.sub(_repl, template), max_len=2000)


render_safe_template = render_notification_template


@dataclass(frozen=True)
class NotificationDeliveryOutcome:
    rule_id: str
    provider: str
    channel_id: str
    status: str  # "sent" | "deduped" | "rate_limited"
    message_id: str = ""
    rendered_text: str = ""


def _check_dedupe_and_rate(
    rule: NotificationRule,
    *,
    dedupe_fingerprint: str,
    now_mono: float,
) -> str | None:
    dedupe_sec = max(0, int(rule.dedupe_window_seconds or 0))
    if dedupe_sec > 0:
        dkey = f"{rule.id}:{dedupe_fingerprint}"
        prev = _DEDUPE_CACHE.get(dkey)
        if prev is not None and (now_mono - prev) < dedupe_sec:
            return "deduped"

    rpm = max(1, int(rule.rate_limit_per_min or 30))
    rkey = str(rule.id)
    hits = [t for t in _RATE_WINDOWS.get(rkey, []) if (now_mono - t) < 60.0]
    if len(hits) >= rpm:
        _RATE_WINDOWS[rkey] = hits
        return "rate_limited"

    hits.append(now_mono)
    _RATE_WINDOWS[rkey] = hits
    if dedupe_sec > 0:
        _DEDUPE_CACHE[f"{rule.id}:{dedupe_fingerprint}"] = now_mono
    return None


async def dispatch_notification_rule(
    session: AsyncSession,
    *,
    rule: NotificationRule,
    connection: IntegrationConnection,
    context: dict[str, Any],
    dedupe_key: str = "",
    http_client: Any = None,
    now_mono: float | None = None,
    now_ts: float | None = None,
) -> NotificationDeliveryOutcome:
    """Send one formatted notification to Slack or Microsoft Teams, honoring dedupe & rate limits."""
    ts = now_ts if now_ts is not None else (time.monotonic() if now_mono is None else now_mono)
    rendered = render_notification_template(rule.template, context)
    fp_src = dedupe_key or f"{rule.event_type}:{context.get('call_id', '')}:{rendered}"
    fp = hashlib.sha256(fp_src.encode("utf-8")).hexdigest()[:32]

    blocked = _check_dedupe_and_rate(rule, dedupe_fingerprint=fp, now_mono=ts)
    if blocked == "deduped":
        return NotificationDeliveryOutcome(
            rule_id=str(rule.id),
            provider=connection.provider,
            channel_id=rule.channel_id,
            status="deduplicated",
            rendered_text=rendered,
        )
    if blocked == "rate_limited":
        return NotificationDeliveryOutcome(
            rule_id=str(rule.id),
            provider=connection.provider,
            channel_id=rule.channel_id,
            status="rate_limited",
            rendered_text=rendered,
        )

    provider = instantiate_provider_v2(connection, http_client=http_client)
    title = str(context.get("title") or f"VoxDesk {rule.event_type}")
    caller = str(context.get("caller_phone") or "")

    if isinstance(provider, SlackProvider):
        blocks = build_slack_call_blocks(
            header=title,
            summary=rendered,
            caller_phone=caller,
            disposition=rule.event_type,
            call_id=str(context.get("call_id") or ""),
        )
        sent = await provider.post_message(
            channel=rule.channel_id, text=rendered, blocks=blocks
        )
        return NotificationDeliveryOutcome(
            rule_id=str(rule.id),
            provider="slack",
            channel_id=rule.channel_id,
            status="sent",
            message_id=str(sent.get("ts") or ""),
            rendered_text=rendered,
        )

    if isinstance(provider, TeamsProvider):
        if not rule.team_id:
            raise IntegrationV2Error(
                "Teams notification rule requires 'team_id'", provider="teams"
            )
        card = build_teams_call_adaptive_card(
            title=title,
            summary=rendered,
            caller_phone=caller,
            disposition=rule.event_type,
        )
        msg_id = await provider.post_channel_adaptive_card(
            team_id=rule.team_id,
            channel_id=rule.channel_id,
            card=card,
            summary_text=title,
        )
        return NotificationDeliveryOutcome(
            rule_id=str(rule.id),
            provider="teams",
            channel_id=rule.channel_id,
            status="sent",
            message_id=msg_id,
            rendered_text=rendered,
        )

    raise IntegrationV2Error(
        f"Provider {connection.provider!r} is not supported for channel notification rules",
        provider=connection.provider,
    )


async def route_tenant_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
    http_client: Any = None,
    now_mono: float | None = None,
) -> list[NotificationDeliveryOutcome]:
    """Match enabled tenant ``NotificationRule`` rows for ``event_type`` and deliver each."""
    if event_type not in SUPPORTED_NOTIFICATION_EVENTS:
        raise IntegrationV2Error(f"Unsupported notification event_type {event_type!r}")

    stmt = (
        select(NotificationRule, IntegrationConnection)
        .join(
            IntegrationConnection,
            IntegrationConnection.id == NotificationRule.connection_id,
        )
        .where(
            NotificationRule.tenant_id == tenant_id,
            NotificationRule.event_type == event_type,
            NotificationRule.enabled.is_(True),
            IntegrationConnection.tenant_id == tenant_id,
            IntegrationConnection.status == "active",
        )
    )
    pairs = (await session.execute(stmt)).all()
    ctx = {"event_type": event_type, **payload}
    outcomes: list[NotificationDeliveryOutcome] = []
    for rule, conn in pairs:
        try:
            outcomes.append(
                await dispatch_notification_rule(
                    session,
                    rule=rule,
                    connection=conn,
                    context=ctx,
                    http_client=http_client,
                    now_mono=now_mono,
                )
            )
        except RateLimited:
            outcomes.append(
                NotificationDeliveryOutcome(
                    rule_id=str(rule.id),
                    provider=conn.provider,
                    channel_id=rule.channel_id,
                    status="rate_limited",
                )
            )
    return outcomes
