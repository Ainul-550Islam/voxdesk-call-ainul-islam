"""Organization audit events.

Identifiers and status only. Names, slugs and anything credential-shaped stay
out of the detail payload; ``emit`` scrubs the rest.
"""

from __future__ import annotations

import uuid

from app.auth.identity.events import emit
from app.db.models import AuditAction

_ACTIONS = {
    "created": AuditAction.ORGANIZATION_CREATED,
    "updated": AuditAction.ORGANIZATION_UPDATED,
    "suspended": AuditAction.ORGANIZATION_SUSPENDED,
    "restored": AuditAction.ORGANIZATION_RESTORED,
    "read_only": AuditAction.ORGANIZATION_READ_ONLY,
}


async def organization_event(
    session,
    *,
    kind: str,
    organization_id: uuid.UUID,
    status: str,
    tenant_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_email: str = "",
    ip_address: str = "",
    user_agent: str = "",
    commit: bool = False,
) -> None:
    action = _ACTIONS[kind]
    await emit(
        session,
        action,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        ip_address=ip_address,
        user_agent=user_agent,
        detail={
            "organization_id": str(organization_id),
            "status": status,
            "event": f"organization.{kind}",
        },
        commit=commit,
    )
