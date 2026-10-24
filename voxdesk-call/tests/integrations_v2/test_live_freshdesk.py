"""Opt-in live sandbox round-trip test for Freshdesk API v2."""

from __future__ import annotations

import os
import uuid

import pytest

from app.integrations_v2.core.provider_base import ConnectionRuntimeContext
from app.integrations_v2.helpdesk.freshdesk import FreshdeskProvider
from app.integrations_v2.helpdesk.models import (
    TicketCreateInput,
    TicketPriority,
    TicketStatus,
    TicketUpdateInput,
)


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_freshdesk_sandbox_roundtrip():
    domain = os.getenv("FRESHDESK_LIVE_DOMAIN", "").strip()
    api_key = os.getenv("FRESHDESK_LIVE_API_KEY", "").strip()
    email = os.getenv("FRESHDESK_LIVE_REQUESTER_EMAIL", "voxdesk-live@example.com").strip()
    if not (domain and api_key):
        pytest.skip("Set FRESHDESK_LIVE_DOMAIN and FRESHDESK_LIVE_API_KEY to run")

    ctx = ConnectionRuntimeContext(
        tenant_id="live-test",
        connection_id="live-fd",
        provider="freshdesk",
        auth_kind="api_key",
        credentials={"api_key": api_key},
        config={"domain": domain},
    )
    provider = FreshdeskProvider(ctx)
    health = await provider.health()
    assert health.ok is True

    marker = uuid.uuid4().hex[:8]
    created = await provider.create_ticket(
        TicketCreateInput(
            subject=f"[VoxDesk Live Ticket {marker}]",
            description="Automated Freshdesk live verification.",
            priority=TicketPriority.LOW,
            requester_email=email,
        )
    )
    assert created.external_id

    fetched = await provider.get_ticket(created.external_id)
    assert fetched.external_id == created.external_id

    updated = await provider.update_ticket(
        created.external_id,
        TicketUpdateInput(status=TicketStatus.CLOSED, comment_body="Closing Freshdesk live test"),
    )
    assert updated.external_id == created.external_id
