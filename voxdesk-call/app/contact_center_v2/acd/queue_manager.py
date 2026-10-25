"""Batch 04 ACD policy extensions built on the canonical contact-center router."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center import service as acd_service
from app.contact_center.models import (
    AgentPresence,
    Queue,
    QueueEntry,
    RoutingAssignment,
    RoutingDecision,
)
from app.contact_center.queues import update_queue
from app.contact_center_v2.assist import assist_counts
from app.contact_center_v2.acd.callbacks import schedule_callback
from app.contact_center_v2.errors import Conflict, Validation
from app.contact_center_v2.repository import as_utc, owned, utcnow
from app.db.contact_center_v2_models import (
    AcdQueuePolicy,
    CallbackRequest,
    QueuePriorityConfig,
    SoftphoneSession,
)
from app.db.models import Call, Tenant, User
from app.telephony import phone


async def upsert_policy(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_id: uuid.UUID,
    payload: dict,
) -> tuple[Queue, AcdQueuePolicy]:
    queue = await owned(session, Queue, queue_id, tenant_id, for_update=True)
    overflow_queue_id = payload.get("overflow_queue_id")
    if overflow_queue_id is not None:
        target = await owned(session, Queue, overflow_queue_id, tenant_id)
        if target.id == queue.id or target.environment_id != queue.environment_id:
            raise Validation("Overflow queue must be another queue in the same environment")
    escalation = str(payload.get("escalation_number") or "")
    if payload.get("overflow_kind") == "escalate":
        escalation = phone.normalize(escalation)
    minimum_proficiency = (queue.overflow_policy or {}).get("cc2_min_proficiency")
    await update_queue(
        session,
        queue,
        {
            "strategy": payload["strategy"],
            "required_skills": payload.get("required_skills", []),
            "priority": payload.get("priority", 0),
            "max_concurrency": payload.get("max_concurrency", 10),
            # Batch 04's sweep owns all extended overflow actions. Keeping the
            # legacy immediate action at none prevents two callback/transfer
            # implementations from racing for the same queue entry.
            "overflow_policy": {"kind": "none"},
        },
    )
    row = (
        await session.execute(
            select(AcdQueuePolicy).where(
                AcdQueuePolicy.tenant_id == tenant_id,
                AcdQueuePolicy.queue_id == queue.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = AcdQueuePolicy(
            tenant_id=tenant_id,
            environment_id=queue.environment_id,
            queue_id=queue.id,
        )
        session.add(row)
    row.overflow_kind = payload.get("overflow_kind", "none")
    row.overflow_after_seconds = payload.get("overflow_after_seconds", 0)
    row.overflow_waiting = payload.get("overflow_waiting", 0)
    row.overflow_queue_id = overflow_queue_id
    row.callback_enabled = bool(payload.get("callback_enabled", False))
    row.updated_at = utcnow()
    # The number remains in the canonical queue policy for existing operators,
    # but kind=none ensures only this extension executes it.
    queue.overflow_policy = {
        "kind": "none",
        "max_wait_seconds": row.overflow_after_seconds,
        "max_waiting": row.overflow_waiting,
        "escalation_number": escalation,
    }
    if minimum_proficiency is not None:
        queue.overflow_policy["cc2_min_proficiency"] = minimum_proficiency
    await session.flush()
    from app.contact_center_v2.acd.priority import configure_priority

    await configure_priority(
        session,
        tenant_id=tenant_id,
        queue_id=queue.id,
        priority=payload.get("priority", queue.priority),
        strategy=payload["strategy"],
        max_wait_seconds=payload.get("max_wait_seconds", row.overflow_after_seconds or 300),
        fallback_kind=payload.get("fallback_kind", "callback"),
        overflow_chain=payload.get("overflow_chain"),
        ewt_config=payload.get("ewt_config"),
    )
    return queue, row


def policy_payload(queue: Queue, row: AcdQueuePolicy | None) -> dict:
    result = queue.as_dict()
    result["acd_v2"] = (
        None
        if row is None
        else {
            "overflow_kind": row.overflow_kind,
            "overflow_after_seconds": row.overflow_after_seconds,
            "overflow_waiting": row.overflow_waiting,
            "overflow_queue_id": str(row.overflow_queue_id) if row.overflow_queue_id else None,
            "callback_enabled": row.callback_enabled,
            "last_overflow_sweep_at": (
                row.last_overflow_sweep_at.isoformat() if row.last_overflow_sweep_at else None
            ),
        }
    )
    return result


async def route_queue(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_id: uuid.UUID,
    actor_id: uuid.UUID,
    call_id: uuid.UUID | None = None,
    priority: int = 0,
):
    from app.contact_center_v2.acd.skills import synchronize_queue_skills

    await synchronize_queue_skills(session, tenant_id=tenant_id, queue_id=queue_id)
    await sweep_overflow(session, tenant_id=tenant_id, queue_id=queue_id)
    if call_id is None:
        result = await acd_service.assign_next(
            session,
            tenant_id=tenant_id,
            queue_id=queue_id,
            actor_id=actor_id,
            require_softphone=True,
        )
    else:
        result = await acd_service.enqueue(
            session,
            tenant_id=tenant_id,
            queue_id=queue_id,
            call_id=call_id,
            priority=priority,
            actor_id=actor_id,
            require_softphone=True,
        )
    softphone_call = None
    if result.outcome == "assigned" and result.assignment is not None:
        from app.contact_center_v2.softphone import prepare_acd_call

        softphone_call = await prepare_acd_call(
            session,
            entry_id=result.entry.id,
            agent_user_id=result.assignment.user_id,
        )
    return result, softphone_call


async def _close_overflow_entry(
    session: AsyncSession, entry: QueueEntry, *, outcome: str, status: str = "overflowed"
) -> None:
    result = await session.execute(
        update(QueueEntry)
        .where(
            QueueEntry.id == entry.id,
            QueueEntry.tenant_id == entry.tenant_id,
            QueueEntry.status == "waiting",
            QueueEntry.version == entry.version,
        )
        .values(
            status=status,
            outcome=outcome,
            active_lock=None,
            version=entry.version + 1,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise Conflict("Queue entry changed during overflow")
    await session.refresh(entry)


async def _overflow_decision(
    session: AsyncSession, entry: QueueEntry, *, outcome: str, applied: bool
) -> None:
    session.add(
        RoutingDecision(
            tenant_id=entry.tenant_id,
            environment_id=entry.environment_id,
            entry_id=entry.id,
            queue_id=entry.queue_id,
            strategy="cc2_overflow",
            matched_skills=[],
            rejected=[],
            applied=applied,
            outcome=outcome,
        )
    )
    await session.flush()


async def _apply_overflow(
    session: AsyncSession,
    *,
    policy: AcdQueuePolicy,
    entry: QueueEntry,
    now: datetime,
    action: dict | None = None,
) -> str:
    action = action or {"kind": policy.overflow_kind}
    kind = str(action["kind"])
    if kind == "none":
        return "waiting"
    if kind == "callback":
        if not policy.callback_enabled:
            raise Conflict("Callback overflow requires callback_enabled=true")
        await schedule_callback(
            session,
            tenant_id=entry.tenant_id,
            queue_entry_id=entry.id,
            requested_by=None,
            scheduled_at=now,
            timezone_name="UTC",
            note="Automatic ACD overflow callback",
        )
        await _close_overflow_entry(session, entry, outcome="callback_requested")
        await _overflow_decision(session, entry, outcome="callback_requested", applied=True)
        return "callback_requested"
    if kind == "queue":
        target_id = action.get("queue_id") or action.get("overflow_queue_id") or policy.overflow_queue_id
        if not target_id:
            raise Conflict("Queue overflow requires an explicit target queue")
        target = await session.get(Queue, uuid.UUID(str(target_id)))
        if (
            target is None
            or target.tenant_id != entry.tenant_id
            or target.environment_id != entry.environment_id
            or not target.enabled
        ):
            raise Conflict("The configured overflow queue is unavailable")
        await _close_overflow_entry(session, entry, outcome="queue_overflow")
        moved = QueueEntry(
            tenant_id=entry.tenant_id,
            environment_id=entry.environment_id,
            queue_id=target.id,
            call_id=entry.call_id,
            status="waiting",
            priority=entry.priority,
            enqueued_at=now,
            active_lock=str(entry.call_id),
            outcome="queue_overflow",
        )
        session.add(moved)
        await session.flush()
        await _overflow_decision(session, entry, outcome="queue_overflow", applied=True)
        return "queue_overflow"
    if kind == "escalate":
        from app.telephony.transfer_service import request_transfer

        tenant = await session.get(Tenant, entry.tenant_id)
        call = await session.get(Call, entry.call_id)
        queue = await session.get(Queue, entry.queue_id)
        escalation = str(action.get("escalation_number") or "") or (
            str((queue.overflow_policy or {}).get("escalation_number") or "")
            if queue is not None and queue.tenant_id == entry.tenant_id
            else ""
        )
        if (
            tenant is None
            or call is None
            or call.tenant_id != entry.tenant_id
            or queue is None
            or not escalation
        ):
            await _close_overflow_entry(session, entry, outcome="escalate_unavailable", status="failed")
            await _overflow_decision(session, entry, outcome="escalate_unavailable", applied=False)
            return "escalate_unavailable"
        result = await request_transfer(
            session,
            tenant,
            call,
            reason="acd v2 overflow",
            destination_override=escalation,
        )
        outcome = "escalate_accepted" if result.ok else "escalate_failed"
        await _close_overflow_entry(
            session, entry, outcome=outcome, status="overflowed" if result.ok else "failed"
        )
        await _overflow_decision(session, entry, outcome=outcome, applied=result.ok)
        return outcome
    if kind == "ai_agent":
        from app.telephony.provider import get_provider
        from app.telephony.takeover import build_ai_rollback_twiml

        call = await session.get(Call, entry.call_id)
        if call is None or call.tenant_id != entry.tenant_id or not call.call_sid:
            await _close_overflow_entry(session, entry, outcome="ai_agent_unavailable", status="failed")
            await _overflow_decision(session, entry, outcome="ai_agent_unavailable", applied=False)
            return "ai_agent_unavailable"
        await _close_overflow_entry(session, entry, outcome="ai_agent_requested")
        await _overflow_decision(session, entry, outcome="ai_agent_requested", applied=False)
        await session.commit()
        from app.contact_center_v2.softphone import run_provider

        result = await run_provider(
            get_provider().redirect_call,
            call.call_sid,
            build_ai_rollback_twiml(call.call_sid, call_id=str(call.id)),
        )
        ambiguous = not result.ok and result.error_code == "provider_error"
        entry.status = "failed" if not result.ok and not ambiguous else "overflowed"
        entry.outcome = (
            "ai_agent_redirected" if result.ok
            else "ai_agent_outcome_unknown" if ambiguous
            else "ai_agent_failed"
        )
        entry.version += 1
        await _overflow_decision(session, entry, outcome=entry.outcome, applied=result.ok)
        await session.commit()
        return entry.outcome
    if kind == "voicemail":
        from app.telephony.provider import get_provider
        from app.telephony.transfer import voicemail_twiml

        call = await session.get(Call, entry.call_id)
        if call is None or call.tenant_id != entry.tenant_id:
            await _close_overflow_entry(session, entry, outcome="voicemail_unavailable", status="failed")
            await _overflow_decision(session, entry, outcome="voicemail_unavailable", applied=False)
            return "voicemail_unavailable"
        # Persist the request before redirecting. If the provider outcome is
        # ambiguous, the entry no longer qualifies for another automatic sweep.
        await _close_overflow_entry(session, entry, outcome="voicemail_requested")
        await _overflow_decision(session, entry, outcome="voicemail_requested", applied=False)
        await session.commit()
        from app.contact_center_v2.softphone import run_provider

        result = await run_provider(
            get_provider().redirect_call,
            call.call_sid,
            voicemail_twiml(
                "Please leave a message after the tone. This voicemail will be recorded."
            ),
        )
        ambiguous = not result.ok and result.error_code == "provider_error"
        entry.status = "failed" if not result.ok and not ambiguous else "overflowed"
        entry.outcome = (
            "voicemail_redirected"
            if result.ok
            else "voicemail_outcome_unknown"
            if ambiguous
            else "voicemail_failed"
        )
        entry.version += 1
        await _overflow_decision(session, entry, outcome=entry.outcome, applied=result.ok)
        await session.commit()
        return entry.outcome
    raise Validation("Unknown overflow action")


async def sweep_overflow(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> dict[str, int]:
    moment = now or utcnow()
    stmt = select(AcdQueuePolicy).where(AcdQueuePolicy.tenant_id == tenant_id)
    if queue_id is not None:
        stmt = stmt.where(AcdQueuePolicy.queue_id == queue_id)
    policies = (await session.execute(stmt)).scalars().all()
    result = {"examined": 0, "overflowed": 0, "waiting": 0}
    for policy in policies:
        entries = (
            await session.execute(
                select(QueueEntry)
                .where(
                    QueueEntry.tenant_id == tenant_id,
                    QueueEntry.queue_id == policy.queue_id,
                    QueueEntry.status == "waiting",
                )
                .order_by(QueueEntry.enqueued_at, QueueEntry.id)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        depth = len(entries)
        priority = (
            await session.execute(
                select(QueuePriorityConfig).where(
                    QueuePriorityConfig.tenant_id == tenant_id,
                    QueuePriorityConfig.queue_id == policy.queue_id,
                )
            )
        ).scalar_one_or_none()
        from app.contact_center_v2.acd.router import select_overflow_action

        for entry in entries:
            result["examined"] += 1
            action = select_overflow_action(
                elapsed_seconds=max(0.0, (moment - as_utc(entry.enqueued_at)).total_seconds()),
                depth=depth,
                default_kind=policy.overflow_kind,
                default_after_seconds=policy.overflow_after_seconds,
                default_waiting=policy.overflow_waiting,
                max_wait_seconds=priority.max_wait_seconds if priority else 300,
                overflow_chain=list(priority.overflow_chain or []) if priority else [],
                fallback_kind=priority.fallback_kind if priority else "callback",
            )
            if action is None:
                result["waiting"] += 1
                continue
            outcome = await _apply_overflow(
                session, policy=policy, entry=entry, now=moment, action=action,
            )
            if outcome == "waiting":
                result["waiting"] += 1
            else:
                result["overflowed"] += 1
        policy.last_overflow_sweep_at = moment
    await session.flush()
    return result


async def wallboard(session: AsyncSession, *, tenant_id: uuid.UUID) -> dict:
    now = utcnow()
    queues = (
        await session.execute(select(Queue).where(Queue.tenant_id == tenant_id).order_by(Queue.name))
    ).scalars().all()
    entries = (
        await session.execute(
            select(QueueEntry).where(
                QueueEntry.tenant_id == tenant_id,
                QueueEntry.status.in_(("waiting", "assigned")),
            )
        )
    ).scalars().all()
    assignments = (
        await session.execute(
            select(RoutingAssignment).where(
                RoutingAssignment.tenant_id == tenant_id,
                RoutingAssignment.status == "active",
            )
        )
    ).scalars().all()
    by_entry = {row.entry_id: row for row in assignments}
    queue_items = []
    for queue in queues:
        owned_entries = [row for row in entries if row.queue_id == queue.id]
        waiting = [row for row in owned_entries if row.status == "waiting"]
        assigned = [row for row in owned_entries if row.status == "assigned"]
        oldest = min((as_utc(row.enqueued_at) for row in waiting), default=None)
        queue_items.append(
            {
                "queue_id": str(queue.id),
                "name": queue.name,
                "enabled": queue.enabled,
                "waiting": len(waiting),
                "assigned": len(assigned),
                "oldest_wait_seconds": max(0, int((now - oldest).total_seconds())) if oldest else 0,
                "max_concurrency": queue.max_concurrency,
                "assignments": [
                    {
                        "entry_id": str(row.id),
                        "call_id": str(row.call_id),
                        "agent_user_id": str(by_entry[row.id].user_id),
                    }
                    for row in assigned
                    if row.id in by_entry
                ],
            }
        )
    presence = (
        await session.execute(
            select(AgentPresence, User)
            .join(User, User.id == AgentPresence.user_id)
            .where(AgentPresence.tenant_id == tenant_id, User.tenant_id == tenant_id)
            .order_by(User.full_name, User.email)
        )
    ).all()
    active_sessions = set(
        (
            await session.execute(
                select(SoftphoneSession.user_id).where(
                    SoftphoneSession.tenant_id == tenant_id,
                    SoftphoneSession.status == "active",
                    SoftphoneSession.expires_at > now,
                )
            )
        ).scalars().all()
    )
    agents = [
        {
            **row.as_dict(),
            "full_name": user.full_name,
            "email": user.email,
            "softphone_registered": row.user_id in active_sessions,
        }
        for row, user in presence
    ]
    callback_counts = {
        status: int(
            await session.scalar(
                select(func.count())
                .select_from(CallbackRequest)
                .where(CallbackRequest.tenant_id == tenant_id, CallbackRequest.status == status)
            )
            or 0
        )
        for status in ("scheduled", "running", "completed", "failed", "cancelled")
    }
    return {
        "generated_at": now.isoformat(),
        "queues": queue_items,
        "agents": agents,
        "callbacks": callback_counts,
        "assist": await assist_counts(session, tenant_id=tenant_id),
    }


__all__ = [
    "policy_payload",
    "route_queue",
    "sweep_overflow",
    "upsert_policy",
    "wallboard",
]
