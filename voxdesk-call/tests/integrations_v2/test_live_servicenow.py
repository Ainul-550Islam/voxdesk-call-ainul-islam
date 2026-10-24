"""Opt-in live sandbox round-trip test for ServiceNow Table API."""

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
from app.integrations_v2.helpdesk.servicenow import ServiceNowProvider


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_servicenow_pdi_roundtrip():
    instance_url = os.getenv("SERVICENOW_LIVE_INSTANCE_URL", "").strip()
    token = os.getenv("SERVICENOW_LIVE_ACCESS_TOKEN", "").strip()
    if not (instance_url and token):
        pytest.skip("Set SERVICENOW_LIVE_INSTANCE_URL and SERVICENOW_LIVE_ACCESS_TOKEN to run")

    ctx = ConnectionRuntimeContext(
        tenant_id="live-test",
        connection_id="live-sn",
        provider="servicenow",
        instance_url=instance_url,
        credentials={"access_token": token},
    )
    provider = ServiceNowProvider(ctx)
    health = await provider.health()
    assert health.ok is True

    marker = uuid.uuid4().hex[:8]
    created = await provider.create_ticket(
        TicketCreateInput(
            subject=f"[VoxDesk Live Incident {marker}]",
            description="Automated ServiceNow PDI round-trip verification.",
            priority=TicketPriority.LOW,
        )
    )
    assert created.external_id

    fetched = await provider.get_ticket(created.external_id)
    assert fetched.external_id == created.external_id

    updated = await provider.update_ticket(
        created.external_id,
        TicketUpdateInput(status=TicketStatus.RESOLVED, comment_body="Resolved automated PDI check"),
    )
    assert updated.external_id == created.external_id
