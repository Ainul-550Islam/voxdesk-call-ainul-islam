"""ACD decisions.

This module chooses a queue item and an agent, then asks the inbox to record
the owner. It does not own call state, and it does not run on the media
stream. A failed reservation is rolled back. It is never reported as an
assignment.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center import callbacks, overflow, routing
from app.contact_center.agent_state import ensure, transition
from app.contact_center.exceptions import (
    AgentUnavailable,
    DuplicateAssignment,
    NoEligibleAgent,
    OverflowDenied,
    QueueUnavailable,
    StaleState,
)
from app.contact_center.models import (
    AgentPresence,
    Queue,
    QueueEntry,
    RoutingAssignment,
    RoutingDecision,
)
from app.contact_center.policies import (
    member_reason,
    presence_reason,
    skill_reason,
    skill_score,
)
from app.contact_center.repository import (
    active_assignment_for_entry,
    active_entry_for_call,
    active_for_user,
    add_decision,
    assigned_count,
    claim_waiting,
    cursor_for,
    get_assignment,
    get_entry,
    get_queue,
    insert_assignment,
    members,
    presence_for,
    reserve_agent,
    save_cursor,
    skills_for_tenant,
    waiting_count,
    waiting_entries,
)
from app.db.models import (
    AuditAction,
    Call,
    ENTITLED_SUBSCRIPTION_STATUSES,
    Environment,
    InboxThreadState,
    Subscription,
    Tenant,
    User,
)
from app.inbox.repository import get_thread
from app.tenancy.isolation import BoundaryDenied, Conflict, NotFound


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class RouteResult:
    entry: QueueEntry
    decision: RoutingDecision
    assignment: RoutingAssignment | None
    outcome: str
    callback_requested: bool = False
    transfer_accepted: bool = False

    def as_dict(self) -> dict:
        return {
            "entry": self.entry.as_dict(),
            "decision": self.decision.as_dict(),
            "assignment": self.assignment.as_dict() if self.assignment else None,
            "outcome": self.outcome,
            "callback_requested": self.callback_requested,
            "transfer_accepted": self.transfer_accepted,
        }


async def _audit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    operation: str,
    detail: dict,
) -> None:
    from app.auth.service import record_audit

    safe = {key: value for key, value in detail.items() if "number" not in key and "token" not in key}
    safe["operation"] = operation
    await record_audit(
        session,
        action=AuditAction.RESOURCE_BOUND,
        tenant_id=tenant_id,
        actor_user_id=actor_id,
        detail=safe,
        commit=False,
    )


async def assert_entitled(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """A missing subscription is entitled. An explicit non-entitled status is not."""
    row = (
        await session.execute(select(Subscription).where(Subscription.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if row is None:
        return
    if row.status not in ENTITLED_SUBSCRIPTION_STATUSES:
        raise QueueUnavailable("billing subscription is not entitled")


async def _environment(session: AsyncSession, tenant_id: uuid.UUID, environment_id: uuid.UUID) -> Environment:
    row = await session.get(Environment, environment_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    if row.status != "active":
        raise QueueUnavailable("environment is not active")
    return row


async def _call(session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID) -> Call:
    row = await session.get(Call, call_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


async def ensure_inbox(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    call_id: uuid.UUID,
) -> InboxThreadState:
    row = await get_thread(
        session, tenant_id=tenant_id, environment_id=environment_id, call_id=call_id
    )
    if row is not None:
        return row
    row = InboxThreadState(
        tenant_id=tenant_id,
        environment_id=environment_id,
        call_id=call_id,
        status="open",
        assignee_id="",
        tags=[],
        notes=[],
    )
    session.add(row)
    await session.flush()
    return row


async def _start_sla_if_empty(
    session: AsyncSession, thread: InboxThreadState, *, now: datetime
) -> None:
    if thread.first_response_deadline or thread.resolution_deadline:
        return
    from app.inbox import sla

    await sla.start(
        session,
        tenant_id=thread.tenant_id,
        environment_id=thread.environment_id,
        call_id=thread.call_id,
        now=now,
    )


async def build_candidates(session: AsyncSession, queue: Queue, *, now: datetime):
    from app.contact_center.models import Candidate
    from app.contact_center.repository import agent_skills
    from app.environments.membership import resolve

    roster = await members(session, queue.tenant_id, queue.id)
    catalog = await skills_for_tenant(session, queue.tenant_id)
    by_name = {row.name: row for row in catalog.values()}
    required = [str(name).strip().lower() for name in (queue.required_skills or [])]
    environment = await session.get(Environment, queue.environment_id)
    tenant = await session.get(Tenant, queue.tenant_id)
    found = []
    for member in roster:
        reason = member_reason(member)
        user = await session.get(User, member.user_id)
        if not reason and (user is None or user.tenant_id != queue.tenant_id):
            reason = "foreign_user"
        if not reason and user is not None and environment is not None and tenant is not None:
            access = await resolve(session, user, environment, tenant)
            if not access.allowed:
                reason = access.reason or "environment_denied"
        presence = await presence_for(session, queue.tenant_id, member.user_id)
        if not reason:
            reason = presence_reason(presence, now=now)
        held = await agent_skills(session, queue.tenant_id, member.user_id)
        if not reason:
            reason = skill_reason(required, held, by_name)
        proficiency, skill_count = skill_score(required, held, by_name)
        found.append(
            Candidate(
                user_id=member.user_id,
                member_priority=member.priority,
                proficiency=proficiency,
                skill_count=skill_count,
                active_count=0 if presence is None else presence.active_count,
                last_assigned_at=None if presence is None else presence.last_assigned_at,
                eligible=not reason,
                reason=reason,
            )
        )
    return found


async def _ordered_waiting(session: AsyncSession, queue: Queue) -> list[QueueEntry]:
    rows = await waiting_entries(session, queue.tenant_id, queue.id)
    breached: dict[uuid.UUID, bool] = {}
    for row in rows:
        thread = await get_thread(
            session,
            tenant_id=row.tenant_id,
            environment_id=row.environment_id,
            call_id=row.call_id,
        )
        breached[row.id] = bool(thread and thread.sla_state == "breached")

    def _key(row: QueueEntry):
        moment = row.enqueued_at
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return (0 if breached[row.id] else 1, -row.priority, moment, str(row.id))

    return sorted(rows, key=_key)


async def _decision(
    session: AsyncSession,
    *,
    queue: Queue,
    entry: QueueEntry | None,
    choice,
    applied: bool,
    outcome: str,
    user_id: uuid.UUID | None,
) -> RoutingDecision:
    row = RoutingDecision(
        tenant_id=queue.tenant_id,
        environment_id=queue.environment_id,
        entry_id=None if entry is None else entry.id,
        queue_id=queue.id,
        user_id=user_id,
        strategy=choice.strategy if choice is not None else queue.strategy,
        matched_skills=list(choice.matched_skills) if choice is not None else [],
        rejected=list(choice.rejected) if choice is not None else [],
        applied=applied,
        outcome=outcome,
    )
    return await add_decision(session, row)


async def _close_entry(
    session: AsyncSession, entry: QueueEntry, *, status: str, outcome: str
) -> None:
    expected = entry.version
    result = await session.execute(
        update(QueueEntry)
        .where(
            QueueEntry.id == entry.id,
            QueueEntry.tenant_id == entry.tenant_id,
            QueueEntry.version == expected,
        )
        .values(status=status, outcome=outcome, active_lock=None, version=expected + 1)
    )
    if result.rowcount != 1:
        raise StaleState("Queue item changed concurrently")
    await session.refresh(entry)


async def enqueue(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_id: uuid.UUID,
    call_id: uuid.UUID,
    priority: int = 0,
    actor_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> RouteResult:
    moment = now or _now()
    await assert_entitled(session, tenant_id)
    queue = await get_queue(session, tenant_id, queue_id)
    if not queue.enabled:
        raise QueueUnavailable("queue disabled")
    await _environment(session, tenant_id, queue.environment_id)
    call = await _call(session, tenant_id, call_id)
    if call.environment_id != queue.environment_id:
        raise BoundaryDenied()
    existing = await active_entry_for_call(session, tenant_id, call_id)
    if existing is not None:
        if existing.queue_id != queue.id:
            raise DuplicateAssignment("Call already has an active queue item")
        if existing.status == "waiting":
            return await _route_entry(session, queue, existing, actor_id=actor_id, now=moment)
        decision = await _decision(
            session,
            queue=queue,
            entry=existing,
            choice=None,
            applied=False,
            outcome="duplicate",
            user_id=None,
        )
        return RouteResult(existing, decision, None, "duplicate")
    if int(priority) < 0:
        raise QueueUnavailable("priority must be >= 0")
    entry = QueueEntry(
        tenant_id=tenant_id,
        environment_id=queue.environment_id,
        queue_id=queue.id,
        call_id=call.id,
        status="waiting",
        priority=int(priority),
        enqueued_at=moment,
        active_lock=str(call.id),
    )
    try:
        async with session.begin_nested():
            session.add(entry)
            await session.flush()
    except IntegrityError as exc:
        found = await active_entry_for_call(session, tenant_id, call_id)
        if found is None:
            raise DuplicateAssignment("Call already has an active queue item") from exc
        return RouteResult(found, await _decision(
            session, queue=queue, entry=found, choice=None, applied=False, outcome="duplicate", user_id=None
        ), None, "duplicate")
    await ensure_inbox(
        session, tenant_id=tenant_id, environment_id=queue.environment_id, call_id=call.id
    )
    thread = await get_thread(
        session, tenant_id=tenant_id, environment_id=queue.environment_id, call_id=call.id
    )
    if thread is not None:
        await _start_sla_if_empty(session, thread, now=moment)
    return await _route_entry(session, queue, entry, actor_id=actor_id, now=moment)


async def assign_next(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> RouteResult:
    moment = now or _now()
    await assert_entitled(session, tenant_id)
    queue = await get_queue(session, tenant_id, queue_id)
    if not queue.enabled:
        raise QueueUnavailable("queue disabled")
    await _environment(session, tenant_id, queue.environment_id)
    waiting = await _ordered_waiting(session, queue)
    if not waiting:
        raise QueueUnavailable("queue is empty")
    return await _route_entry(session, queue, waiting[0], actor_id=actor_id, now=moment, strict=True)


async def preview(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_id: uuid.UUID,
    now: datetime | None = None,
) -> RoutingDecision:
    moment = now or _now()
    queue = await get_queue(session, tenant_id, queue_id)
    await _environment(session, tenant_id, queue.environment_id)
    waiting = await _ordered_waiting(session, queue)
    entry = waiting[0] if waiting else None
    candidates = await build_candidates(session, queue, now=moment)
    choice = routing.choose(
        candidates,
        strategy=queue.strategy,
        required_skills=list(queue.required_skills or []),
        cursor=await cursor_for(session, tenant_id, queue.id),
    )
    selected = None if choice.selected is None else choice.selected.user_id
    return await _decision(
        session,
        queue=queue,
        entry=entry,
        choice=choice,
        applied=False,
        outcome="preview",
        user_id=selected,
    )


async def _route_entry(
    session: AsyncSession,
    queue: Queue,
    entry: QueueEntry,
    *,
    actor_id: uuid.UUID | None,
    now: datetime,
    strict: bool = False,
) -> RouteResult:
    if entry.status != "waiting":
        decision = await _decision(
            session, queue=queue, entry=entry, choice=None, applied=False, outcome="duplicate", user_id=None
        )
        return RouteResult(entry, decision, None, "duplicate")
    depth = await waiting_count(session, queue.tenant_id, queue.id)
    full = await assigned_count(session, queue.tenant_id, queue.id) >= queue.max_concurrency
    candidates = [] if full else await build_candidates(session, queue, now=now)
    choice = None
    if not full:
        choice = routing.choose(
            candidates,
            strategy=queue.strategy,
            required_skills=list(queue.required_skills or []),
            cursor=await cursor_for(session, queue.tenant_id, queue.id),
        )
    if choice is None or choice.selected is None:
        if overflow.due(queue, entry, now=now, waiting=depth):
            return await _overflow(session, queue, entry, actor_id=actor_id, now=now, strict=strict)
        decision = await _decision(
            session,
            queue=queue,
            entry=entry,
            choice=choice,
            applied=False,
            outcome="waiting",
            user_id=None,
        )
        if strict:
            raise NoEligibleAgent("no eligible agent")
        return RouteResult(entry, decision, None, "waiting")
    try:
        assignment = await _assign_selected(session, queue, entry, choice.selected.user_id, now=now)
    except (DuplicateAssignment, StaleState, Conflict) as exc:
        decision = await _decision(
            session,
            queue=queue,
            entry=entry,
            choice=choice,
            applied=False,
            outcome="assignment_failed",
            user_id=choice.selected.user_id,
        )
        await _audit(
            session,
            tenant_id=queue.tenant_id,
            actor_id=actor_id,
            operation="acd_assign_failed",
            detail={"entry_id": str(entry.id), "reason": type(exc).__name__},
        )
        raise DuplicateAssignment("assignment failed") from exc
    await save_cursor(session, queue.tenant_id, queue.id, str(choice.selected.user_id))
    decision = await _decision(
        session,
        queue=queue,
        entry=entry,
        choice=choice,
        applied=True,
        outcome="assigned",
        user_id=choice.selected.user_id,
    )
    await _audit(
        session,
        tenant_id=queue.tenant_id,
        actor_id=actor_id,
        operation="acd_assigned",
        detail={"entry_id": str(entry.id), "user_id": str(choice.selected.user_id), "decision_id": str(decision.id)},
    )
    return RouteResult(entry, decision, assignment, "assigned")


async def _assign_selected(
    session: AsyncSession,
    queue: Queue,
    entry: QueueEntry,
    user_id: uuid.UUID,
    *,
    now: datetime,
) -> RoutingAssignment:
    presence = await presence_for(session, queue.tenant_id, user_id)
    if presence is None:
        raise StaleState("Agent was no longer available")
    from app.inbox import assignment as inbox_assignment

    try:
        async with session.begin_nested():
            await claim_waiting(session, entry, now=now)
            await reserve_agent(session, presence, now=now)
            created = await insert_assignment(
                session,
                tenant_id=queue.tenant_id,
                entry_id=entry.id,
                queue_id=queue.id,
                user_id=user_id,
                capacity=presence.capacity,
            )
            await ensure_inbox(
                session,
                tenant_id=queue.tenant_id,
                environment_id=queue.environment_id,
                call_id=entry.call_id,
            )
            await inbox_assignment.claim(
                session,
                tenant_id=queue.tenant_id,
                environment_id=queue.environment_id,
                call_id=entry.call_id,
                agent_id=str(user_id),
            )
    except Exception:
        await session.refresh(entry)
        await session.refresh(presence)
        raise
    return created


async def _overflow(
    session: AsyncSession,
    queue: Queue,
    entry: QueueEntry,
    *,
    actor_id: uuid.UUID | None,
    now: datetime,
    strict: bool,
) -> RouteResult:
    decision_choice = overflow.decide(queue)
    if decision_choice.action == "leave_queued":
        decision = await _decision(
            session, queue=queue, entry=entry, choice=None, applied=False, outcome="waiting", user_id=None
        )
        if strict:
            raise NoEligibleAgent("no eligible agent")
        return RouteResult(entry, decision, None, "waiting")
    if decision_choice.action == "callback":
        stored = await callbacks.request_callback(session, entry)
        if not stored:
            raise OverflowDenied("callback was not stored")
        await _close_entry(session, entry, status="overflowed", outcome="callback_requested")
        decision = await _decision(
            session, queue=queue, entry=entry, choice=None, applied=True, outcome="callback_requested", user_id=None
        )
        await _audit(
            session,
            tenant_id=queue.tenant_id,
            actor_id=actor_id,
            operation="acd_callback_requested",
            detail={"entry_id": str(entry.id), "decision_id": str(decision.id)},
        )
        return RouteResult(entry, decision, None, "callback_requested", callback_requested=True)
    if decision_choice.action == "voicemail":
        await _close_entry(session, entry, status="overflowed", outcome="voicemail_requested")
        decision = await _decision(
            session, queue=queue, entry=entry, choice=None, applied=True, outcome="voicemail_requested", user_id=None
        )
        return RouteResult(entry, decision, None, "voicemail_requested")
    if decision_choice.action == "escalate_unavailable":
        await _close_entry(session, entry, status="failed", outcome="escalate_unavailable")
        decision = await _decision(
            session, queue=queue, entry=entry, choice=None, applied=False, outcome="escalate_unavailable", user_id=None
        )
        return RouteResult(entry, decision, None, "escalate_unavailable")
    if decision_choice.action != "escalate":
        raise OverflowDenied("unknown overflow")
    accepted = await _request_telephony(session, queue.tenant_id, entry)
    outcome = "escalate_accepted" if accepted else "escalate_failed"
    await _close_entry(
        session,
        entry,
        status="overflowed" if accepted else "failed",
        outcome=outcome,
    )
    decision = await _decision(
        session, queue=queue, entry=entry, choice=None, applied=accepted, outcome=outcome, user_id=None
    )
    await _audit(
        session,
        tenant_id=queue.tenant_id,
        actor_id=actor_id,
        operation="acd_escalate",
        detail={"entry_id": str(entry.id), "accepted": accepted, "decision_id": str(decision.id)},
    )
    return RouteResult(entry, decision, None, outcome, transfer_accepted=accepted)


async def _request_telephony(session: AsyncSession, tenant_id: uuid.UUID, entry: QueueEntry) -> bool:
    """Telephony owns the dial. Success is only ``TransferResult.ok``."""
    from app.telephony.transfer_service import request_transfer

    tenant = await session.get(Tenant, tenant_id)
    call = await session.get(Call, entry.call_id)
    if tenant is None or call is None or call.tenant_id != tenant_id:
        return False
    result = await request_transfer(session, tenant, call, reason="acd overflow")
    return bool(result.ok)


async def release_assignment(
    session: AsyncSession,
    assignment: RoutingAssignment,
    *,
    now: datetime | None = None,
    requeue: bool = True,
) -> str:
    if assignment.status != "active":
        return "duplicate"
    moment = now or _now()
    entry = await get_entry(session, assignment.tenant_id, assignment.entry_id)
    presence = await presence_for(session, assignment.tenant_id, assignment.user_id)
    async with session.begin_nested():
        result = await session.execute(
            update(RoutingAssignment)
            .where(
                RoutingAssignment.id == assignment.id,
                RoutingAssignment.tenant_id == assignment.tenant_id,
                RoutingAssignment.status == "active",
            )
            .values(status="released", entry_lock=None, agent_lock=None, released_at=moment)
        )
        if result.rowcount != 1:
            return "duplicate"
        if presence is not None and presence.active_count > 0:
            expected = presence.version
            updated = await session.execute(
                update(AgentPresence)
                .where(
                    AgentPresence.id == presence.id,
                    AgentPresence.tenant_id == presence.tenant_id,
                    AgentPresence.version == expected,
                )
                .values(active_count=AgentPresence.active_count - 1, version=expected + 1)
            )
            if updated.rowcount != 1:
                raise StaleState("Agent state changed concurrently")
        if requeue and entry.status == "assigned":
            expected = entry.version
            updated = await session.execute(
                update(QueueEntry)
                .where(
                    QueueEntry.id == entry.id,
                    QueueEntry.tenant_id == entry.tenant_id,
                    QueueEntry.version == expected,
                    QueueEntry.status == "assigned",
                )
                .values(
                    status="waiting",
                    assigned_at=None,
                    outcome="requeued",
                    active_lock=str(entry.call_id),
                    version=expected + 1,
                )
            )
            if updated.rowcount != 1:
                raise StaleState("Queue item changed concurrently")
        thread = await get_thread(
            session,
            tenant_id=entry.tenant_id,
            environment_id=entry.environment_id,
            call_id=entry.call_id,
        )
        if thread is not None and thread.assignee_id == str(assignment.user_id):
            from app.inbox import assignment as inbox_assignment

            await inbox_assignment.release(
                session,
                tenant_id=entry.tenant_id,
                environment_id=entry.environment_id,
                call_id=entry.call_id,
                agent_id=str(assignment.user_id),
            )
    await session.refresh(assignment)
    await session.refresh(entry)
    if presence is not None:
        await session.refresh(presence)
    return "applied"


async def release_user_work(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> int:
    rows = await active_for_user(session, tenant_id, user_id)
    released = 0
    for row in rows:
        outcome = await release_assignment(session, row, now=now, requeue=True)
        if outcome == "applied":
            released += 1
    return released


async def requeue(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    entry_id: uuid.UUID,
    actor_id: uuid.UUID | None = None,
) -> RouteResult:
    entry = await get_entry(session, tenant_id, entry_id)
    queue = await get_queue(session, tenant_id, entry.queue_id)
    current = await active_assignment_for_entry(session, tenant_id, entry.id)
    if current is not None:
        outcome = await release_assignment(session, current, requeue=True)
        if outcome == "duplicate" and entry.status != "waiting":
            raise StaleState("Could not requeue")
    elif entry.status != "waiting":
        await session.execute(
            update(QueueEntry)
            .where(QueueEntry.id == entry.id, QueueEntry.tenant_id == tenant_id)
            .values(
                status="waiting",
                outcome="requeued",
                active_lock=str(entry.call_id),
                assigned_at=None,
                version=entry.version + 1,
            )
        )
        await session.refresh(entry)
    await _audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        operation="acd_requeue",
        detail={"entry_id": str(entry.id)},
    )
    await session.refresh(entry)
    if entry.status != "waiting":
        raise StaleState("Could not requeue")
    return await _route_entry(session, queue, entry, actor_id=actor_id, now=_now(), strict=False)


async def set_state(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    target: str,
    actor_id: uuid.UUID | None,
    supervisor: bool = False,
    capacity: int | None = None,
) -> tuple[AgentPresence, str]:
    if actor_id != user_id and not supervisor:
        raise AgentUnavailable("agents may change only their own state")
    row = await ensure(session, tenant_id, user_id)
    if capacity is not None:
        if int(capacity) < 1 or int(capacity) < row.active_count:
            raise AgentUnavailable("capacity is below active assignments")
        row.capacity = int(capacity)
    outcome = await transition(session, row, target, supervisor=supervisor)
    if outcome == "applied":
        await _audit(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            operation="acd_agent_state",
            detail={"user_id": str(user_id), "state": target},
        )
    return row, outcome


async def accept_work(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID, actor_id: uuid.UUID
) -> str:
    if actor_id != user_id:
        raise AgentUnavailable("only the agent can accept")
    row = await presence_for(session, tenant_id, user_id)
    if row is None:
        raise AgentUnavailable("no presence")
    return await transition(session, row, "busy")


async def complete_work(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    assignment_id: uuid.UUID,
    actor_id: uuid.UUID,
    supervisor: bool = False,
) -> str:
    assignment = await get_assignment(session, tenant_id, assignment_id)
    if assignment.user_id != actor_id and not supervisor:
        raise AgentUnavailable("only the owner can complete")
    if assignment.status != "active":
        return "duplicate"
    entry = await get_entry(session, tenant_id, assignment.entry_id)
    outcome = await release_assignment(session, assignment, requeue=False)
    if outcome != "applied":
        return outcome
    await _close_entry(session, entry, status="completed", outcome="completed")
    presence = await presence_for(session, tenant_id, assignment.user_id)
    if presence is not None and presence.active_count == 0 and presence.state in {"ringing", "busy"}:
        target = "wrap_up" if presence.state == "busy" else "available"
        await transition(session, presence, target)
    return "applied"


async def supervisor_reassign(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    assignment_id: uuid.UUID,
    user_id: uuid.UUID,
    actor_id: uuid.UUID,
    now: datetime | None = None,
) -> RouteResult:
    moment = now or _now()
    current = await get_assignment(session, tenant_id, assignment_id)
    if current.status != "active":
        entry = await get_entry(session, tenant_id, current.entry_id)
        decision = RoutingDecision(
            tenant_id=tenant_id,
            entry_id=entry.id,
            queue_id=current.queue_id,
            user_id=current.user_id,
            applied=False,
            outcome="duplicate",
        )
        await add_decision(session, decision)
        return RouteResult(entry, decision, current, "duplicate")
    if current.user_id == user_id:
        entry = await get_entry(session, tenant_id, current.entry_id)
        decision = RoutingDecision(
            tenant_id=tenant_id,
            entry_id=entry.id,
            queue_id=current.queue_id,
            user_id=user_id,
            applied=False,
            outcome="duplicate",
        )
        await add_decision(session, decision)
        return RouteResult(entry, decision, current, "duplicate")
    queue = await get_queue(session, tenant_id, current.queue_id)
    candidates = await build_candidates(session, queue, now=moment)
    match = next((item for item in candidates if item.user_id == user_id and item.eligible), None)
    if match is None:
        raise NoEligibleAgent("target agent is not eligible")
    entry = await get_entry(session, tenant_id, current.entry_id)
    await release_assignment(session, current, now=moment, requeue=True)
    await session.refresh(entry)
    result = await _assign_selected(session, queue, entry, user_id, now=moment)
    decision = await _decision(
        session, queue=queue, entry=entry, choice=None, applied=True, outcome="reassigned", user_id=user_id
    )
    await _audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        operation="acd_reassign",
        detail={"assignment_id": str(assignment_id), "user_id": str(user_id)},
    )
    return RouteResult(entry, decision, result, "reassigned")
