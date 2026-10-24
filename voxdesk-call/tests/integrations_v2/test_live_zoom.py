"""Opt-in live sandbox round-trip test for Zoom REST API v2."""

from __future__ import annotations

import os
import uuid

import pytest

from app.integrations_v2.collab.zoom import ZoomProvider
from app.integrations_v2.core.provider_base import ConnectionRuntimeContext


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_zoom_sandbox_roundtrip():
    token = os.getenv("ZOOM_LIVE_ACCESS_TOKEN", "").strip()
    if not token:
        pytest.skip("Set ZOOM_LIVE_ACCESS_TOKEN to run")

    ctx = ConnectionRuntimeContext(
        tenant_id="live-test",
        connection_id="live-zoom",
        provider="zoom",
        credentials={"access_token": token},
    )
    provider = ZoomProvider(ctx)
    health = await provider.health()
    assert health.ok is True

    marker = uuid.uuid4().hex[:8]
    mtg = await provider.create_meeting(
        topic=f"VoxDesk Live Zoom {marker}", duration_minutes=15
    )
    assert mtg["join_url"]
