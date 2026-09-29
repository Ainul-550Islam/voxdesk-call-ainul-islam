"""Durable human-in-the-loop approval gates."""

from __future__ import annotations
import hashlib
import json
import uuid
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import AuditAction, HumanApproval


class ApprovalError(RuntimeError):
    """Approval is missing, expired, already decided, or not tenant-owned."""


def digest_payload(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def request(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    action: str,
    subject_ref: str,
    payload: dict,
    requested_by: uuid.UUID | None,
    ttl_seconds: int = 900,
    environment_id=None,
) -> HumanApproval:
    from app.auth.identity.events import emit as audit_event

    if not action or not subject_ref or ttl_seconds < 1 or ttl_seconds > 86400:
        raise ApprovalError("approval request is invalid")
    row = HumanApproval(
        tenant_id=tenant_id,
        environment_id=environment_id,
        action=action[:120],
        subject_ref=subject_ref[:255],
        payload_digest=digest_payload(payload),
        requested_by=requested_by,
        expires_at=datetime.utcnow() + timedelta(seconds=ttl_seconds),
    )
    session.add(row)
    await session.flush()
    await audit_event(
        session,
        AuditAction.INTEGRATION_TESTED,
        tenant_id=tenant_id,
        actor_user_id=requested_by,
        detail={"approval_id": str(row.id), "action": action, "outcome": "requested"},
    )
    return row


async def decide(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    approval_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    approved: bool,
    payload: dict | None = None,
) -> HumanApproval:
    from app.auth.identity.events import emit as audit_event

    row = (
        await session.execute(
            select(HumanApproval).where(
                HumanApproval.id == approval_id, HumanApproval.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApprovalError("approval not found")
    if row.status != "pending":
        raise ApprovalError("approval has already been decided")
    if row.expires_at < datetime.utcnow():
        row.status = "expired"
        raise ApprovalError("approval expired")
    if payload is not None and digest_payload(payload) != row.payload_digest:
        raise ApprovalError("approval payload does not match")
    row.status, row.decided_by, row.decided_at = (
        ("approved" if approved else "rejected"),
        actor_user_id,
        datetime.utcnow(),
    )
    await audit_event(
        session,
        AuditAction.INTEGRATION_TESTED,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        detail={"approval_id": str(row.id), "outcome": row.status},
    )
    return row


async def require_approved(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    approval_id: uuid.UUID,
    payload: dict | None = None,
) -> HumanApproval:
    row = (
        await session.execute(
            select(HumanApproval).where(
                HumanApproval.id == approval_id, HumanApproval.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if row is None or row.status != "approved" or row.expires_at < datetime.utcnow():
        raise ApprovalError("a current approved HITL gate is required")
    if payload is not None and digest_payload(payload) != row.payload_digest:
        raise ApprovalError("approval payload does not match")
    return row
