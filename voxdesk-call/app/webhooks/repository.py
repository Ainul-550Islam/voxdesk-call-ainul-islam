"""Durable webhook subscriptions and deliveries.

The signing secret is sealed when encryption is available. It is never written
to logs. Duplicate ``subscription_id + event_id`` cannot insert two deliveries.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import WebhookDelivery, WebhookSubscription


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
    session: AsyncSession, *, tenant_id: uuid.UUID, subscription_id: uuid.UUID
) -> WebhookSubscription | None:
    row = await session.get(WebhookSubscription, subscription_id)
    if row is None or row.tenant_id != tenant_id:
        return None
    return row


async def create_delivery(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    subscription_id: uuid.UUID,
    event_id: str,
    environment_id: uuid.UUID | None = None,
) -> tuple[WebhookDelivery, bool]:
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
                    WebhookDelivery.subscription_id == subscription_id,
                    WebhookDelivery.event_id == event_id,
                )
            )
        ).scalar_one()
        return found, False
    return row, True
