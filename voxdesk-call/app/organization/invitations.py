"""Membership invitation lifecycle.

The raw token is returned once to the inviter and stored only as a SHA-256
digest. Audit detail carries the invitation id, never the token and never a
field named token — the identity scrubber would mask it, and we do not rely
on that as the only control.

Accepting an invitation binds the organization and tenant stored on the row.
A client-supplied organization or tenant id that disagrees is rejected with
the same response as an unknown token.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit
from app.auth.identity.tokens import hash_token
from app.auth.password import verify_dummy
from app.auth.service import normalize_email
from app.db.models import (
    AuditAction,
    InvitationScope,
    MembershipInvitation,
    MembershipStatus,
    Organization,
    OrganizationMembership,
    Tenant,
    TenantMembership,
    User,
    UserRole,
)
from app.organization.membership_policies import (
    assert_can_grant,
    assert_organization_accepts_members,
)
from app.organization.membership_service import organization_for_user
from app.organization.service import Actor
from app.tenancy.isolation import Conflict, LifecycleDenied

INVITATION_TTL = timedelta(days=7)
_INVALID = "Invitation is not valid."


class InvitationInvalid(Exception):
    """Every public invitation failure. The message does not say why."""

    def __init__(self, message: str = _INVALID) -> None:
        super().__init__(message)
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _actor(actor: Actor | None) -> Actor:
    return actor or Actor()


def issue_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def binding_key(
    scope: str, organization_id: uuid.UUID, tenant_id: uuid.UUID | None
) -> str:
    if scope == InvitationScope.TENANT.value:
        return f"tenant:{tenant_id}"
    return f"org:{organization_id}"


def _ids_disagree(
    invitation: MembershipInvitation,
    *,
    organization_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None,
) -> bool:
    if organization_id is not None and organization_id != invitation.organization_id:
        return True
    if tenant_id is not None and invitation.tenant_id is not None:
        if tenant_id != invitation.tenant_id:
            return True
    if tenant_id is not None and invitation.scope == InvitationScope.ORGANIZATION.value:
        if invitation.tenant_id is None and tenant_id != invitation.home_tenant_id:
            return True
    return False


async def _audit(
    session: AsyncSession,
    action: AuditAction,
    *,
    actor: Actor,
    invitation: MembershipInvitation,
    event: str,
) -> None:
    await emit(
        session,
        action,
        tenant_id=actor.tenant_id or invitation.home_tenant_id,
        actor_user_id=actor.user_id,
        target_user_id=invitation.accepted_by_user_id,
        actor_email=actor.email,
        ip_address=actor.ip_address,
        user_agent=actor.user_agent,
        detail={
            "invitation_id": str(invitation.id),
            "organization_id": str(invitation.organization_id),
            "scope": invitation.scope,
            "event": event,
            "role": invitation.role.value,
        },
        commit=False,
    )


async def _live_duplicate(
    session: AsyncSession, key: str, email: str
) -> MembershipInvitation | None:
    return (
        await session.execute(
            select(MembershipInvitation).where(
                MembershipInvitation.binding_key == key,
                MembershipInvitation.email == email,
                MembershipInvitation.status == MembershipStatus.INVITED.value,
            )
        )
    ).scalar_one_or_none()


async def create_invitation(
    session: AsyncSession,
    *,
    actor_user: User,
    scope: str,
    organization_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    home_tenant_id: uuid.UUID,
    email: str,
    role: UserRole,
    actor: Actor | None = None,
) -> tuple[MembershipInvitation, str]:
    """Create one live invitation. The second item is the raw token, once."""
    organization = await session.get(Organization, organization_id)
    if organization is None:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    actor_org = await organization_for_user(session, actor_user)
    if actor_org is None or actor_org.id != organization.id:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    assert_can_grant(actor_user, role)
    assert_organization_accepts_members(organization)
    if scope == InvitationScope.TENANT.value:
        tenant = await session.get(Tenant, tenant_id) if tenant_id else None
        if tenant is None or tenant.organization_id != organization_id:
            from app.tenancy.isolation import BoundaryDenied
            raise BoundaryDenied()
        if tenant.lifecycle_status in ("suspended", "deleted", "read_only"):
            raise LifecycleDenied("This tenant is not accepting members")
    home = await session.get(Tenant, home_tenant_id)
    if home is None or home.organization_id != organization_id:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    normalized = normalize_email(email)
    key = binding_key(scope, organization_id, tenant_id)
    if await _live_duplicate(session, key, normalized) is not None:
        raise Conflict("An invitation is already pending")
    raw, digest = issue_token()
    now = _now()
    row = MembershipInvitation(
        scope=scope,
        organization_id=organization_id,
        tenant_id=tenant_id,
        home_tenant_id=home_tenant_id,
        email=normalized,
        role=role,
        status=MembershipStatus.INVITED.value,
        token_hash=digest,
        binding_key=key,
        expires_at=now + INVITATION_TTL,
        invited_by_user_id=actor_user.id,
    )
    session.add(row)
    await session.flush()
    who = _actor(actor)
    await _audit(
        session, AuditAction.INVITATION_CREATED, actor=who, invitation=row,
        event="invitation.created",
    )
    await session.commit()
    await session.refresh(row)
    return row, raw


async def _lookup(session: AsyncSession, raw_token: str) -> MembershipInvitation | None:
    if not raw_token or len(raw_token) > 200:
        return None
    digest = hash_token(raw_token)
    return (
        await session.execute(
            select(MembershipInvitation).where(MembershipInvitation.token_hash == digest)
        )
    ).scalar_one_or_none()


async def revoke_invitation(
    session: AsyncSession,
    *,
    actor_user: User,
    invitation_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor: Actor | None = None,
) -> MembershipInvitation:
    row = await session.get(MembershipInvitation, invitation_id)
    if row is None or row.organization_id != organization_id:
        from app.tenancy.isolation import NotFound
        raise NotFound()
    actor_org = await organization_for_user(session, actor_user)
    if actor_org is None or actor_org.id != organization_id:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    if row.status != MembershipStatus.INVITED.value:
        return row
    now = _now()
    row.status = MembershipStatus.REVOKED.value
    row.revoked_at = now
    row.updated_at = now
    # Rotate the digest so a copied token cannot be raced in after revoke.
    row.token_hash = hash_token(secrets.token_urlsafe(32))
    await _audit(
        session, AuditAction.INVITATION_REVOKED, actor=_actor(actor), invitation=row,
        event="invitation.revoked",
    )
    await session.commit()
    await session.refresh(row)
    return row


async def resend_invitation(
    session: AsyncSession,
    *,
    actor_user: User,
    invitation_id: uuid.UUID,
    organization_id: uuid.UUID,
    actor: Actor | None = None,
) -> tuple[MembershipInvitation, str]:
    row = await session.get(MembershipInvitation, invitation_id)
    if row is None or row.organization_id != organization_id:
        from app.tenancy.isolation import NotFound
        raise NotFound()
    actor_org = await organization_for_user(session, actor_user)
    if actor_org is None or actor_org.id != organization_id:
        from app.tenancy.isolation import BoundaryDenied
        raise BoundaryDenied()
    if row.status != MembershipStatus.INVITED.value:
        raise InvitationInvalid()
    raw, digest = issue_token()
    now = _now()
    row.token_hash = digest
    row.expires_at = now + INVITATION_TTL
    row.updated_at = now
    await _audit(
        session, AuditAction.INVITATION_RESENT, actor=_actor(actor), invitation=row,
        event="invitation.resent",
    )
    await session.commit()
    await session.refresh(row)
    return row, raw


async def _ensure_membership(
    session: AsyncSession,
    *,
    user: User,
    invitation: MembershipInvitation,
) -> None:
    now = _now()
    org_row = (
        await session.execute(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == invitation.organization_id,
                OrganizationMembership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if org_row is None:
        session.add(OrganizationMembership(
            organization_id=invitation.organization_id,
            user_id=user.id,
            role=invitation.role,
            status=MembershipStatus.ACTIVE.value,
            accepted_at=now,
        ))
    else:
        org_row.status = MembershipStatus.ACTIVE.value
        org_row.role = invitation.role
        org_row.accepted_at = now
        org_row.suspended_at = None
    tenant_id = invitation.tenant_id or invitation.home_tenant_id
    tenant_row = (
        await session.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if tenant_row is None:
        session.add(TenantMembership(
            tenant_id=tenant_id,
            user_id=user.id,
            role=invitation.role,
            status=MembershipStatus.ACTIVE.value,
            accepted_at=now,
        ))
    else:
        tenant_row.status = MembershipStatus.ACTIVE.value
        tenant_row.role = invitation.role
        tenant_row.accepted_at = now
        tenant_row.suspended_at = None


async def _bind_user(
    session: AsyncSession,
    invitation: MembershipInvitation,
    *,
    authenticated: User | None,
    email: str | None,
    password: str | None,
) -> User:
    from app.auth import password as pw

    if authenticated is not None:
        if normalize_email(authenticated.email) != invitation.email:
            raise InvitationInvalid()
        user = authenticated
    else:
        if not email or normalize_email(email) != invitation.email:
            verify_dummy()
            raise InvitationInvalid()
        user = (
            await session.execute(select(User).where(User.email == invitation.email))
        ).scalar_one_or_none()
        if user is None:
            if not password:
                raise InvitationInvalid()
            pw.validate_policy(password, email=invitation.email)
            user = User(
                tenant_id=invitation.home_tenant_id,
                email=invitation.email,
                full_name="",
                password_hash=pw.hash_password(password),
                role=invitation.role,
                is_active=True,
            )
            session.add(user)
            await session.flush()
        else:
            if not password or not pw.verify_password(password, user.password_hash):
                verify_dummy()
                raise InvitationInvalid()
    home = await organization_for_user(session, user)
    if home is None or home.id != invitation.organization_id:
        verify_dummy()
        raise InvitationInvalid()
    if invitation.tenant_id is not None and user.tenant_id != invitation.tenant_id:
        # A user already bound to another tenant in this organization is not
        # moved. The invitation cannot rewrite tenant_id.
        if user.tenant_id != invitation.home_tenant_id:
            raise InvitationInvalid()
    await _ensure_membership(session, user=user, invitation=invitation)
    if user.role is not invitation.role and user.tenant_id == (
        invitation.tenant_id or invitation.home_tenant_id
    ):
        user.role = invitation.role
        user.token_version += 1
    return user


async def accept_invitation(
    session: AsyncSession,
    *,
    raw_token: str,
    organization_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    authenticated: User | None = None,
    email: str | None = None,
    password: str | None = None,
    actor: Actor | None = None,
) -> MembershipInvitation:
    """Consume one invited row. A second accept finds nothing left to consume."""
    row = await _lookup(session, raw_token)
    if row is None:
        verify_dummy()
        raise InvitationInvalid()
    if _ids_disagree(row, organization_id=organization_id, tenant_id=tenant_id):
        raise InvitationInvalid()
    now = _now()
    if row.expires_at <= now and row.status == MembershipStatus.INVITED.value:
        row.status = MembershipStatus.EXPIRED.value
        row.updated_at = now
        await session.commit()
        raise InvitationInvalid()
    if row.status != MembershipStatus.INVITED.value:
        raise InvitationInvalid()
    result = await session.execute(
        update(MembershipInvitation)
        .where(
            MembershipInvitation.id == row.id,
            MembershipInvitation.status == MembershipStatus.INVITED.value,
        )
        .values(status="accepted", accepted_at=now, updated_at=now)
    )
    if result.rowcount != 1:
        raise InvitationInvalid()
    await session.refresh(row)
    try:
        user = await _bind_user(
            session, row, authenticated=authenticated, email=email, password=password,
        )
    except InvitationInvalid:
        await session.rollback()
        raise
    row.accepted_by_user_id = user.id
    who = _actor(actor)
    if who.user_id is None:
        who = Actor(
            user_id=user.id, email=user.email, tenant_id=user.tenant_id,
            ip_address=who.ip_address, user_agent=who.user_agent,
        )
    await _audit(
        session, AuditAction.INVITATION_ACCEPTED, actor=who, invitation=row,
        event="invitation.accepted",
    )
    await session.commit()
    await session.refresh(row)
    return row
