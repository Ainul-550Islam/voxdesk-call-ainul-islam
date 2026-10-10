"""Transactional writer for the existing tenant-scoped audit ledger."""
from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.events import AuditEventType, normalize_event_type, storage_action
from app.audit.redaction import configured_secret_values, redact_tree, redact_text
from app.db.models import AuditAction, AuditLog, Environment, User


class AuditScopeError(ValueError):
    """Raised when an audit event attempts to cross its declared tenant scope."""


def _clean_ip(value: str | None) -> str:
    if not value:
        return ""
    candidate = value.strip()
    try:
        return str(ipaddress.ip_address(candidate))[:64]
    except ValueError:
        return ""


def _uuid_or_none(value: uuid.UUID | str | None, field: str) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def _resource_id(value: uuid.UUID | str | None, known_secrets) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not text or len(text) > 160 or any(ord(character) < 32 for character in text):
        raise ValueError("resource_id must be a printable identifier of at most 160 characters")
    return redact_text(text, known_secrets=known_secrets, redact_pii=True)[:160]


async def record_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str | None,
    event_type: str,
    actor_user_id: uuid.UUID | str | None = None,
    actor_type: str | None = None,
    actor_email: str | None = None,
    target_user_id: uuid.UUID | str | None = None,
    environment_id: uuid.UUID | str | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | str | None = None,
    request_id: str | None = None,
    result: str = "success",
    ip_address: str | None = None,
    user_agent: str | None = None,
    detail: Any = None,
    known_secrets: tuple[str, ...] | list[str] = (),
    redact_pii: bool = True,
    occurred_at: datetime | None = None,
) -> AuditLog:
    """Stage one bounded, scrubbed audit record in the caller's transaction.

    No commit is performed here: callers should commit the business mutation
    and its corresponding event atomically. ``tenant_id`` is always explicit;
    environment and user references are checked against that tenant before the
    event is staged. A system event without a tenant may not claim an
    environment, actor or tenant-owned target.
    """
    tenant_uuid = _uuid_or_none(tenant_id, "tenant_id")
    actor_uuid = _uuid_or_none(actor_user_id, "actor_user_id")
    target_uuid = _uuid_or_none(target_user_id, "target_user_id")
    environment_uuid = _uuid_or_none(environment_id, "environment_id")
    if tenant_uuid is None and any((actor_uuid, target_uuid, environment_uuid)):
        raise AuditScopeError("tenant-owned audit references require a tenant_id")

    if tenant_uuid is not None:
        if environment_uuid is not None:
            environment_exists = await session.scalar(
                select(Environment.id).where(
                    Environment.id == environment_uuid,
                    Environment.tenant_id == tenant_uuid,
                )
            )
            if environment_exists is None:
                raise AuditScopeError("environment_id does not belong to tenant_id")
        if actor_uuid is not None:
            actor_exists = await session.scalar(
                select(User.id).where(
                    User.id == actor_uuid,
                    User.tenant_id == tenant_uuid,
                )
            )
            if actor_exists is None:
                raise AuditScopeError("actor_user_id does not belong to tenant_id")
        if target_uuid is not None:
            target_exists = await session.scalar(
                select(User.id).where(
                    User.id == target_uuid,
                    User.tenant_id == tenant_uuid,
                )
            )
            if target_exists is None:
                raise AuditScopeError("target_user_id does not belong to tenant_id")

    normalized_type = normalize_event_type(event_type)
    if result not in {"success", "failure", "denied", "pending", "error"}:
        raise ValueError("result must be a recognized audit outcome")
    normalized_actor_type = (actor_type or ("human" if actor_uuid else "system")).strip().lower()
    if normalized_actor_type not in {"human", "api_key", "service_account", "scim", "provider", "worker", "system"}:
        raise ValueError("actor_type must be a recognized principal type")
    if resource_type is not None and len(resource_type) > 64:
        raise ValueError("resource_type is too long")
    if request_id is not None and len(request_id) > 64:
        raise ValueError("request_id is too long")

    secrets = (*configured_secret_values(), *known_secrets)
    safe_resource_id = _resource_id(resource_id, secrets)
    safe_detail = redact_tree(
        detail if detail is not None else {},
        known_secrets=secrets,
        redact_pii=redact_pii,
    )
    if not isinstance(safe_detail, dict):
        safe_detail = {"value": safe_detail}

    when = occurred_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    row = AuditLog(
        tenant_id=tenant_uuid,
        actor_user_id=actor_uuid,
        actor_type=normalized_actor_type,
        target_user_id=target_uuid,
        action=storage_action(normalized_type),
        event_type=normalized_type,
        environment_id=environment_uuid,
        resource_type=resource_type.strip().lower() if resource_type else None,
        resource_id=safe_resource_id,
        request_id=request_id,
        result=result,
        actor_email=redact_text(
            (actor_email or "").strip().lower()[:320],
            known_secrets=secrets,
            redact_pii=False,
        ),
        ip_address=_clean_ip(ip_address),
        user_agent=redact_text(
            (user_agent or "")[:300], known_secrets=secrets, redact_pii=True
        ),
        detail=safe_detail,
        created_at=when,
    )
    session.add(row)
    return row


async def record_enterprise_audit(
    db: AsyncSession,
    tenant_id: uuid.UUID | str,
    user_id: uuid.UUID | str | None,
    action: str | AuditAction | AuditEventType,
    detail: dict[str, Any] | None = None,
    *,
    environment_id: uuid.UUID | str | None = None,
    resource_type: str | None = None,
    resource_id: Any = None,
) -> AuditLog:
    """Insert an ``AuditLog`` row in the caller's transaction and flush immediately.

    Never swallows exceptions: if the audit insert fails, the caller's
    transaction rolls back and the request fails (500).
    """
    row = await record_event(
        db,
        tenant_id=tenant_id,
        actor_user_id=user_id,
        event_type=action,
        environment_id=environment_id,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail or {},
    )
    await db.flush()
    return row

