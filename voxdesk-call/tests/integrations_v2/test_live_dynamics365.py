"""Opt-in live sandbox round-trip test for Microsoft Dynamics 365 Dataverse Web API v9.2."""

from __future__ import annotations

import os
import uuid

import pytest

from app.integrations_v2.core.provider_base import ConnectionRuntimeContext
from app.integrations_v2.crm.dynamics365 import Dynamics365Provider


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_dynamics365_sandbox_roundtrip():
    instance_url = os.getenv("DYNAMICS_LIVE_INSTANCE_URL", "").strip()
    token = os.getenv("DYNAMICS_LIVE_ACCESS_TOKEN", "").strip()
    if not (instance_url and token):
        pytest.skip("Set DYNAMICS_LIVE_INSTANCE_URL and DYNAMICS_LIVE_ACCESS_TOKEN to run")

    ctx = ConnectionRuntimeContext(
        tenant_id="live-test",
        connection_id="live-dyn",
        provider="dynamics365",
        instance_url=instance_url,
        credentials={"access_token": token},
    )
    provider = Dynamics365Provider(ctx)
    health = await provider.health()
    assert health.ok is True

    marker = uuid.uuid4().hex[:8]
    email = f"voxdesk-dyn-{marker}@example.com"
    contact = await provider.upsert_contact(
        first_name="VoxDesk", last_name=f"Dyn{marker}", email=email
    )
    assert contact.external_id
