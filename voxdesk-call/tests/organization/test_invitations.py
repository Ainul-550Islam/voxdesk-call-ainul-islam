"""Invitation create, accept, expiry and cross-scope rejection."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.models import AuditAction, AuditLog, MembershipInvitation, User
from app.organization.invitations import INVITATION_TTL
from tests.conftest import TEST_PASSWORD, auth_headers

pytestmark = pytest.mark.asyncio


async def _invite(client, headers, organization_id, email, role="organization_member"):
    return await client.post(
        f"/api/organizations/{organization_id}/invitations",
        json={"email": email, "role": role},
        headers=headers,
    )


async def test_create_accept_revoke_resend_and_duplicate(client, db, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    org = tenant_a.organization_id
    email = f"invitee-{uuid.uuid4().hex[:8]}@example.com"
    created = await _invite(client, headers, org, email)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["invitation_token"]
    assert "token_hash" not in body
    duplicate = await _invite(client, headers, org, email)
    assert duplicate.status_code == 409, duplicate.text

    accepted = await client.post(
        f"/api/organizations/{org}/invitations/accept",
        json={
            "invitation_token": body["invitation_token"],
            "email": email,
            "password": TEST_PASSWORD,
        },
    )
    assert accepted.status_code == 200, accepted.text
    user = (
        await db.execute(select(User).where(User.email == email))
    ).scalar_one()
    assert user.tenant_id == tenant_a.id
    again = await client.post(
        f"/api/organizations/{org}/invitations/accept",
        json={"invitation_token": body["invitation_token"], "email": email, "password": TEST_PASSWORD},
    )
    assert again.status_code == 400, again.text

    second = await _invite(client, headers, org, f"other-{uuid.uuid4().hex[:6]}@example.com")
    assert second.status_code == 200, second.text
    revoked = await client.post(
        f"/api/organizations/{org}/invitations/{second.json()['id']}/revoke",
        headers=headers,
    )
    assert revoked.status_code == 200, revoked.text
    dead = await client.post(
        f"/api/organizations/{org}/invitations/accept",
        json={
            "invitation_token": second.json()["invitation_token"],
            "email": f"other-{uuid.uuid4().hex[:6]}@example.com",
            "password": TEST_PASSWORD,
        },
    )
    assert dead.status_code == 400

    third = await _invite(client, headers, org, f"resend-{uuid.uuid4().hex[:6]}@example.com")
    resent = await client.post(
        f"/api/organizations/{org}/invitations/{third.json()['id']}/resend",
        headers=headers,
    )
    assert resent.status_code == 200, resent.text
    assert resent.json()["invitation_token"] != third.json()["invitation_token"]
    old = await client.post(
        f"/api/organizations/{org}/invitations/accept",
        json={
            "invitation_token": third.json()["invitation_token"],
            "email": third.json().get("email", "nobody@example.com"),
            "password": TEST_PASSWORD,
        },
    )
    assert old.status_code == 400

    audits = (
        await db.execute(
            select(AuditLog).where(AuditLog.action == AuditAction.INVITATION_CREATED)
        )
    ).scalars().all()
    assert audits
    blob = str(audits[-1].detail)
    assert "invitation_token" not in blob
    assert body["invitation_token"] not in blob


async def test_expired_wrong_org_and_invalid_token_look_the_same(
    client, db, tenant_a, tenant_b, owner_a
):
    headers = await auth_headers(client, owner_a)
    email = f"expire-{uuid.uuid4().hex[:8]}@example.com"
    created = await _invite(client, headers, tenant_a.organization_id, email)
    assert created.status_code == 200, created.text
    row = await db.get(MembershipInvitation, uuid.UUID(created.json()["id"]))
    row.expires_at = row.expires_at - INVITATION_TTL - timedelta(days=1)
    await db.commit()
    expired = await client.post(
        f"/api/organizations/{tenant_a.organization_id}/invitations/accept",
        json={"invitation_token": created.json()["invitation_token"], "email": email,
              "password": TEST_PASSWORD},
    )
    wrong_org = await client.post(
        f"/api/organizations/{tenant_b.organization_id}/invitations/accept",
        json={"invitation_token": created.json()["invitation_token"], "email": email,
              "password": TEST_PASSWORD},
    )
    # The expired token was consumed as expired, so mint a fresh one for the
    # cross-organization attempt.
    fresh = await _invite(client, headers, tenant_a.organization_id, f"cross-{uuid.uuid4().hex[:6]}@example.com")
    crossed = await client.post(
        f"/api/organizations/{tenant_b.organization_id}/invitations/accept",
        json={
            "invitation_token": fresh.json()["invitation_token"],
            "email": f"cross-{uuid.uuid4().hex[:6]}@example.com",
            "password": TEST_PASSWORD,
            "organization_id": str(tenant_b.organization_id),
        },
    )
    unknown = await client.post(
        f"/api/organizations/{tenant_a.organization_id}/invitations/accept",
        json={"invitation_token": "not-a-real-invitation-token-value", "email": email,
              "password": TEST_PASSWORD},
    )
    assert expired.status_code == 400
    assert crossed.status_code == 400
    assert unknown.status_code == 400
    assert expired.json() == unknown.json()
    assert crossed.json()["detail"]["code"] == "invitation_invalid"
    assert wrong_org.status_code == 400
    still = await db.get(User, row.accepted_by_user_id) if row.accepted_by_user_id else None
    assert still is None


async def test_invitation_response_does_not_reveal_whether_the_email_exists(
    client, tenant_a, tenant_b, owner_a, owner_b
):
    headers = await auth_headers(client, owner_a)
    known = await _invite(client, headers, tenant_a.organization_id, owner_b.email)
    unknown = await _invite(
        client, headers, tenant_a.organization_id, f"nobody-{uuid.uuid4().hex[:8]}@example.com"
    )
    assert known.status_code == 200, known.text
    assert unknown.status_code == 200, unknown.text
    assert set(known.json()) == set(unknown.json())
    assert "exists" not in known.text.lower()


async def test_tenant_invitation_cannot_jump_organizations(
    client, tenant_a, tenant_b, owner_a
):
    headers = await auth_headers(client, owner_a)
    created = await client.post(
        f"/api/tenants/{tenant_a.id}/invitations",
        json={
            "email": f"tenant-invite-{uuid.uuid4().hex[:6]}@example.com",
            "role": "tenant_member",
            "organization_id": str(tenant_b.organization_id),
            "tenant_id": str(tenant_b.id),
        },
        headers=headers,
    )
    assert created.status_code == 404, created.text
