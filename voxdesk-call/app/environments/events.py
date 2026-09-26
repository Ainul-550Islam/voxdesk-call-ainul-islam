"""Environment audit events. Identifiers, kind and status only."""

from __future__ import annotations

import uuid

from app.auth.identity.events import emit
from app.db.models import AuditAction

_ACTIONS = {
    "created": AuditAction.ENVIRONMENT_CREATED,
    "updated": AuditAction.ENVIRONMENT_UPDATED,
    "suspended": AuditAction.ENVIRONMENT_SUSPENDED,
    "restored": AuditAction.ENVIRONMENT_RESTORED,
    "archived": AuditAction.ENVIRONMENT_ARCHIVED,
    "default_changed": AuditAction.ENVIRONMENT_DEFAULT_CHANGED,
}


async def environment_event(
    session,
    *,
    kind: str,
    environment_id: uuid.UUID,
    tenant_id: uuid.UUID,
    status: str,
    environment_kind: str,
    actor_user_id: uuid.UUID | None = None,
    actor_email: str = "",
    ip_address: str = "",
    user_agent: str = "",
    commit: bool = False,
) -> None:
    await emit(
        session,
        _ACTIONS[kind],
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        ip_address=ip_address,
        user_agent=user_agent,
        detail={
            "environment_id": str(environment_id),
            "tenant_id": str(tenant_id),
            "status": status,
            "kind": environment_kind,
            "event": f"environment.{kind}",
        },
        commit=commit,
    )
