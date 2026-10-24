"""Opt-in live sandbox test for Slack Web API v2 (auth.test + optional channel post)."""

from __future__ import annotations

import os

import pytest

from app.integrations_v2.collab.slack import SlackProvider
from app.integrations_v2.core.provider_base import ConnectionRuntimeContext


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_slack_sandbox_health_and_post():
    bot_token = os.getenv("SLACK_LIVE_BOT_TOKEN", "").strip()
    if not bot_token:
        pytest.skip("Set SLACK_LIVE_BOT_TOKEN to run")

    ctx = ConnectionRuntimeContext(
        tenant_id="live-test",
        connection_id="live-slack",
        provider="slack",
        credentials={"access_token": bot_token},
    )
    provider = SlackProvider(ctx)
    health = await provider.health()
    assert health.ok is True

    channel_id = os.getenv("SLACK_LIVE_CHANNEL_ID", "").strip()
    if channel_id:
        ts = await provider.post_message(
            channel=channel_id, text="[VoxDesk Live Sandbox] Connectivity check"
        )
        assert ts
