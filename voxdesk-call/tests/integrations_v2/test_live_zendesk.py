"""Opt-in live sandbox round-trip test for Zendesk Support REST API v2."""

from __future__ import annotations

import os
import uuid

import pytest

from app.integrations_v2.core.provider_base import ConnectionRuntimeContext
from app.integrations_v2.helpdesk.models import (
    TicketCreateInput,
    TicketPriority,
    TicketStatus,
    TicketUpdateInput,
)
from app.integrations_v2.helpdesk.zendesk import ZendeskProvider


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_zendesk_sandbox_roundtrip():
    subdomain = os.getenv("ZENDESK_LIVE_SUBDOMAIN", "").strip()
    token = os.getenv("ZENDESK_LIVE_ACCESS_TOKEN", "").strip()
    if not (subdomain and token):
        pytest.skip("Set ZENDESK_LIVE_SUBDOMAIN and ZENDESK_LIVE_ACCESS_TOKEN to run")

    ctx = ConnectionRuntimeContext(
        tenant_id="live-test",
        connection_id="live-zd",
        provider="zendesk",
        credentials={"access_token": token},
        config={"subdomain": subdomain},
    )
    provider = ZendeskProvider(ctx)
    health = await provider.health()
    assert health.ok is True

    marker = uuid.uuid4().hex[:8]
    created = await provider.create_ticket(
        TicketCreateInput(
            subject=f"[VoxDesk Live Test {marker}]",
            description="Automated live sandbox verification ticket.",
            priority=TicketPriority.LOW,
            tags=["voxdesk_live_test"],
        )
    )
    assert created.external_id

    fetched = await provider.get_ticket(created.external_id)
    assert fetched.external_id == created.external_id

    updated = await provider.update_ticket(
        created.external_id,
        TicketUpdateInput(status=TicketStatus.CLOSED, comment_body="Closing live test ticket"),
    )
    assert updated.external_id == created.external_id
