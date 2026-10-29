# File: tests/api/test_voice_profile_routes.py — tenant-scoped voice-profile
# lifecycle API.
"""Tests for ``app.api.voice_profile_routes``.

The route set is small and CRUD-shaped, so the tests concentrate on the three
things that can go quietly wrong in a multi-tenant write API:

1. **Tenant isolation.** Another tenant's profile id is a 404, not a 403 and
   not a readable row — a 403 would confirm the row exists.
2. **Authorization.** Reads need ``TENANT_READ`` and writes ``TENANT_UPDATE``,
   so a Viewer can list but cannot create or activate.
3. **Audit durability.** Every state change writes an audit row carrying the
   actor's email, staged in the same transaction as the mutation.

Where a response body is asserted, the assertion is against the row in the
database too, so a route that returned a plausible-looking body without
persisting it would still fail.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401
from app.db.models import AuditLog, Tenant, User, UserRole
from app.db.models import VoiceProfile
from app.voice.voice_profile_service import create_profile, list_profiles
from tests.conftest import auth_headers, make_tenant, make_user

BASE = "/api/agent/voice-profiles"


async def _client_headers(client: AsyncClient, user: User) -> dict[str, str]:
    return await auth_headers(client, user)


async def _stored_status(db: AsyncSession, profile_id: uuid.UUID) -> str | None:
    """Read the status straight from the row, bypassing the session cache."""
    await db.rollback()
    row = await db.scalar(select(VoiceProfile.status).where(VoiceProfile.id == profile_id))
    return row


async def _seed_profile(
    db: AsyncSession, tenant: Tenant, *, name: str = "Support Rachael", status: str = "active"
) -> dict:
    profile = await create_profile(
        db,
        tenant_id=tenant.id,
        name=name,
        provider="elevenlabs",
        provider_voice_id=f"voice-{uuid.uuid4().hex[:10]}",
        language="en-US",
        locale="en-US",
        voice_type="provider",
        capabilities={},
        metadata={},
        created_by=None,
        status=status,
    )
    await db.commit()
    return {
        "id": str(profile.id),
        "name": profile.name,
        "provider": profile.provider,
        "status": profile.status,
    }


# ------------------------------------------------------------------- list ---


@pytest.mark.asyncio
async def test_list_returns_only_this_tenants_profiles(client: AsyncClient, db: AsyncSession) -> None:
    ours = await make_tenant(db, name="voice-list-ours")
    theirs = await make_tenant(db, name="voice-list-theirs")
    owner = await make_user(db, ours, UserRole.OWNER, email="voice-list-owner@example.com")
    headers = await _client_headers(client, owner)

    await _seed_profile(db, ours, name="Ours One")
    await _seed_profile(db, theirs, name="Theirs One")

    response = await client.get(BASE, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["total"] == 1
    assert [item["name"] for item in body["items"]] == ["Ours One"]


@pytest.mark.asyncio
async def test_list_filters_by_status_and_provider(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="voice-filter-org")
    owner = await make_user(db, tenant, UserRole.OWNER, email="voice-filter@example.com")
    headers = await _client_headers(client, owner)

    await _seed_profile(db, tenant, name="Active One", status="active")
    await _seed_profile(db, tenant, name="Idle One", status="inactive")

    active = await client.get(BASE, params={"status": "active"}, headers=headers)
    assert active.status_code == 200
    assert [item["name"] for item in active.json()["items"]] == ["Active One"]

    idle = await client.get(BASE, params={"status": "inactive"}, headers=headers)
    assert [item["name"] for item in idle.json()["items"]] == ["Idle One"]

    other = await client.get(BASE, params={"provider": "playht"}, headers=headers)
    assert other.json()["items"] == []


# ----------------------------------------------------------------- create ---


@pytest.mark.asyncio
async def test_create_persists_the_row_and_audits_the_actor(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="voice-create-org")
    owner = await make_user(db, tenant, UserRole.OWNER, email="voice-create@example.com")
    headers = await _client_headers(client, owner)

    response = await client.post(
        BASE,
        json={
            "name": "Sales Adam",
            "provider": "elevenlabs",
            "provider_voice_id": "adam-01",
            "language": "en-GB",
            "voice_type": "provider",
        },
        headers=headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Sales Adam"
    assert body["language"] == "en-GB"

    rows = await list_profiles(db, tenant.id)
    assert len(rows) == 1
    assert rows[0].name == "Sales Adam"
    assert rows[0].provider_voice_id == "adam-01"

    audit = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant.id,
                AuditLog.event_type == "voice_profile.created",
            )
        )
    ).all()
    assert len(audit) == 1
    assert audit[0].actor_email == "voice-create@example.com"
    assert audit[0].result == "success"


@pytest.mark.asyncio
async def test_create_rejects_an_unknown_voice_type(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="voice-type-org")
    owner = await make_user(db, tenant, UserRole.OWNER, email="voice-type@example.com")
    headers = await _client_headers(client, owner)

    response = await client.post(
        BASE,
        json={
            "name": "Mystery",
            "provider": "elevenlabs",
            "provider_voice_id": "x",
            "voice_type": "synthesized_by_hand",
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "voice_type_invalid"
    assert await list_profiles(db, tenant.id) == []


@pytest.mark.asyncio
async def test_create_rejects_a_duplicate_provider_voice(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="voice-dup-org")
    owner = await make_user(db, tenant, UserRole.OWNER, email="voice-dup@example.com")
    headers = await _client_headers(client, owner)
    payload = {
        "name": "First",
        "provider": "elevenlabs",
        "provider_voice_id": "duplicate-voice",
    }

    first = await client.post(BASE, json=payload, headers=headers)
    assert first.status_code == 201, first.text

    second = await client.post(BASE, json={**payload, "name": "Second"}, headers=headers)
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "voice_profile_duplicate"


@pytest.mark.asyncio
async def test_create_rejects_oversized_metadata(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="voice-meta-org")
    owner = await make_user(db, tenant, UserRole.OWNER, email="voice-meta@example.com")
    headers = await _client_headers(client, owner)

    response = await client.post(
        BASE,
        json={
            "name": "Too Much",
            "provider": "elevenlabs",
            "provider_voice_id": "meta-voice",
            "metadata": {f"k{i}": i for i in range(51)},
        },
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "metadata_too_large"


# ------------------------------------------------------------ read / state ---


@pytest.mark.asyncio
async def test_get_returns_one_profile_and_a_foreign_id_is_a_404(
    client: AsyncClient, db: AsyncSession
) -> None:
    ours = await make_tenant(db, name="voice-get-ours")
    theirs = await make_tenant(db, name="voice-get-theirs")
    owner = await make_user(db, ours, UserRole.OWNER, email="voice-get@example.com")
    headers = await _client_headers(client, owner)

    ours_profile = await _seed_profile(db, ours, name="Ours")
    theirs_profile = await _seed_profile(db, theirs, name="Theirs")

    ok = await client.get(f"{BASE}/{ours_profile['id']}", headers=headers)
    assert ok.status_code == 200
    assert ok.json()["name"] == "Ours"

    # Another tenant's id must not be confirmable: 404, not 403.
    missing = await client.get(f"{BASE}/{theirs_profile['id']}", headers=headers)
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "voice_profile_not_found"


@pytest.mark.asyncio
async def test_activate_moves_a_pending_profile_to_active(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Activation is the state machine's own rule, not the route's.

    ``voice_profile_service.validate_profile`` only accepts a profile that is
    ``active`` or ``pending``, because a profile the provider rejected or that
    an operator retired cannot be made usable by renaming a column.
    """
    tenant = await make_tenant(db, name="voice-activate-org")
    owner = await make_user(db, tenant, UserRole.OWNER, email="voice-activate@example.com")
    headers = await _client_headers(client, owner)
    profile = await _seed_profile(db, tenant, name="Pending", status="pending")

    response = await client.post(f"{BASE}/{profile['id']}/activate", json={}, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    assert await _stored_status(db, uuid.UUID(profile["id"])) == "active"


@pytest.mark.asyncio
async def test_deactivate_moves_an_active_profile_to_inactive(
    client: AsyncClient, db: AsyncSession
) -> None:
    tenant = await make_tenant(db, name="voice-deactivate-org")
    owner = await make_user(db, tenant, UserRole.OWNER, email="voice-deactivate@example.com")
    headers = await _client_headers(client, owner)
    profile = await _seed_profile(db, tenant, name="Active", status="active")

    response = await client.post(
        f"{BASE}/{profile['id']}/deactivate", json={"reason": "off-season"}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "inactive"
    assert await _stored_status(db, uuid.UUID(profile["id"])) == "inactive"


@pytest.mark.asyncio
async def test_activating_a_retired_profile_is_refused_with_the_service_reason(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A retired profile stays retired; the refusal names the real rule."""
    tenant = await make_tenant(db, name="voice-retired-org")
    owner = await make_user(db, tenant, UserRole.OWNER, email="voice-retired@example.com")
    headers = await _client_headers(client, owner)
    profile = await _seed_profile(db, tenant, name="Retired", status="inactive")

    response = await client.post(f"{BASE}/{profile['id']}/activate", json={}, headers=headers)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "voice_profile_not_activatable"
    assert await _stored_status(db, uuid.UUID(profile["id"])) == "inactive"


@pytest.mark.asyncio
async def test_state_changes_are_audited_with_the_actor_email(
    client: AsyncClient, db: AsyncSession
) -> None:
    tenant = await make_tenant(db, name="voice-audit-org")
    owner = await make_user(db, tenant, UserRole.OWNER, email="voice-audit@example.com")
    headers = await _client_headers(client, owner)
    pending = await _seed_profile(db, tenant, name="Pending Audited", status="pending")
    active = await _seed_profile(db, tenant, name="Active Audited", status="active")

    await client.post(f"{BASE}/{pending['id']}/activate", json={}, headers=headers)
    await client.post(f"{BASE}/{active['id']}/deactivate", json={}, headers=headers)

    events = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant.id,
                AuditLog.event_type.in_(
                    ["voice_profile.deactivated", "voice_profile.activated"]
                ),
            )
        )
    ).all()
    assert len(events) == 2
    assert {event.event_type for event in events} == {
        "voice_profile.deactivated",
        "voice_profile.activated",
    }
    assert all(event.actor_email == "voice-audit@example.com" for event in events)
    assert all(event.result == "success" for event in events)


@pytest.mark.asyncio
async def test_delete_removes_the_row_and_keeps_the_audit(
    client: AsyncClient, db: AsyncSession
) -> None:
    tenant = await make_tenant(db, name="voice-delete-org")
    owner = await make_user(db, tenant, UserRole.OWNER, email="voice-delete@example.com")
    headers = await _client_headers(client, owner)
    profile = await _seed_profile(db, tenant, name="Doomed", status="active")

    response = await client.delete(f"{BASE}/{profile['id']}", headers=headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "id": profile["id"]}
    assert await list_profiles(db, tenant.id) == []

    # The audit row outlives the profile it describes.
    audit = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant.id,
                AuditLog.event_type == "voice_profile.deleted",
            )
        )
    ).all()
    assert len(audit) == 1
    assert audit[0].actor_email == "voice-delete@example.com"


@pytest.mark.asyncio
async def test_delete_of_another_tenants_profile_is_a_404(
    client: AsyncClient, db: AsyncSession
) -> None:
    ours = await make_tenant(db, name="voice-del-ours")
    theirs = await make_tenant(db, name="voice-del-theirs")
    owner = await make_user(db, ours, UserRole.OWNER, email="voice-del@example.com")
    headers = await _client_headers(client, owner)
    theirs_profile = await _seed_profile(db, theirs, name="Untouchable")

    response = await client.delete(f"{BASE}/{theirs_profile['id']}", headers=headers)

    assert response.status_code == 404
    assert await list_profiles(db, theirs.id) != []


# ------------------------------------------------------------------- RBAC ---


@pytest.mark.asyncio
async def test_a_viewer_can_read_but_cannot_write(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="voice-rbac-org")
    viewer = await make_user(db, tenant, UserRole.VIEWER, email="voice-viewer@example.com")
    headers = await _client_headers(client, viewer)

    listed = await client.get(BASE, headers=headers)
    assert listed.status_code == 200

    created = await client.post(
        BASE,
        json={"name": "Nope", "provider": "elevenlabs", "provider_voice_id": "nope"},
        headers=headers,
    )
    assert created.status_code == 403


@pytest.mark.asyncio
async def test_an_unauthenticated_request_is_refused(client: AsyncClient) -> None:
    response = await client.get(BASE)
    assert response.status_code in (401, 403)


# --------------------------------------------------------------- contract ---


@pytest.mark.asyncio
async def test_the_response_shape_is_the_public_allowlist(client: AsyncClient, db: AsyncSession) -> None:
    """No internal column leaks into the payload.

    A profile row carries provider credentials and tenant identifiers, none of
    which belong in a dashboard response.
    """
    tenant = await make_tenant(db, name="voice-shape-org")
    owner = await make_user(db, tenant, UserRole.OWNER, email="voice-shape@example.com")
    headers = await _client_headers(client, owner)
    await _seed_profile(db, tenant, name="Shaped")

    body = (await client.get(BASE, headers=headers)).json()
    keys = set(body["items"][0])
    assert "tenant_id" not in keys
    assert "api_key" not in keys
    assert keys == {
        "id",
        "name",
        "provider",
        "provider_voice_id",
        "language",
        "locale",
        "voice_type",
        "status",
        "capabilities",
        "metadata",
        "created_at",
        "updated_at",
    }
