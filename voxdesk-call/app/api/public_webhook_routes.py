from __future__ import annotations
import hashlib
import uuid
from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.core.config import settings
from app.db.models import AuditAction, PublicWebhookEndpoint, PublicWebhookReceipt
from app.db.rls import set_public_webhook_lookup, set_tenant_context
from app.db.session import get_sessionmaker
from app.security.secret_store import SecretStoreError, get_secret_store
from app.webhooks.signing import verify

router = APIRouter(prefix="/public/webhooks", tags=["public-webhooks"])


@router.post("/{endpoint_id}")
async def receive(
    endpoint_id: uuid.UUID,
    request: Request,
    x_voxdesk_signature: str = Header("", alias="X-VoxDesk-Signature"),
    x_voxdesk_event: str = Header("", alias="X-VoxDesk-Event"),
):
    if not x_voxdesk_event or len(x_voxdesk_event) > 180:
        raise HTTPException(400, "event id is required")
    body = await request.body()
    if len(body) > 1024 * 1024:
        raise HTTPException(413, "webhook body is too large")
    from app.auth.identity.events import emit as audit_event

    async with get_sessionmaker()() as session:
        await set_public_webhook_lookup(session, endpoint_id)
        endpoint = await session.get(PublicWebhookEndpoint, endpoint_id)
        if endpoint is None or not endpoint.enabled:
            raise HTTPException(404, "webhook endpoint not found")
        await set_tenant_context(session, endpoint.tenant_id)
        try:
            secret = get_secret_store().get(
                str(endpoint.tenant_id), f"webhook:{endpoint.id}", endpoint.secret_ref
            )
        except SecretStoreError:
            raise HTTPException(503, "webhook endpoint is unavailable") from None
        if not verify(
            secret,
            body,
            x_voxdesk_signature,
            tolerance_seconds=settings.crm_webhook_tolerance_seconds,
        ):
            raise HTTPException(401, "invalid webhook signature")
        receipt = PublicWebhookReceipt(
            tenant_id=endpoint.tenant_id,
            endpoint_id=endpoint.id,
            event_id=x_voxdesk_event,
            payload_digest=hashlib.sha256(body).hexdigest(),
        )
        try:
            async with session.begin_nested():
                session.add(receipt)
                await session.flush()
        except IntegrityError:
            existing = (
                await session.execute(
                    select(PublicWebhookReceipt).where(
                        PublicWebhookReceipt.endpoint_id == endpoint.id,
                        PublicWebhookReceipt.event_id == x_voxdesk_event,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None and existing.payload_digest != receipt.payload_digest:
                raise HTTPException(409, "event id was already used for a different payload")
            return {"accepted": True, "duplicate": True, "event_id": x_voxdesk_event}
        await audit_event(
            session,
            AuditAction.INTEGRATION_TESTED,
            tenant_id=endpoint.tenant_id,
            detail={
                "endpoint_id": str(endpoint.id),
                "event_id": x_voxdesk_event,
                "outcome": "accepted",
            },
        )
        await session.commit()
        return {"accepted": True, "duplicate": False, "event_id": x_voxdesk_event}
