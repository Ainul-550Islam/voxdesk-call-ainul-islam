"""A2P 10DLC / toll-free registration API (PART 13, P13-08).

The wizard is four calls, in order: create a draft, submit the brand, submit the
campaign, read the status. The server -- not the UI -- enforces the order, so a
script that skips a step cannot register a campaign against an unapproved brand.

Status changes arrive from the carrier on `POST /api/messaging/webhooks/
registration`. That endpoint is idempotent and forward-only: a retried or
out-of-order event is recorded as ignored, never applied.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.config import settings
from app.db.session import get_session
from app.db.telephony_models import MessagingRegistration
from app.telephony import messaging_registration as logic

router = APIRouter(tags=["messaging-registration"])


class DraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    payload: dict = Field(default_factory=dict)
    carrier_reference: str | None = None


class WebhookEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    carrier_reference: str | None = None
    registration_id: str | None = None
    status: str
    rejection_reason: str | None = None


def _serialize(row: MessagingRegistration) -> dict:
    return {
        "id": str(row.id),
        "kind": row.kind,
        "state": row.state,
        "carrier_reference": row.carrier_reference,
        "payload": dict(row.payload or {}),
        "rejection_reason": row.rejection_reason,
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "next_step": logic.next_step(
            row.kind, row.state, {row.kind: row.state}
        ),
    }


async def _rows(session: AsyncSession, tenant_id: uuid.UUID) -> list[MessagingRegistration]:
    return list(
        (
            await session.execute(
                select(MessagingRegistration)
                .where(MessagingRegistration.tenant_id == tenant_id)
                .order_by(MessagingRegistration.created_at)
            )
        )
        .scalars()
        .all()
    )


async def _states(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, str]:
    return {row.kind: row.state for row in await _rows(session, tenant_id)}


@router.get("/api/messaging/registrations")
async def list_registrations(
    ctx: TenantContext = Depends(require_permission(Permission.MESSAGING_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = await _rows(session, ctx.tenant_id)
    summary = logic.summarise(rows)
    sms_allowed, sms_reason = logic.sms_send_permitted(summary["states"])
    return {
        "registrations": [_serialize(row) for row in rows],
        "summary": summary,
        "sms": {"allowed": sms_allowed, "reason": sms_reason},
        "kinds": list(logic.KINDS),
        "states": list(logic.STATES),
    }


@router.post("/api/messaging/registrations", status_code=status.HTTP_201_CREATED)
async def create_draft(
    payload: DraftCreate,
    ctx: TenantContext = Depends(require_permission(Permission.MESSAGING_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.kind not in logic.KINDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"kind must be one of {', '.join(logic.KINDS)}",
        )
    existing = [row for row in await _rows(session, ctx.tenant_id) if row.kind == payload.kind]
    if any(row.state not in logic.TERMINAL for row in existing):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"a {payload.kind} registration is already open",
        )
    row = MessagingRegistration(
        tenant_id=ctx.tenant_id,
        kind=payload.kind,
        state=logic.DRAFT,
        carrier_reference=payload.carrier_reference,
        payload=dict(payload.payload),
    )
    session.add(row)
    await session.commit()
    return _serialize(row)


@router.post("/api/messaging/registrations/{registration_id}/submit")
async def submit_registration(
    registration_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.MESSAGING_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    row = (
        await session.execute(
            select(MessagingRegistration).where(
                MessagingRegistration.id == registration_id,
                MessagingRegistration.tenant_id == ctx.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="registration not found")

    states = await _states(session, ctx.tenant_id)
    plan = logic.plan_submission(row.kind, states, row.payload or {})
    if not plan.allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"blockers": plan.blockers, "next_step": plan.next_step},
        )
    transition = logic.apply_carrier_status(row.state, logic.SUBMITTED)
    if not transition.applied:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=transition.reason or "this registration cannot be submitted",
        )
    row.state = transition.state
    row.submitted_at = datetime.now(timezone.utc)
    await session.commit()
    return _serialize(row)


@router.get("/api/messaging/status")
async def messaging_status(
    ctx: TenantContext = Depends(require_permission(Permission.MESSAGING_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    states = await _states(session, ctx.tenant_id)
    allowed, reason = logic.sms_send_permitted(states)
    toll_free_allowed, toll_free_reason = logic.sms_send_permitted(states, toll_free=True)
    return {
        "states": states,
        "sms": {"allowed": allowed, "reason": reason},
        "toll_free_sms": {"allowed": toll_free_allowed, "reason": toll_free_reason},
        "next_steps": {
            kind: logic.next_step(kind, states.get(kind, logic.DRAFT), states)
            for kind in logic.KINDS
        },
    }


_SIGNATURE_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _verify_carrier_signature(raw_body: bytes, signature_header: str | None) -> None:
    """Refuse a carrier callback that is not signed with the carrier secret.

    This endpoint mutates registration state, so an unsigned POST is an
    unauthenticated write. There is no "skip verification in development" flag:
    with no secret configured the callback is refused (503) rather than trusted,
    because a permissive default is the bug that ships.
    """
    secret = settings.telephony_webhook_secret.strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "webhook_signature_not_configured",
                "message": "carrier webhook signature verification is unavailable",
            },
        )
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "webhook_signature_missing",
                "message": "missing X-VoxDesk-Signature header",
            },
        )
    provided = signature_header.strip()
    if provided.startswith("sha256="):
        provided = provided[len("sha256=") :]
    if not _SIGNATURE_RE.fullmatch(provided):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "webhook_signature_invalid",
                "message": "invalid X-VoxDesk-Signature header",
            },
        )
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided.lower()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "webhook_signature_invalid",
                "message": "invalid X-VoxDesk-Signature header",
            },
        )


@router.post("/api/messaging/webhooks/registration")
async def registration_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Carrier status callback.

    No tenant context: the carrier is not logged in. The callback is
    HMAC-SHA256 signed over the raw body (`X-VoxDesk-Signature`, secret
    `TELEPHONY_WEBHOOK_SECRET`), and the registration is found by its carrier
    reference (or id) so the tenant is derived from the row -- never from the
    request body.
    """
    raw_body = await request.body()
    _verify_carrier_signature(raw_body, request.headers.get("X-VoxDesk-Signature"))
    try:
        data = json.loads(raw_body or b"{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"body is not JSON: {exc}",
        ) from exc
    try:
        event = WebhookEvent(**data)
    except Exception as exc:  # pydantic's message is the useful one
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    stmt = select(MessagingRegistration)
    if event.registration_id:
        try:
            stmt = stmt.where(MessagingRegistration.id == uuid.UUID(event.registration_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="registration_id is not a uuid"
            ) from exc
    elif event.carrier_reference:
        stmt = stmt.where(MessagingRegistration.carrier_reference == event.carrier_reference)
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="carrier_reference or registration_id is required",
        )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        # Acknowledge unknown references so the carrier stops retrying a row we
        # will never have; the miss is visible in the response body.
        return {"applied": False, "reason": "unknown registration"}

    try:
        transition = logic.apply_carrier_status(row.state, event.status)
    except logic.RegistrationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    if transition.applied:
        row.state = transition.state
        if row.state in logic.TERMINAL:
            row.decided_at = datetime.now(timezone.utc)
        if row.state == logic.REJECTED:
            row.rejection_reason = (event.rejection_reason or "rejected by carrier")[:400]
    await session.commit()
    return {"applied": transition.applied, "state": row.state, "reason": transition.reason}
