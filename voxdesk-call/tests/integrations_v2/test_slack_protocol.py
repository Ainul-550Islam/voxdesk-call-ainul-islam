"""Slack Web API v2 + Block Kit + signed /voxdesk slash command + RBAC + interactivity tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import httpx
import pytest
from sqlalchemy import select

from app.db.models import Call, Lead, UserRole
from app.integrations_v2.collab.slack import (
    SlackProvider,
    handle_slack_interaction,
    handle_slack_slash_command,
)
from app.integrations_v2.core.connection_store import upsert_connection
from app.integrations_v2.core.errors import AuthExpired
from app.integrations_v2.core.provider_base import ConnectionRuntimeContext
from tests.conftest import make_tenant, make_user
from tests.integrations_v2.conftest import make_protocol_http_client


def _sign_slack(secret: str, ts: int, body: bytes) -> dict[str, str]:
    sig = "v0=" + hmac.new(
        secret.encode("utf-8"), f"v0:{ts}:".encode("ascii") + body, hashlib.sha256
    ).hexdigest()
    return {"X-Slack-Request-Timestamp": str(ts), "X-Slack-Signature": sig}


@pytest.mark.asyncio
async def test_slack_block_kit_slash_command_rbac_and_interactive_buttons(db):
    posted_messages: list[dict] = []

    def slack_server(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "slack.com"
        assert req.headers.get("Authorization") == "Bearer xoxb-bot-secret-token"
        path = req.url.path
        if path == "/api/auth.test":
            return httpx.Response(200, json={"ok": True, "team_id": "T001", "user_id": "B001"})
        if path == "/api/conversations.list":
            return httpx.Response(
                200,
                json={"ok": True, "channels": [{"id": "C100", "name": "escalations", "is_private": False}]},
            )
        if path == "/api/chat.postMessage":
            body = json.loads(req.content)
            posted_messages.append(body)
            return httpx.Response(200, json={"ok": True, "channel": body["channel"], "ts": "1700000.001"})
        return httpx.Response(404)

    ctx = ConnectionRuntimeContext(
        tenant_id="t-sl",
        connection_id="c-sl",
        provider="slack",
        credentials={"access_token": "xoxb-bot-secret-token"},
    )
    slack = SlackProvider(ctx, http_client=make_protocol_http_client(slack_server))
    assert (await slack.health()).account_id == "T001"
    channels = await slack.list_channels()
    assert channels[0]["name"] == "escalations"

    msg = await slack.post_message(channel="C100", text="Alert")
    assert msg["ts"] == "1700000.001"

    tenant = await make_tenant(db, "Slack RBAC Tenant")
    owner_user = await make_user(db, tenant, UserRole.OWNER)
    viewer_user = await make_user(db, tenant, UserRole.VIEWER)

    lead = Lead(
        tenant_id=tenant.id,
        name="Hot Lead",
        phone="+14155550188",
        email="lead@example.com",
    )
    db.add(lead)
    await db.flush()

    signing_secret = "slack_sign_secret_xyz"
    conn = await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="slack",
        category="collab",
        auth_kind="oauth2",
        credentials={"access_token": "xoxb-bot-secret-token"},
        config={
            "slack_user_map": {
                "U_OWNER": str(owner_user.id),
                "U_VIEWER": str(viewer_user.id),
            }
        },
        webhook_secret=signing_secret,
    )
    await db.commit()

    now = int(time.time())

    viewer_cmd = urlencode({"user_id": "U_VIEWER", "text": "call +14155550123"}).encode("utf-8")
    with pytest.raises(AuthExpired):
        await handle_slack_slash_command(
            db,
            connection=conn,
            raw_body=viewer_cmd,
            headers=_sign_slack(signing_secret, now, viewer_cmd),
            now=now,
        )

    unmapped_cmd = urlencode({"user_id": "U_STRANGER", "text": "call +14155550123"}).encode("utf-8")
    with pytest.raises(AuthExpired):
        await handle_slack_slash_command(
            db,
            connection=conn,
            raw_body=unmapped_cmd,
            headers=_sign_slack(signing_secret, now, unmapped_cmd),
            now=now,
        )

    owner_cmd = urlencode({"user_id": "U_OWNER", "text": "call +14155550123"}).encode("utf-8")
    reply = await handle_slack_slash_command(
        db,
        connection=conn,
        raw_body=owner_cmd,
        headers=_sign_slack(signing_secret, now, owner_cmd),
        now=now,
    )
    await db.commit()
    assert "Outbound call" in reply["text"]

    calls = (
        await db.execute(select(Call).where(Call.tenant_id == tenant.id))
    ).scalars().all()
    assert len(calls) == 1
    assert calls[0].to_number == "+14155550123"

    interaction_payload = {
        "user": {"id": "U_OWNER"},
        "actions": [
            {
                "action_id": "voxdesk_assign_lead",
                "value": json.dumps({"action": "assign_lead", "lead_id": str(lead.id)}),
            }
        ],
    }
    inter_body = urlencode({"payload": json.dumps(interaction_payload)}).encode("utf-8")
    inter_res = await handle_slack_interaction(
        db,
        connection=conn,
        raw_body=inter_body,
        headers=_sign_slack(signing_secret, now, inter_body),
        now=now,
    )
    await db.commit()
    assert inter_res["action"] == "assign_lead"
    await db.refresh(lead)
    assert lead.custom_fields["assigned_user_id"] == str(owner_user.id)
