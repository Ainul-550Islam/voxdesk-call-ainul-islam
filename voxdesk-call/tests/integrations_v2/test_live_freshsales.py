"""Opt-in live sandbox round-trip test for Freshsales Suite CRM API."""

from __future__ import annotations

import os
import uuid

import pytest

from app.integrations_v2.core.provider_base import ConnectionRuntimeContext
from app.integrations_v2.crm.freshsales import FreshsalesProvider


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_freshsales_sandbox_roundtrip():
    domain = os.getenv("FRESHSALES_LIVE_DOMAIN", "").strip()
    api_key = os.getenv("FRESHSALES_LIVE_API_KEY", "").strip()
    if not (domain and api_key):
        pytest.skip("Set FRESHSALES_LIVE_DOMAIN and FRESHSALES_LIVE_API_KEY to run")

    ctx = ConnectionRuntimeContext(
        tenant_id="live-test",
        connection_id="live-fs",
        provider="freshsales",
        auth_kind="api_key",
        credentials={"api_key": api_key},
        config={"domain": domain},
    )
    provider = FreshsalesProvider(ctx)
    health = await provider.health()
    assert health.ok is True

    marker = uuid.uuid4().hex[:8]
    email = f"voxdesk-fs-{marker}@example.com"
    contact = await provider.upsert_contact(
        first_name="VoxDesk", last_name=f"Test{marker}", email=email
    )
    assert contact.external_id

    found = await provider.lookup_contact(email=email)
    assert found is not None
