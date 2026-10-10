"""Opt-in live Salesforce sandbox verification suite (Part 1F / Gate G1).

Gated by ``SALESFORCE_SANDBOX_INSTANCE_URL`` and ``SALESFORCE_SANDBOX_ACCESS_TOKEN``.
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.integrations.crm.base import ProviderContext
from app.integrations.crm.models import NormalizedActivity, NormalizedContact
from app.integrations.crm.providers.salesforce import SalesforceProvider

_SF_INSTANCE_URL = os.getenv("SALESFORCE_SANDBOX_INSTANCE_URL", "").strip()
_SF_ACCESS_TOKEN = os.getenv("SALESFORCE_SANDBOX_ACCESS_TOKEN", "").strip()


@pytest.mark.live
@pytest.mark.skipif(
    not (_SF_INSTANCE_URL and _SF_ACCESS_TOKEN),
    reason="SALESFORCE_SANDBOX_INSTANCE_URL and SALESFORCE_SANDBOX_ACCESS_TOKEN not set",
)
@pytest.mark.asyncio
async def test_salesforce_live_sandbox_round_trip():
    provider = SalesforceProvider(
        ProviderContext(
            tenant_id=str(uuid.uuid4()),
            credentials={
                "access_token": _SF_ACCESS_TOKEN,
                "refresh_token": os.getenv("SALESFORCE_SANDBOX_REFRESH_TOKEN", ""),
                "client_id": os.getenv("SALESFORCE_SANDBOX_CLIENT_ID", ""),
                "client_secret": os.getenv("SALESFORCE_SANDBOX_CLIENT_SECRET", ""),
            },
            config={"instance_url": _SF_INSTANCE_URL},
            field_mappings={},
        )
    )
    health = await provider.health_check()
    assert health.connected is True

    unique_email = f"voxdesk-live-{uuid.uuid4().hex[:8]}@example.com"
    contact = NormalizedContact(
        first_name="VoxDesk",
        last_name="SandboxTest",
        email=unique_email,
        phone="+15550190001",
    )
    upserted = await provider.upsert_contact(contact)
    assert upserted.external_id

    task = await provider.create_activity(
        upserted.external_id,
        NormalizedActivity(
            title="VoxDesk Live Sandbox Call",
            body="Automated sandbox verification call task.",
            duration_seconds=30,
        ),
    )
    assert task.external_id
