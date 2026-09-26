"""Number search, purchase, assignment and tenant ownership."""

from __future__ import annotations

import pytest

from app.telephony.capabilities import CapabilitySet
from app.telephony.number_provisioning import assign, list_owned, provision, release
from app.telephony.provider_errors import (
    ProviderConflictError,
    ProviderUnavailableError,
    ProviderValidationError,
    UnsupportedCapability,
)
from app.telephony.providers.base import NumberResult
from app.tenancy.isolation import NotFound
from tests.conftest import auth_headers, subscribe

pytestmark = pytest.mark.asyncio


class Scripted:
    def __init__(self, external_id="PN1", fail=None):
        self.external_id = external_id
        self.fail = fail
        self.released = []

    async def provision_number(self, e164, *, country=""):
        if self.fail:
            raise self.fail
        return NumberResult(
            "twilio",
            e164,
            self.external_id,
            CapabilitySet(voice=True, source="provider_api"),
            country=country,
        )

    async def release_number(self, external_id):
        self.released.append(external_id)
        return NumberResult("twilio", "", external_id)

    async def search_numbers(self, *, country, limit=10):
        return [
            NumberResult(
                "twilio",
                "+15557650001",
                "",
                CapabilitySet(voice=True, sms=True, source="provider_api"),
                country=country,
            )
        ]


async def test_purchase_requires_billing_and_then_assigns_once(db, tenant_a, billing_plans):
    from app.db.models import SubscriptionStatus

    denied = await subscribe(db, tenant_a, status=SubscriptionStatus.CANCELED)
    with pytest.raises(ProviderValidationError):
        await provision(
            db, tenant_a, e164="+15557651111", provider="twilio", country="US", adapter=Scripted()
        )
    denied.status = SubscriptionStatus.ACTIVE
    await db.commit()
    first = await provision(
        db, tenant_a, e164="+15557651111", provider="twilio", country="US", adapter=Scripted("PN-A")
    )
    assert first.status == "provisioned"
    assert first.capabilities["voice"] is True
    assert first.capabilities["whatsapp"] is False
    with pytest.raises(ProviderConflictError):
        await provision(
            db,
            tenant_a,
            e164="+15557651111",
            provider="telnyx",
            country="US",
            adapter=Scripted("PN-B"),
        )
    assigned = await assign(db, tenant_a.id, first.id, use="voice")
    assert assigned.status == "assigned"
    with pytest.raises(UnsupportedCapability):
        await assign(db, tenant_a.id, first.id, use="sms")
    again = await assign(db, tenant_a.id, first.id, use="voice")
    assert again.status == "assigned"
    assert again.assigned_use == "voice"


async def test_provider_failure_releases_the_reservation(db, tenant_a):
    await subscribe(db, tenant_a)
    with pytest.raises(ProviderUnavailableError):
        await provision(
            db,
            tenant_a,
            e164="+15557652222",
            provider="twilio",
            adapter=Scripted(fail=ProviderUnavailableError("down", provider="twilio")),
        )
    rows = await list_owned(db, tenant_a.id)
    assert rows[0].status == "released"
    assert rows[0].active_key is None


async def test_cross_tenant_assign_and_release_are_not_found(db, tenant_a, tenant_b):
    await subscribe(db, tenant_a)
    row = await provision(db, tenant_a, e164="+15557653333", provider="twilio", adapter=Scripted())
    with pytest.raises(NotFound):
        await assign(db, tenant_b.id, row.id, use="voice")
    with pytest.raises(NotFound):
        await release(db, tenant_b.id, row.id, adapter=Scripted())
    assert row.status == "provisioned"


async def test_release_calls_provider_once_and_is_idempotent(db, tenant_a):
    await subscribe(db, tenant_a)
    adapter = Scripted("PN-REL")
    row = await provision(db, tenant_a, e164="+15557654444", provider="twilio", adapter=adapter)
    released = await release(db, tenant_a.id, row.id, adapter=adapter)
    again = await release(db, tenant_a.id, row.id, adapter=adapter)
    assert released.status == "released"
    assert again.status == "released"
    assert adapter.released == ["PN-REL"]


async def test_stale_provider_snapshot_does_not_revive_a_release(db, tenant_a):
    from datetime import datetime, timedelta, timezone

    from app.telephony.callback_reconciliation import reconcile_number

    await subscribe(db, tenant_a)
    row = await provision(db, tenant_a, e164="+15557655555", provider="twilio", adapter=Scripted())
    await release(db, tenant_a.id, row.id, adapter=Scripted())
    old = datetime.now(timezone.utc) - timedelta(hours=1)
    foreign = await reconcile_number(
        db,
        row,
        {
            "status": "assigned",
            "observed_at": old,
            "tenant_id": "00000000-0000-0000-0000-000000000099",
        },
    )
    assert foreign["outcome"] == "ownership_ignored"
    assert row.status == "released"
    stale = await reconcile_number(db, row, {"status": "assigned", "observed_at": old})
    assert stale["outcome"] == "stale_ignored"
    assert row.status == "released"


async def test_http_inventory_is_tenant_scoped(
    client, db, tenant_a, tenant_b, owner_a, owner_b, viewer_a
):
    await subscribe(db, tenant_a)
    row = await provision(db, tenant_a, e164="+15557656666", provider="twilio", adapter=Scripted())
    await db.commit()
    owner = await auth_headers(client, owner_a)
    other = await auth_headers(client, owner_b)
    viewer = await auth_headers(client, viewer_a)
    listed = await client.get(f"/api/tenants/{tenant_a.id}/phone-numbers", headers=owner)
    assert listed.status_code == 200, listed.text
    assert listed.json()["numbers"][0]["id"] == str(row.id)
    hidden = await client.get(f"/api/tenants/{tenant_a.id}/phone-numbers", headers=other)
    assert hidden.status_code == 404
    denied = await client.post(
        "/api/phone-numbers/provision",
        json={"e164": "+15557657777", "provider": "twilio", "country": "US"},
        headers=viewer,
    )
    assert denied.status_code == 403
    stolen = await client.post(
        f"/api/tenants/{tenant_b.id}/phone-numbers/{row.id}/assign",
        json={"use": "voice"},
        headers=owner,
    )
    assert stolen.status_code == 404
    released = await client.post(f"/api/phone-numbers/{row.id}/release", headers=other)
    assert released.status_code == 404
    caps = await client.get(f"/api/phone-numbers/{row.id}/capabilities", headers=owner)
    assert caps.status_code == 200
    assert caps.json()["capabilities"]["voice"] is True
    assert "api_key" not in caps.text
    search = await client.post(
        "/api/phone-numbers/search",
        json={"country": "US", "provider": "telnyx"},
        headers=owner,
    )
    assert search.status_code == 503
