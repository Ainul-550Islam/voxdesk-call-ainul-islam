"""CRM provider capability matrix and opt-in live sandbox verification (Part 1F / Gate G1)."""
from __future__ import annotations

import os
import uuid

import pytest

from app.db.models import CrmProviderType
from app.integrations.crm.base import Capability, ProviderContext
from app.integrations.crm.registry import PROVIDERS, build


def test_crm_provider_capability_matrix_complete():
    expected = {
        CrmProviderType.GOHIGHLEVEL: {
            Capability.UPSERT_CONTACT,
            Capability.CREATE_CONTACT,
            Capability.UPDATE_CONTACT,
            Capability.GET_CONTACT,
            Capability.CREATE_NOTE,
            Capability.CREATE_ACTIVITY,
            Capability.CREATE_APPOINTMENT,
            Capability.ADD_TAG,
            Capability.ADD_CUSTOM_FIELDS,
            Capability.HEALTH_CHECK,
        },
        CrmProviderType.HUBSPOT: {
            Capability.UPSERT_CONTACT,
            Capability.CREATE_CONTACT,
            Capability.UPDATE_CONTACT,
            Capability.GET_CONTACT,
            Capability.CREATE_NOTE,
            Capability.CREATE_ACTIVITY,
            Capability.ADD_CUSTOM_FIELDS,
            Capability.HEALTH_CHECK,
        },
        CrmProviderType.JOBBER: {
            Capability.UPSERT_CONTACT,
            Capability.CREATE_CONTACT,
            Capability.UPDATE_CONTACT,
            Capability.GET_CONTACT,
            Capability.CREATE_NOTE,
            Capability.CREATE_ACTIVITY,
            Capability.HEALTH_CHECK,
        },
        CrmProviderType.SALESFORCE: {
            Capability.UPSERT_CONTACT,
            Capability.CREATE_CONTACT,
            Capability.UPDATE_CONTACT,
            Capability.GET_CONTACT,
            Capability.CREATE_NOTE,
            Capability.CREATE_ACTIVITY,
            Capability.ADD_CUSTOM_FIELDS,
            Capability.HEALTH_CHECK,
        },
        CrmProviderType.WEBHOOK: {
            Capability.UPSERT_CONTACT,
            Capability.CREATE_CONTACT,
            Capability.CREATE_NOTE,
            Capability.CREATE_ACTIVITY,
            Capability.CREATE_APPOINTMENT,
            Capability.CANCEL_APPOINTMENT,
            Capability.ADD_TAG,
            Capability.ADD_CUSTOM_FIELDS,
            Capability.HEALTH_CHECK,
        },
    }
    assert set(PROVIDERS.keys()) == set(expected.keys())
    for provider_type, caps in expected.items():
        cls = PROVIDERS[provider_type]
        assert set(cls.capabilities) == caps


@pytest.mark.live
@pytest.mark.skipif(
    not os.getenv("HUBSPOT_SANDBOX_ACCESS_TOKEN"),
    reason="HUBSPOT_SANDBOX_ACCESS_TOKEN not set",
)
@pytest.mark.asyncio
async def test_hubspot_live_sandbox_health():
    adapter = build(
        CrmProviderType.HUBSPOT,
        ProviderContext(
            tenant_id=str(uuid.uuid4()),
            credentials={"access_token": os.environ["HUBSPOT_SANDBOX_ACCESS_TOKEN"]},
            config={},
            field_mappings={},
        ),
    )
    res = await adapter.health_check()
    assert res.connected is True


@pytest.mark.live
@pytest.mark.skipif(
    not (os.getenv("GHL_SANDBOX_ACCESS_TOKEN") and os.getenv("GHL_SANDBOX_LOCATION_ID")),
    reason="GHL_SANDBOX_ACCESS_TOKEN and GHL_SANDBOX_LOCATION_ID not set",
)
@pytest.mark.asyncio
async def test_ghl_live_sandbox_health():
    adapter = build(
        CrmProviderType.GOHIGHLEVEL,
        ProviderContext(
            tenant_id=str(uuid.uuid4()),
            credentials={"access_token": os.environ["GHL_SANDBOX_ACCESS_TOKEN"]},
            config={"location_id": os.environ["GHL_SANDBOX_LOCATION_ID"]},
            field_mappings={},
        ),
    )
    res = await adapter.health_check()
    assert res.connected is True


@pytest.mark.live
@pytest.mark.skipif(
    not os.getenv("JOBBER_SANDBOX_ACCESS_TOKEN"),
    reason="JOBBER_SANDBOX_ACCESS_TOKEN not set",
)
@pytest.mark.asyncio
async def test_jobber_live_sandbox_health():
    adapter = build(
        CrmProviderType.JOBBER,
        ProviderContext(
            tenant_id=str(uuid.uuid4()),
            credentials={"access_token": os.environ["JOBBER_SANDBOX_ACCESS_TOKEN"]},
            config={},
            field_mappings={},
        ),
    )
    res = await adapter.health_check()
    assert res.connected is True
