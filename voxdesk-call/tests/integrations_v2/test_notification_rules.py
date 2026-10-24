"""Tests for NotificationRule safe template rendering, dedupe window, rate-limiting, and real Slack/Teams dispatch."""

from __future__ import annotations

import json

import httpx
import pytest

from app.db.integrations_v2_models import NotificationRule
from app.integrations_v2.collab.notification_rules import (
    dispatch_notification_rule,
    render_safe_template,
)
from app.integrations_v2.core.connection_store import upsert_connection
from tests.conftest import make_tenant
from tests.integrations_v2.conftest import make_protocol_http_client


def test_safe_template_renders_variables_without_eval():
    out = render_safe_template(
        "[{{event_type}}] Caller {{caller_phone}} — {{unknown_var}} — {{__import__('os')}}",
        {"event_type": "escalation", "caller_phone": "+14155550100"},
    )
    assert out.startswith("[escalation] Caller +14155550100 —  — {{__import__('os')}}")


@pytest.mark.asyncio
async def test_notification_rule_dispatch_dedupe_and_rate_limit(db):
    tenant = await make_tenant(db, "Notif Rule Tenant")
    conn = await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="slack",
        category="collab",
        auth_kind="oauth2",
        credentials={"access_token": "xoxb-notif-tok"},
    )
    rule = NotificationRule(
        tenant_id=tenant.id,
        connection_id=conn.id,
        event_type="escalation",
        channel_id="C999",
        template="[{{event_type}}] {{summary}}",
        rate_limit_per_min=2,
        dedupe_window_seconds=120,
        enabled=True,
    )
    db.add(rule)
    await db.commit()

    posted_messages: list[dict] = []

    def slack_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/chat.postMessage":
            posted_messages.append(json.loads(req.content))
            return httpx.Response(200, json={"ok": True, "ts": f"170000.{len(posted_messages)}"})
        return httpx.Response(404)

    http = make_protocol_http_client(slack_handler)

    # First delivery succeeds
    out1 = await dispatch_notification_rule(
        db,
        rule=rule,
        connection=conn,
        context={"event_type": "escalation", "summary": "VIP waiting"},
        dedupe_key="call-1",
        http_client=http,
        now_ts=1000.0,
    )
    assert out1.status == "sent"
    assert out1.message_id == "170000.1"
    assert out1.rendered_text == "[escalation] VIP waiting"

    # Identical dedupe_key inside 120s window is suppressed
    out_dup = await dispatch_notification_rule(
        db,
        rule=rule,
        connection=conn,
        context={"event_type": "escalation", "summary": "VIP waiting"},
        dedupe_key="call-1",
        http_client=http,
        now_ts=1050.0,
    )
    assert out_dup.status == "deduplicated"
    assert len(posted_messages) == 1

    # Second distinct event succeeds (rate_limit_per_min=2)
    out2 = await dispatch_notification_rule(
        db,
        rule=rule,
        connection=conn,
        context={"event_type": "escalation", "summary": "Second call"},
        dedupe_key="call-2",
        http_client=http,
        now_ts=1010.0,
    )
    assert out2.status == "sent"

    # Third distinct event within the same 60s window hits rate_limit_per_min=2
    out3 = await dispatch_notification_rule(
        db,
        rule=rule,
        connection=conn,
        context={"event_type": "escalation", "summary": "Third call"},
        dedupe_key="call-3",
        http_client=http,
        now_ts=1020.0,
    )
    assert out3.status == "rate_limited"
    assert len(posted_messages) == 2
