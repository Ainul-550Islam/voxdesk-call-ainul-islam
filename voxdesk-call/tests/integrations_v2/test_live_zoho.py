"""Opt-in live sandbox round-trip tests for Zoho Desk API v1 and Zoho CRM REST API v6."""

from __future__ import annotations

import os
import uuid

import pytest

from app.integrations_v2.core.provider_base import ConnectionRuntimeContext
from app.integrations_v2.crm.zoho_crm import ZohoCrmProvider
from app.integrations_v2.helpdesk.models import (
    TicketCreateInput,
    TicketPriority,
    TicketStatus,
    TicketUpdateInput,
)
from app.integrations_v2.helpdesk.zoho_desk import ZohoDeskProvider


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_zoho_desk_sandbox_roundtrip():
    token = os.getenv("ZOHO_LIVE_ACCESS_TOKEN", "").strip()
    org_id = os.getenv("ZOHO_DESK_LIVE_ORG_ID", "").strip()
    dept_id = os.getenv("ZOHO_DESK_LIVE_DEPARTMENT_ID", "").strip()
    region = os.getenv("ZOHO_LIVE_REGION", "us").strip()
    if not (token and org_id and dept_id):
        pytest.skip(
            "Set ZOHO_LIVE_ACCESS_TOKEN, ZOHO_DESK_LIVE_ORG_ID, and ZOHO_DESK_LIVE_DEPARTMENT_ID to run"
        )

    ctx = ConnectionRuntimeContext(
        tenant_id="live-test",
        connection_id="live-zdesk",
        provider="zoho_desk",
        region=region,
        credentials={"access_token": token},
        config={"org_id": org_id, "department_id": dept_id, "region": region},
    )
    provider = ZohoDeskProvider(ctx)
    health = await provider.health()
    assert health.ok is True

    marker = uuid.uuid4().hex[:8]
    created = await provider.create_ticket(
        TicketCreateInput(
            subject=f"[VoxDesk Live ZohoDesk {marker}]",
            description="Automated Zoho Desk sandbox verification.",
            priority=TicketPriority.LOW,
            requester_name="VoxDesk Live",
            requester_email=f"voxdesk-{marker}@example.com",
        )
    )
    assert created.external_id

    fetched = await provider.get_ticket(created.external_id)
    assert fetched.external_id == created.external_id

    updated = await provider.update_ticket(
        created.external_id,
        TicketUpdateInput(status=TicketStatus.CLOSED),
    )
    assert updated.external_id == created.external_id


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_zoho_crm_sandbox_roundtrip():
    token = os.getenv("ZOHO_LIVE_ACCESS_TOKEN", "").strip()
    region = os.getenv("ZOHO_LIVE_REGION", "us").strip()
    if not token:
        pytest.skip("Set ZOHO_LIVE_ACCESS_TOKEN to run")

    ctx = ConnectionRuntimeContext(
        tenant_id="live-test",
        connection_id="live-zcrm",
        provider="zoho_crm",
        region=region,
        credentials={"access_token": token},
        config={"region": region},
    )
    provider = ZohoCrmProvider(ctx)
    health = await provider.health()
    assert health.ok is True

    marker = uuid.uuid4().hex[:8]
    email = f"voxdesk-live-{marker}@example.com"
    upserted = await provider.upsert_contact(
        first_name="VoxDesk", last_name=f"Live{marker}", email=email
    )
    assert upserted.external_id

    found = await provider.lookup_contact(email=email)
    assert found is not None
