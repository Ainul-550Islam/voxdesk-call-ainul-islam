"""PART 14 / P14-03 — share links: expiry, usage cap, passcode, sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services import agent_share_service as share


@pytest.mark.asyncio
async def test_a_link_returns_its_token_exactly_once_and_stores_only_a_hash(db, tenant_a) -> None:
    row, token = await share.create_link(
        db, tenant_id=tenant_a.id, agent_id="agent_1", created_by="owner@a.test", label="Demo"
    )
    assert token and token not in row.token_hash
    assert len(row.token_hash) == 64
    assert await share.get_by_token(db, token) is not None
    assert await share.get_by_token(db, "not-a-token") is None


@pytest.mark.asyncio
async def test_redeeming_consumes_a_use_and_the_cap_is_enforced(db, tenant_a) -> None:
    row, token = await share.create_link(
        db, tenant_id=tenant_a.id, agent_id="agent_1", created_by="x", max_uses=2
    )
    await share.redeem(db, token=token)
    await share.redeem(db, token=token)
    with pytest.raises(share.ShareError) as exhausted:
        await share.redeem(db, token=token)
    assert exhausted.value.code == "exhausted"
    assert row.used_count == 2


@pytest.mark.asyncio
async def test_an_expired_link_is_refused_with_expired(db, tenant_a) -> None:
    _, token = await share.create_link(
        db, tenant_id=tenant_a.id, agent_id="agent_1", created_by="x", ttl_hours=1
    )
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    with pytest.raises(share.ShareError) as expired:
        await share.redeem(db, token=token, moment=later)
    assert expired.value.code == "expired"


@pytest.mark.asyncio
async def test_a_passcode_is_required_and_compared_against_a_hash(db, tenant_a) -> None:
    row, token = await share.create_link(
        db, tenant_id=tenant_a.id, agent_id="agent_1", created_by="x", passcode="hunter22"
    )
    assert row.passcode_hash != "hunter22"
    with pytest.raises(share.ShareError) as required:
        await share.redeem(db, token=token)
    assert required.value.code == "passcode_required"
    with pytest.raises(share.ShareError) as wrong:
        await share.redeem(db, token=token, passcode="hunter23")
    assert wrong.value.code == "bad_passcode"
    await share.redeem(db, token=token, passcode="hunter22")
    assert row.used_count == 1


@pytest.mark.asyncio
async def test_a_short_passcode_is_refused_at_creation(db, tenant_a) -> None:
    with pytest.raises(share.ShareError) as weak:
        await share.create_link(
            db, tenant_id=tenant_a.id, agent_id="agent_1", created_by="x", passcode="123"
        )
    assert weak.value.code == "weak_passcode"


@pytest.mark.asyncio
async def test_ttl_and_usage_bounds_are_refused_at_creation(db, tenant_a) -> None:
    for kwargs, code in (
        ({"ttl_hours": 0}, "invalid_ttl"),
        ({"ttl_hours": 24 * 40}, "invalid_ttl"),
        ({"max_uses": 0}, "invalid_max_uses"),
        ({"max_seconds": 5}, "invalid_max_seconds"),
        ({"agent_id": "  "}, "invalid_agent"),
    ):
        payload = {"tenant_id": tenant_a.id, "agent_id": "agent_1", "created_by": "x", **kwargs}
        with pytest.raises(share.ShareError) as exc:
            await share.create_link(db, **payload)
        assert exc.value.code == code


@pytest.mark.asyncio
async def test_revoking_is_terminal_and_tenant_scoped(db, tenant_a, tenant_b) -> None:
    row, token = await share.create_link(
        db, tenant_id=tenant_a.id, agent_id="agent_1", created_by="x"
    )
    with pytest.raises(share.ShareError):
        await share.revoke(db, tenant_id=tenant_b.id, link_id=row.id)
    await share.revoke(db, tenant_id=tenant_a.id, link_id=row.id)
    with pytest.raises(share.ShareError) as revoked:
        await share.redeem(db, token=token)
    assert revoked.value.code == "revoked"


@pytest.mark.asyncio
async def test_the_view_never_contains_a_secret(db, tenant_a) -> None:
    row, token = await share.create_link(
        db, tenant_id=tenant_a.id, agent_id="agent_1", created_by="x", passcode="hunter22"
    )
    body = share.view(row)
    assert token not in str(body)
    assert "hunter22" not in str(body)
    assert body["requires_passcode"] is True
    assert body["state"] == "active"


def test_a_session_token_is_signed_and_expires() -> None:
    class Row:
        id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        agent_id = "agent_1"
        max_seconds = 300

    token, claims = share.mint_session(Row(), secret="topsecret", ttl_seconds=120)
    assert share.verify_session(token, secret="topsecret")["agent_id"] == "agent_1"
    assert claims["max_seconds"] == 300
    with pytest.raises(share.ShareError) as tampered:
        share.verify_session(token, secret="other-secret")
    assert tampered.value.code == "bad_signature"


def test_an_expired_session_token_is_refused() -> None:
    class Row:
        id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        agent_id = "agent_1"
        max_seconds = 300

    token, _ = share.mint_session(Row(), secret="topsecret", ttl_seconds=60)
    later = datetime.now(timezone.utc) + timedelta(minutes=5)
    with pytest.raises(share.ShareError) as expired:
        share.verify_session(token, secret="topsecret", moment=later)
    assert expired.value.code == "expired"


def test_the_ttl_never_exceeds_the_links_own_max_seconds() -> None:
    class Row:
        id = uuid.uuid4()
        tenant_id = uuid.uuid4()
        agent_id = "agent_1"
        max_seconds = 120

    _, claims = share.mint_session(Row(), secret="s", ttl_seconds=900)
    now = int(datetime.now(timezone.utc).timestamp())
    assert claims["exp"] - now <= 120
