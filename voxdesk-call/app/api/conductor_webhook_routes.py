"""Signed, tenant-scoped Conductor callback ingestion.

Callbacks require both a tenant-authorized API principal and an HMAC signature.
A database uniqueness constraint makes event delivery idempotent across workers
and concurrent retries; the receipt, evidence, and audit event commit together.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Permission, TenantContext, require_permission
from app.core.config import settings
from app.core.errors import AppError
from app.db.models import ConductorEvidenceSourceEnum, ConductorWebhookReceipt
from app.db.session import get_session
from app.governance.audit import record_conductor_audit
from app.services.conductor_evidence_service import record_proposal_evidence
from app.services.conductor_proposal_service import get_proposal_row

router = APIRouter(prefix="/api/v1/conductor/webhooks", tags=["conductor-webhooks"])
_SIGNATURE_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class ConductorWebhookEventPayload(BaseModel):
    event_id: str = Field(..., min_length=1, max_length=120)
    event_type: str = Field(..., min_length=1, max_length=64)
    proposal_id: uuid.UUID
    source_id: str = Field(..., min_length=1, max_length=120)
    summary: str = Field(default="", max_length=500)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_id", "event_type", "source_id")
    @classmethod
    def _nonblank_identifiers(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must contain a non-whitespace character")
        return value


def _verify_signature(raw_body: bytes, signature_header: str | None) -> None:
    secret = settings.conductor_webhook_secret.strip()
    if not secret:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "webhook_signature_not_configured",
                "message": "Conductor webhook signature verification is unavailable",
            },
        )
    if not signature_header:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "webhook_signature_missing",
                "message": "Missing X-Conductor-Signature header",
            },
        )
    provided = signature_header.strip()
    if provided.startswith("sha256="):
        provided = provided[len("sha256="):]
    if not _SIGNATURE_RE.fullmatch(provided):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "webhook_signature_invalid",
                "message": "Invalid X-Conductor-Signature header",
            },
        )
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided.lower()):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "webhook_signature_invalid",
                "message": "Invalid X-Conductor-Signature header",
            },
        )


def _replay_response(
    receipt: ConductorWebhookReceipt,
    proposal_id: uuid.UUID,
) -> dict[str, Any]:
    if receipt.proposal_id != proposal_id:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "webhook_event_id_conflict",
                "message": "The event ID is already bound to another proposal",
            },
        )
    return {
        "accepted": True,
        "idempotent_replay": True,
        "proposal_id": str(receipt.proposal_id),
        "evidence_id": str(receipt.evidence_id) if receipt.evidence_id else None,
    }


@router.post("/events")
async def ingest_conductor_webhook_event(
    request: Request,
    body: ConductorWebhookEventPayload,
    x_conductor_signature: str | None = Header(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    # Starlette caches the body consumed by Pydantic, so the signed bytes are
    # exactly those represented by this validated event object.
    raw_bytes = await request.body()
    _verify_signature(raw_bytes, x_conductor_signature)

    tenant_id = uuid.UUID(str(ctx.tenant_id))
    try:
        existing = await session.scalar(
            select(ConductorWebhookReceipt).where(
                ConductorWebhookReceipt.tenant_id == tenant_id,
                ConductorWebhookReceipt.event_id == body.event_id,
            )
        )
        if existing is not None:
            return _replay_response(existing, body.proposal_id)

        proposal = await get_proposal_row(session, tenant_id, body.proposal_id)
        receipt = ConductorWebhookReceipt(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            proposal_id=proposal.id,
            event_id=body.event_id,
        )
        session.add(receipt)
        # Claim the tenant/event pair before producing evidence. The unique
        # constraint arbitrates concurrent deliveries on separate workers.
        await session.flush()

        ev_row = await record_proposal_evidence(
            session,
            tenant_id=tenant_id,
            proposal_id=proposal.id,
            source_type=ConductorEvidenceSourceEnum.TEST_RUN,
            source_id=body.source_id,
            evidence_summary=body.summary or f"Async webhook event: {body.event_type}",
            evidence_payload={
                "webhook_event_id": body.event_id,
                "event_type": body.event_type,
                "provider_payload": body.payload,
            },
        )
        receipt.evidence_id = ev_row.id
        await record_conductor_audit(
            session,
            tenant_id=tenant_id,
            actor_user_id=ctx.user_id,
            event="conductor.webhook.received",
            session_id=proposal.session_id,
            proposal_id=proposal.id,
            agent_id=proposal.agent_id,
            correlation_id=proposal.correlation_id,
            detail={
                "webhook_event_id": body.event_id,
                "event_type": body.event_type,
                "evidence_id": str(ev_row.id),
            },
        )
        await session.commit()
        return {
            "accepted": True,
            "idempotent_replay": False,
            "proposal_id": str(proposal.id),
            "evidence_id": str(ev_row.id),
        }
    except AppError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=getattr(exc, "status_code", 400),
            detail={"code": str(exc.code), "message": exc.message},
        ) from exc
    except IntegrityError as exc:
        await session.rollback()
        existing = await session.scalar(
            select(ConductorWebhookReceipt).where(
                ConductorWebhookReceipt.tenant_id == tenant_id,
                ConductorWebhookReceipt.event_id == body.event_id,
            )
        )
        if existing is not None:
            return _replay_response(existing, body.proposal_id)
        raise HTTPException(
            status_code=409,
            detail={
                "code": "webhook_persistence_conflict",
                "message": "The event could not be persisted consistently",
            },
        ) from exc
