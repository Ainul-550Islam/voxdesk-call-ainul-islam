"""Tenant-scoped browser/desk-phone device registry.

Only a SHA-256 fingerprint is persisted. A device row is not evidence of an
active WebRTC session; registration and signed provider events remain separate.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import Conflict, NotFound, PermissionDenied, Validation
from app.contact_center_v2.repository import utcnow
from app.db.contact_center_v2_models import AgentDevice, SoftphoneSession
from app.db.models import Environment, User


def fingerprint_digest(fingerprint: str) -> str:
    value = fingerprint.strip()
    if len(value) < 8 or len(value) > 256:
        raise Validation("Device fingerprint must contain 8–256 characters")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def register_device(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    user_id: uuid.UUID,
    label: str,
    fingerprint: str,
    kind: str = "browser",
    now: datetime | None = None,
) -> AgentDevice:
    if kind not in {"browser", "desk_phone"}:
        raise Validation("Unknown softphone device kind")
    clean_label = label.strip()
    if not clean_label or len(clean_label) > 100:
        raise Validation("Device label must contain 1–100 characters")
    user = await session.get(User, user_id)
    environment = await session.get(Environment, environment_id)
    if user is None or user.tenant_id != tenant_id or not user.is_active:
        raise NotFound("agent")
    if environment is None or environment.tenant_id != tenant_id or environment.status != "active":
        raise NotFound("environment")
    digest = fingerprint_digest(fingerprint)
    row = (
        await session.execute(
            select(AgentDevice).where(
                AgentDevice.tenant_id == tenant_id,
                AgentDevice.user_id == user_id,
                AgentDevice.fingerprint_hash == digest,
            )
        )
    ).scalar_one_or_none()
    moment = now or utcnow()
    if row is None:
        row = AgentDevice(
            tenant_id=tenant_id,
            environment_id=environment_id,
            user_id=user_id,
            kind=kind,
            label=clean_label,
            fingerprint_hash=digest,
            status="active",
            created_at=moment,
        )
        session.add(row)
    else:
        row.environment_id = environment_id
        row.kind = kind
        row.label = clean_label
        row.status = "active"
        row.revoked_at = None
    row.last_heartbeat_at = moment
    await session.flush()
    return row


async def heartbeat_device(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    device_id: uuid.UUID,
    now: datetime | None = None,
) -> AgentDevice:
    row = await session.get(AgentDevice, device_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound("softphone device")
    if row.user_id != user_id:
        raise PermissionDenied("This device belongs to another agent")
    if row.status != "active":
        raise Conflict("Softphone device is revoked")
    row.last_heartbeat_at = now or utcnow()
    await session.flush()
    return row


async def revoke_device(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    device_id: uuid.UUID,
) -> AgentDevice:
    row = await session.get(AgentDevice, device_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound("softphone device")
    if row.user_id != user_id:
        raise PermissionDenied("This device belongs to another agent")
    active_call = (
        await session.execute(
            select(SoftphoneSession.id).where(
                SoftphoneSession.tenant_id == tenant_id,
                SoftphoneSession.device_id == row.id,
                SoftphoneSession.status == "active",
            )
        )
    ).first()
    if active_call is not None:
        raise Conflict("Revoke the active softphone session before revoking its device")
    row.status = "revoked"
    row.revoked_at = utcnow()
    await session.flush()
    return row


async def list_devices(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> list[AgentDevice]:
    return list(
        (
            await session.execute(
                select(AgentDevice).where(
                    AgentDevice.tenant_id == tenant_id,
                    AgentDevice.user_id == user_id,
                ).order_by(AgentDevice.created_at.desc())
            )
        )
        .scalars()
        .all()
    )


__all__ = [
    "fingerprint_digest",
    "heartbeat_device",
    "list_devices",
    "register_device",
    "revoke_device",
]
