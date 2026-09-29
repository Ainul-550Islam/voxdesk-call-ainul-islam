"""Bridge governance events into the application's existing audit pipeline."""

from __future__ import annotations

import uuid
from typing import Any

from app.auth.identity.events import emit, scrub
from app.db.models import AuditAction

from .context import GovernanceScope


async def record_governance_audit(
    session,
    scope: GovernanceScope,
    *,
    event: str,
    actor_user_id: uuid.UUID | None,
    detail: dict[str, Any] | None = None,
    commit: bool = False,
) -> None:
    """Use the existing credential-scrubbing ``AuditLog`` bridge.

    The detailed event name lives in the scrubbed detail payload because the
    existing AuditAction vocabulary is intentionally not replaced by a second
    audit table. The immutable governance evidence row is the durable event
    stream; this call keeps it visible to current audit readers and operators.
    """
    payload = scrub(
        {
            "event": event,
            "operation": event,
            "organization_id": str(scope.organization_id),
            "environment_id": str(scope.environment_id) if scope.environment_id else None,
            **(detail or {}),
        }
    )
    await emit(
        session,
        AuditAction.GOVERNANCE_EVENT,
        tenant_id=scope.tenant_id,
        actor_user_id=actor_user_id,
        actor_email="",
        detail=payload,
        commit=commit,
    )
