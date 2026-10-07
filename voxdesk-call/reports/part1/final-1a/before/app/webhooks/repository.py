"""Durable webhook subscriptions and deliveries.

The signing secret is sealed when encryption is available. It is never written
to logs. Duplicate ``subscription_id + event_id`` cannot insert two deliveries.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Environment, WebhookDelivery, WebhookSubscription
from app.core.ssrf import validate_outbound_url
from app.tenancy.isolation import BoundaryDenied

_UNSCOPED = object()


def seal_secret(tenant_id: uuid.UUID, secret: str) -> str:
    from app.auth.identity.secrets import encryption_available, encrypt_text

    if encryption_available():
        envelope, _key = encrypt_text(secret, tenant_id=str(tenant_id), purpose="outbound_webhook")
        return envelope
    if settings.is_production:
        from app.auth.identity.exceptions import IdentitySecretsUnavailable

        raise IdentitySecretsUnavailable("webhook secrets require the encryption key ring")
    return "local:" + secret


def open_secret(tenant_id: uuid.UUID, envelope: str) -> str:
    if envelope.startswith("local:"):
        if settings.is_production:
            from app.auth.identity.exceptions import IdentitySecretsUnavailable

            raise IdentitySecretsUnavailable("refusing an unsealed webhook secret")
        return envelope[len("local:") :]
    from app.auth.identity.secrets import decrypt_text

    return decrypt_text(envelope, tenant_id=str(tenant_id), purpose="outbound_webhook")


async def create_subscription(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    endpoint: str,
    secret: str,
    event_types: list[str],
    environment_id: uuid.UUID | None = None,
) -> WebhookSubscription:
    await _validate_environment(session, tenant_id, environment_id)
    validate_outbound_url(endpoint, require_https=True)
    event_types = _event_types(event_types)
    if not secret:
        raise ValueError("Webhook secret must not be empty")
    row = WebhookSubscription(
        tenant_id=tenant_id,
        environment_id=environment_id,
        endpoint=endpoint,
        enabled=True,
        event_types=list(event_types),
        secret_envelope=seal_secret(tenant_id, secret),
        secret_version=1,
    )
    session.add(row)
    await session.flush()
    return row


async def get_subscription(
    session: AsyncSession, *, tenant_id: uuid.UUID, subscription_id: uuid.UUID,
    environment_id: uuid.UUID | None | object = _UNSCOPED,
) -> WebhookSubscription | None:
    """Tenant-wide internal lookup unless an exact environment is supplied.

    Environment-aware API callers must explicitly pass environment_id, including
    None for a tenant-wide subscription. Omitting it preserves internal worker
    compatibility; it must not be used to bypass an authenticated environment.
    """
    query = select(WebhookSubscription).where(
        WebhookSubscription.id == subscription_id,
        WebhookSubscription.tenant_id == tenant_id,
    )
    if environment_id is not _UNSCOPED:
        query = query.where(WebhookSubscription.environment_id == environment_id)
    return (await session.execute(query)).scalar_one_or_none()


async def create_delivery(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    subscription_id: uuid.UUID,
    event_id: str,
    environment_id: uuid.UUID | None = None,
) -> tuple[WebhookDelivery, bool]:
    subscription = await get_subscription(
        session, tenant_id=tenant_id, subscription_id=subscription_id,
    )
    if subscription is None or (
        subscription.environment_id is not None
        and subscription.environment_id != environment_id
    ):
        raise BoundaryDenied()
    await _validate_environment(session, tenant_id, environment_id)
    if not event_id or len(event_id) > 128:
        raise ValueError("Webhook event identity must contain 1 to 128 characters")
    row = WebhookDelivery(
        tenant_id=tenant_id,
        environment_id=environment_id,
        subscription_id=subscription_id,
        event_id=event_id,
        status="queued",
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        found = (
            await session.execute(
                select(WebhookDelivery).where(
                    WebhookDelivery.tenant_id == tenant_id,
                    WebhookDelivery.environment_id == environment_id,
                    WebhookDelivery.subscription_id == subscription_id,
                    WebhookDelivery.event_id == event_id,
                )
            )
        ).scalar_one_or_none()
        if found is None:
            raise
        return found, False
    return row, True


async def _validate_environment(session, tenant_id, environment_id) -> None:
    if environment_id is not None:
        owned = await session.scalar(select(Environment.id).where(
            Environment.id == environment_id, Environment.tenant_id == tenant_id,
        ))
        if owned is None:
            raise BoundaryDenied()


def _event_types(values: list[str]) -> list[str]:
    if not isinstance(values, list) or len(values) > 100 or any(
        not isinstance(value, str) or not value.strip() or len(value) > 128
        for value in values
    ):
        raise ValueError("event_types must contain at most 100 nonempty event names")
    return list(dict.fromkeys(values))


async def list_subscriptions(
    session: AsyncSession, *, tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None, limit: int = 100, offset: int = 0,
) -> list[WebhookSubscription]:
    """List one exact authenticated scope, with bounded deterministic paging."""
    query = select(WebhookSubscription).where(
        WebhookSubscription.tenant_id == tenant_id,
        WebhookSubscription.environment_id == environment_id,
    ).order_by(WebhookSubscription.created_at, WebhookSubscription.id)
    return list((await session.scalars(
        query.limit(max(1, min(limit, 200))).offset(max(0, offset))
    )).all())


async def _locked_subscription(session, tenant_id, environment_id, subscription_id):
    row = (await session.execute(select(WebhookSubscription).where(
        WebhookSubscription.id == subscription_id,
        WebhookSubscription.tenant_id == tenant_id,
        WebhookSubscription.environment_id == environment_id,
    ).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if row is None:
        raise BoundaryDenied()
    return row


async def update_subscription(
    session: AsyncSession, *, tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None, subscription_id: uuid.UUID,
    endpoint: str | None = None, enabled: bool | None = None,
    event_types: list[str] | None = None,
) -> WebhookSubscription:
    """Modify only mutable fields. Scope and signing secret cannot be patched."""
    row = await _locked_subscription(session, tenant_id, environment_id, subscription_id)
    if endpoint is not None:
        validate_outbound_url(endpoint, require_https=True)
    if event_types is not None:
        event_types = _event_types(event_types)
    if enabled is not None and not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    if endpoint is not None:
        row.endpoint = endpoint
    if enabled is not None:
        row.enabled = enabled
    if event_types is not None:
        row.event_types = event_types
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return row


async def rotate_secret(
    session: AsyncSession, *, tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None, subscription_id: uuid.UUID, secret: str,
) -> WebhookSubscription:
    """Seal a caller-generated secret and increment its durable version.

    PostgreSQL row locking serializes concurrent rotations. Flush only, so an
    API audit write and this rotation may commit or roll back as one unit.
    """
    row = await _locked_subscription(session, tenant_id, environment_id, subscription_id)
    if not secret:
        raise ValueError("Webhook secret must not be empty")
    envelope = seal_secret(tenant_id, secret)
    row.secret_envelope = envelope
    row.secret_version += 1
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return row


async def delete_subscription(
    session: AsyncSession, *, tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None, subscription_id: uuid.UUID,
) -> None:
    """Delete the owned row; the existing delivery FK defines its cascade."""
    row = await _locked_subscription(session, tenant_id, environment_id, subscription_id)
    await session.delete(row)
    await session.flush()
