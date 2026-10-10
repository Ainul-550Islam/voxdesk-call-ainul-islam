from __future__ import annotations
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from app.db.models import AuditAction, EmailDelivery
from .delivery import EmailMessage, EmailSendResult, recipient_hash
from .providers import EmailConfig, build_provider


async def send(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    recipient: str,
    subject: str,
    body: str,
    provider: str,
    secret_ref: str | None,
    from_address: str,
    idempotency_key: str,
    domain: str = "",
    environment_id=None,
) -> EmailDelivery:
    if not idempotency_key or len(idempotency_key) > 180:
        raise ValueError("bounded idempotency key is required")
    existing = (
        await session.execute(
            select(EmailDelivery).where(
                EmailDelivery.tenant_id == tenant_id,
                EmailDelivery.idempotency_key == idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = EmailDelivery(
        tenant_id=tenant_id,
        environment_id=environment_id,
        recipient_hash=recipient_hash(recipient),
        template_name="",
        subject=subject[:255],
        provider=provider[:64],
        idempotency_key=idempotency_key,
        status="sending",
        attempt_count=1,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = (
            await session.execute(
                select(EmailDelivery).where(
                    EmailDelivery.tenant_id == tenant_id,
                    EmailDelivery.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        raise
    try:
        from app.auth.identity.events import emit as audit_event

        result: EmailSendResult = await build_provider(
            EmailConfig(provider, secret_ref, from_address, domain)
        ).send(EmailMessage(str(tenant_id), recipient, subject, body))
        row.status, row.error_category = result.outcome, result.category
        await audit_event(
            session,
            AuditAction.INTEGRATION_TESTED,
            tenant_id=tenant_id,
            detail={"provider": provider, "outcome": result.outcome, "category": result.category},
        )
    except Exception as exc:
        __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
        row.status, row.error_category = "permanent_failure", type(exc).__name__
    await session.flush()
    return row
