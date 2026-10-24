"""Opt-in live sandbox round-trip test for Microsoft Teams Graph API v1.0."""

from __future__ import annotations

import os
import uuid

import pytest

from app.integrations_v2.collab.teams import TeamsProvider
from app.integrations_v2.core.provider_base import ConnectionRuntimeContext


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_teams_sandbox_roundtrip():
    token = os.getenv("TEAMS_LIVE_ACCESS_TOKEN", "").strip()
    if not token:
        pytest.skip("Set TEAMS_LIVE_ACCESS_TOKEN to run")

    ctx = ConnectionRuntimeContext(
        tenant_id="live-test",
        connection_id="live-teams",
        provider="teams",
        credentials={"access_token": token},
    )
    provider = TeamsProvider(ctx)
    health = await provider.health()
    assert health.ok is True

    marker = uuid.uuid4().hex[:8]
    mtg = await provider.create_online_meeting(
        subject=f"VoxDesk Live Test {marker}", duration_minutes=15
    )
    assert mtg["join_url"]
