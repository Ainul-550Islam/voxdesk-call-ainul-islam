# File: tests/api/test_messaging_registration_webhook.py — the carrier callback is signed
"""`POST /api/messaging/webhooks/registration` (PART 13, P13-08).

The endpoint mutates registration state and has no tenant context — the carrier
is not logged in — so the signature *is* the authentication. Until now it
accepted any JSON body, which meant anyone who knew a carrier reference could
move a registration to approved (`tests/smoke/test_app_boot.py` caught it as an
unallowlisted public route).

The tests below pin the closed default:

1. no secret configured -> 503 (never "trusted because unverifiable");
2. no header -> 401;
3. a wrong or malformed signature -> 401, and nothing changes;
4. a correct signature -> the event is applied;
5. a replayed event is still idempotent (the signature does not replace the
   forward-only state machine).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from sqlalchemy import select

import app.db.models  # noqa: F401
from app.core.config import settings
from app.db.models import Tenant, UserRole
from app.db.telephony_models import MessagingRegistration
from tests.conftest import make_tenant

URL = "/api/messaging/webhooks/registration"
SECRET = "test-carrier-secret"


def _sign(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def carrier_secret(monkeypatch):
    monkeypatch.setattr(settings, "telephony_webhook_secret", SECRET, raising=False)
    return SECRET


async def _seed_registration(db, tenant: Tenant, *, reference: str) -> MessagingRegistration:
    row = MessagingRegistration(
        tenant_id=tenant.id,
        kind="campaign",
        state="submitted",
        carrier_reference=reference,
        payload={},
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


def _event(reference: str, state: str = "approved") -> dict:
    return {"carrier_reference": reference, "status": state}


@pytest.mark.asyncio
async def test_unsigned_callback_is_refused_when_no_secret_is_configured(client, db, monkeypatch):
    monkeypatch.setattr(settings, "telephony_webhook_secret", "", raising=False)
    tenant = await make_tenant(db, "Unsigned Tenant")
    row = await _seed_registration(db, tenant, reference="ca-unsigned")

    body = json.dumps(_event("ca-unsigned")).encode()
    resp = await client.post(URL, content=body, headers={"Content-Type": "application/json"})

    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "webhook_signature_not_configured"
    await db.refresh(row)
    assert row.state == "submitted"


@pytest.mark.asyncio
async def test_missing_signature_header_is_401(client, db, carrier_secret):
    tenant = await make_tenant(db, "No Header Tenant")
    row = await _seed_registration(db, tenant, reference="ca-nohdr")

    resp = await client.post(URL, json=_event("ca-nohdr"))

    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "webhook_signature_missing"
    await db.refresh(row)
    assert row.state == "submitted"


@pytest.mark.asyncio
async def test_wrong_or_malformed_signature_is_401(client, db, carrier_secret):
    tenant = await make_tenant(db, "Bad Signature Tenant")
    row = await _seed_registration(db, tenant, reference="ca-badsig")
    body = json.dumps(_event("ca-badsig")).encode()

    wrong = await client.post(
        URL,
        content=body,
        headers={"Content-Type": "application/json", "X-VoxDesk-Signature": _sign(body, "other")},
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "webhook_signature_invalid"

    malformed = await client.post(
        URL,
        content=body,
        headers={"Content-Type": "application/json", "X-VoxDesk-Signature": "not-a-hash"},
    )
    assert malformed.status_code == 401

    await db.refresh(row)
    assert row.state == "submitted"


@pytest.mark.asyncio
async def test_signed_callback_is_applied(client, db, carrier_secret):
    tenant = await make_tenant(db, "Signed Tenant")
    row = await _seed_registration(db, tenant, reference="ca-signed")
    body = json.dumps(_event("ca-signed")).encode()

    resp = await client.post(
        URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-VoxDesk-Signature": f"sha256={_sign(body)}",
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] is True
    await db.refresh(row)
    assert row.state == "approved"


@pytest.mark.asyncio
async def test_a_replayed_signed_event_does_not_move_a_terminal_state(client, db, carrier_secret):
    tenant = await make_tenant(db, "Replay Tenant")
    row = await _seed_registration(db, tenant, reference="ca-replay")
    row.state = "approved"
    await db.commit()

    body = json.dumps(_event("ca-replay", "submitted")).encode()
    resp = await client.post(
        URL,
        content=body,
        headers={"Content-Type": "application/json", "X-VoxDesk-Signature": _sign(body)},
    )

    assert resp.status_code == 200
    assert resp.json()["applied"] is False
    stored = await db.scalar(
        select(MessagingRegistration).where(MessagingRegistration.id == row.id)
    )
    assert stored is not None and stored.state == "approved"


@pytest.mark.asyncio
async def test_unknown_registration_is_acknowledged_not_trusted(client, db, carrier_secret):
    await make_tenant(db, "Unknown Ref Tenant")
    body = json.dumps({"carrier_reference": f"missing-{uuid.uuid4().hex[:6]}", "status": "approved"}).encode()

    resp = await client.post(
        URL,
        content=body,
        headers={"Content-Type": "application/json", "X-VoxDesk-Signature": _sign(body)},
    )

    assert resp.status_code == 200
    assert resp.json() == {"applied": False, "reason": "unknown registration"}


@pytest.mark.asyncio
async def test_tenant_admin_routes_still_require_a_session(client, db):
    """The signed webhook is public; the registration *wizard* is not."""
    resp = await client.get("/api/messaging/registrations")
    assert resp.status_code == 401
    # Sanity: the fixture role exists, so a failure above is about auth and not setup.
    assert UserRole.OWNER.value
