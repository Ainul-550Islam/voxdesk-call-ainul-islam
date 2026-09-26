"""Authorized replay of a dead-lettered telephony callback.

A completed replay is ``replayed``. A failure that is only queued again is
``accepted_for_retry``. Those outcomes are not interchangeable. Replay does
not change tenant, and it does not run the effect twice for the same event
id already processed.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import emit
from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.models import AuditAction, UserRole
from app.telephony.call_events import get_owned
from app.telephony.callback_dlq import note_failure
from app.telephony.callback_reconciliation import _apply
from app.tenancy.isolation import Forbidden, LifecycleDenied

_MAX_REPLAY = 3


def operator_may_replay(role: UserRole | None) -> bool:
    return role is not None and has_permission(role, Permission.SECURITY_SETTINGS)


async def replay(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    event_id: uuid.UUID,
    role: UserRole | None,
    actor_user_id: uuid.UUID | None = None,
) -> dict:
    if not operator_may_replay(role):
        raise Forbidden()
    event = await get_owned(session, tenant_id, event_id)
    if event.status != "dead_letter":
        raise LifecycleDenied("Only a dead-letter event can be replayed")
    if event.replay_count >= _MAX_REPLAY:
        raise LifecycleDenied("Replay budget is exhausted")
    event.replay_count += 1
    envelope = {
        "provider": event.provider,
        "event_type": event.event_type,
        "external_id": event.external_id,
        "event_id": event.event_id,
        "tenant_id": event.tenant_id,
        "observed_at": event.provider_observed_at,
        "metadata": dict(event.safe_metadata or {}),
        **dict(event.safe_metadata or {}),
    }
    try:
        outcome, changed = await _apply(session, event, envelope)
    except Exception as exc:
        queued = await note_failure(session, event, exc)
        await _audit(session, tenant_id, actor_user_id, event, "accepted_for_retry")
        return {
            "outcome": "accepted_for_retry",
            "status": queued["status"],
            "replay_count": event.replay_count,
            "side_effect": False,
        }
    event.status = "replayed"
    event.outcome = outcome[:64]
    from datetime import datetime, timezone

    event.processed_at = datetime.now(timezone.utc)
    await session.flush()
    await _audit(session, tenant_id, actor_user_id, event, "replayed")
    return {
        "outcome": "replayed",
        "status": event.status,
        "replay_count": event.replay_count,
        "side_effect": changed,
        "detail": outcome,
    }


async def _audit(session, tenant_id, actor_user_id, event, operation: str) -> None:
    await emit(
        session,
        AuditAction.INTEGRATION_UPDATED,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        detail={
            "operation": operation,
            "event_id": str(event.id),
            "provider": event.provider,
            "event_type": event.event_type,
            "replay_count": event.replay_count,
        },
        commit=False,
    )
