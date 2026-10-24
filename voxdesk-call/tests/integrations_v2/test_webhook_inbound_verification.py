"""Tests for Slack v0, Zoom x-zm-signature + challenge, Zendesk base64 HMAC, Teams clientState + validationToken, replay window, and dedup."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from app.integrations_v2.core.connection_store import upsert_connection
from app.integrations_v2.core.webhook_inbound import (
    WebhookSignatureError,
    receive_inbound_webhook,
)
from tests.conftest import make_tenant


def _slack_headers(secret: str, ts: int, body: bytes) -> dict[str, str]:
    sig = "v0=" + hmac.new(
        secret.encode("utf-8"),
        f"v0:{ts}:".encode("ascii") + body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "X-Slack-Request-Timestamp": str(ts),
        "X-Slack-Signature": sig,
    }


@pytest.mark.asyncio
async def test_slack_signature_replay_protection_and_dedup(db):
    tenant = await make_tenant(db, "Webhook Tenant")
    secret = "slack_signing_secret_test_value"
    conn = await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="slack",
        category="collab",
        auth_kind="oauth2",
        credentials={"access_token": "xoxb-test-token"},
        webhook_secret=secret,
    )
    await db.commit()

    now = int(time.time())
    event_body = json.dumps({"type": "event_callback", "event_id": "Ev12345"}).encode("utf-8")

    res1 = await receive_inbound_webhook(
        db,
        connection=conn,
        raw_body=event_body,
        headers=_slack_headers(secret, now, event_body),
        now=now,
    )
    assert res1.status == "accepted"

    res_dup = await receive_inbound_webhook(
        db,
        connection=conn,
        raw_body=event_body,
        headers=_slack_headers(secret, now, event_body),
        now=now,
    )
    assert res_dup.status == "duplicate"

    stale_ts = now - 600
    with pytest.raises(WebhookSignatureError):
        await receive_inbound_webhook(
            db,
            connection=conn,
            raw_body=event_body,
            headers=_slack_headers(secret, stale_ts, event_body),
            now=now,
        )

    with pytest.raises(WebhookSignatureError):
        await receive_inbound_webhook(
            db,
            connection=conn,
            raw_body=b'{"type":"event_callback","event_id":"EvTampered"}',
            headers=_slack_headers(secret, now, event_body),
            now=now,
        )


@pytest.mark.asyncio
async def test_zoom_zendesk_and_teams_client_state_and_validation_token(db, client):
    tenant = await make_tenant(db, "Zoom ZD Teams Webhook Tenant")

    zoom_secret = "zoom_wh_secret_123456"
    zoom_conn = await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="zoom",
        category="collab",
        auth_kind="oauth2",
        credentials={"access_token": "zm-tok"},
        webhook_secret=zoom_secret,
    )

    zd_secret = "zendesk_wh_secret_654321"
    zd_conn = await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="zendesk",
        category="helpdesk",
        auth_kind="oauth2",
        credentials={"access_token": "zd-tok"},
        config={"subdomain": "acme"},
        webhook_secret=zd_secret,
    )

    teams_secret = "teams_client_state_secret_999"
    teams_conn = await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="teams",
        category="collab",
        auth_kind="oauth2",
        credentials={"access_token": "graph-tok"},
        webhook_secret=teams_secret,
    )
    await db.commit()

    now = int(time.time())
    zoom_body = json.dumps(
        {"event": "endpoint.url_validation", "payload": {"plainToken": "plain_abc"}}
    ).encode("utf-8")
    zm_sig = "v0=" + hmac.new(
        zoom_secret.encode("utf-8"),
        f"v0:{now}:".encode("ascii") + zoom_body,
        hashlib.sha256,
    ).hexdigest()
    zm_res = await receive_inbound_webhook(
        db,
        connection=zoom_conn,
        raw_body=zoom_body,
        headers={"x-zm-request-timestamp": str(now), "x-zm-signature": zm_sig},
        now=now,
    )
    assert zm_res.status == "challenge"
    assert zm_res.challenge_response["plainToken"] == "plain_abc"

    zd_ts = "2026-10-24T12:00:00Z"
    zd_body = json.dumps({"id": "zd-ev-1", "type": "zen:event-type:ticket.created"}).encode("utf-8")
    zd_sig = base64.b64encode(
        hmac.new(zd_secret.encode("utf-8"), zd_ts.encode("utf-8") + zd_body, hashlib.sha256).digest()
    ).decode("ascii")
    zd_res = await receive_inbound_webhook(
        db,
        connection=zd_conn,
        raw_body=zd_body,
        headers={
            "X-Zendesk-Webhook-Signature-Timestamp": zd_ts,
            "X-Zendesk-Webhook-Signature": zd_sig,
        },
        now=int(time.mktime(time.strptime(zd_ts, "%Y-%m-%dT%H:%M:%SZ"))) - time.timezone,
    )
    assert zd_res.status == "accepted"

    # Teams Graph ?validationToken= plain-text handshake over public HTTP boundary
    hs_resp = await client.post(
        f"/webhooks/int2/teams/{teams_conn.id}?validationToken=ms_graph_token_xyz"
    )
    assert hs_resp.status_code == 200
    assert hs_resp.text == "ms_graph_token_xyz"

    # Teams Graph change notification with valid vs forged clientState
    valid_teams_body = json.dumps(
        {
            "value": [
                {
                    "id": "notif-1",
                    "subscriptionId": "sub-1",
                    "clientState": teams_secret,
                    "changeType": "created",
                }
            ]
        }
    ).encode("utf-8")
    teams_ok = await receive_inbound_webhook(
        db, connection=teams_conn, raw_body=valid_teams_body, headers={}, now=now
    )
    assert teams_ok.status == "accepted"

    bad_teams_body = json.dumps(
        {"value": [{"id": "notif-2", "clientState": "forged_state"}]}
    ).encode("utf-8")
    with pytest.raises(WebhookSignatureError):
        await receive_inbound_webhook(
            db, connection=teams_conn, raw_body=bad_teams_body, headers={}, now=now
        )
