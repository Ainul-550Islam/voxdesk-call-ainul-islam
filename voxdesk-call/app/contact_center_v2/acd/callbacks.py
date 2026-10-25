"""Scheduled ACD callbacks executed through the canonical durable job queue."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.models import Queue, QueueEntry
from app.contact_center_v2.errors import Conflict, NotConfigured, NotFound, Validation
from app.core.logging import log
from app.contact_center_v2.occupancy import (
    active_softphone_session,
    available_agent_ids,
    release_agent,
    reserve_agent,
)
from app.contact_center_v2.pacing import timezone_for_contact
from app.contact_center_v2.policy import verified_caller_id
from app.contact_center_v2.repository import as_utc, owned, utcnow
from app.contact_center_v2.settings import settings
from app.db.contact_center_v2_models import (
    AgentReservation,
    CallbackRequest,
    DialerPolicy,
    SoftphoneCall,
    SoftphoneLeg,
)
from app.db.models import (
    Call,
    CallDirection,
    CallStatus,
    RequestIdempotencyReceipt,
    Tenant,
)
from app.jobs.queue import cancel as cancel_job
from app.jobs.queue import enqueue
from app.jobs.types import JobType
from app.resilience.idempotency import claim_request, complete_request, fail_request
from app.telephony import dnc, phone
from app.telephony.providers.twilio import TwilioAdapter


def _public_url(path: str) -> str:
    from app.contact_center_v2.softphone import _public_url as build

    return build(path)


def _destination(call: Call) -> str:
    raw = call.from_number if call.direction is CallDirection.INBOUND else call.to_number
    return phone.normalize(raw)


async def _enqueue_attempt(
    session: AsyncSession,
    callback: CallbackRequest,
    *,
    available_at: datetime,
    legacy_key: bool = False,
    idempotency_key: str | None = None,
) -> uuid.UUID:
    key = idempotency_key or (
        f"acd-callback:{callback.queue_entry_id}"
        if legacy_key
        else (
            f"acd-callback:{callback.queue_entry_id}:attempt:{callback.attempts + 1}"
            f":at:{int(available_at.timestamp())}"
        )
    )
    job, _ = await enqueue(
        session,
        tenant_id=callback.tenant_id,
        environment_id=callback.environment_id,
        job_type=JobType.ACD_CALLBACK,
        idempotency_key=key,
        payload={"callback_id": str(callback.id)},
        available_at=available_at,
        max_attempts=2,
        priority=0,
    )
    callback.job_id = job.id
    return job.id


async def schedule_callback(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_entry_id: uuid.UUID,
    requested_by: uuid.UUID | None,
    scheduled_at: datetime,
    timezone_name: str,
    note: str = "",
) -> tuple[CallbackRequest, bool]:
    entry = await owned(session, QueueEntry, queue_entry_id, tenant_id)
    if entry.status == "assigned":
        raise Conflict("Release the active assignment before scheduling a callback")
    queue = await session.get(Queue, entry.queue_id)
    call = await session.get(Call, entry.call_id)
    if queue is None or queue.tenant_id != tenant_id or call is None or call.tenant_id != tenant_id:
        raise NotFound("callback scope")
    timezone_for_contact(timezone_name)
    if scheduled_at.tzinfo is None:
        raise Validation("scheduled_at must include a timezone offset")
    moment = as_utc(scheduled_at)
    if moment > utcnow() + timedelta(days=31):
        raise Validation("Callbacks may be scheduled at most 31 days ahead")
    _destination(call)  # validate before persisting a request that can never execute
    existing = (
        await session.execute(
            select(CallbackRequest).where(
                CallbackRequest.tenant_id == tenant_id,
                CallbackRequest.queue_entry_id == entry.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            as_utc(existing.scheduled_at) != moment
            or existing.timezone != timezone_name
            or existing.status == "cancelled"
        ):
            raise Conflict("A different callback is already scheduled for this queue entry")
        return existing, False
    callback = CallbackRequest(
        tenant_id=tenant_id,
        environment_id=entry.environment_id,
        queue_entry_id=entry.id,
        queue_id=entry.queue_id,
        call_id=entry.call_id,
        requested_by=requested_by,
        status="scheduled",
        timezone=timezone_name,
        scheduled_at=moment,
        next_attempt_at=moment,
        note=note,
    )
    session.add(callback)
    await session.flush()
    await _enqueue_attempt(session, callback, available_at=moment, legacy_key=True)
    entry.outcome = "callback_scheduled"
    await session.flush()
    return callback, True


async def ensure_legacy_callback(
    session: AsyncSession, *, tenant_id: uuid.UUID, entry_id: uuid.UUID, job_id: uuid.UUID
) -> CallbackRequest:
    entry = await owned(session, QueueEntry, entry_id, tenant_id)
    existing = (
        await session.execute(
            select(CallbackRequest).where(
                CallbackRequest.tenant_id == tenant_id,
                CallbackRequest.queue_entry_id == entry.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    call = await session.get(Call, entry.call_id)
    tenant = await session.get(Tenant, tenant_id)
    if call is None or tenant is None:
        raise NotFound("callback scope")
    now = utcnow()
    callback = CallbackRequest(
        tenant_id=tenant_id,
        environment_id=entry.environment_id,
        queue_entry_id=entry.id,
        queue_id=entry.queue_id,
        call_id=entry.call_id,
        requested_by=None,
        job_id=job_id,
        status="scheduled",
        timezone=tenant.timezone,
        scheduled_at=now,
        next_attempt_at=now,
        note="Automatic ACD overflow callback",
    )
    session.add(callback)
    await session.flush()
    return callback


async def cancel_callback(
    session: AsyncSession, *, tenant_id: uuid.UUID, callback_id: uuid.UUID
) -> CallbackRequest:
    row = await owned(session, CallbackRequest, callback_id, tenant_id, for_update=True)
    if row.status == "running":
        raise Conflict("A running callback cannot be cancelled; end its call instead")
    if row.status in {"completed", "failed"}:
        raise Conflict("A terminal callback cannot be cancelled")
    if row.status != "cancelled":
        row.status = "cancelled"
        row.cancelled_at = utcnow()
        await session.flush()
    return row


async def reschedule_callback(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    callback_id: uuid.UUID,
    scheduled_at: datetime,
    timezone_name: str,
) -> tuple[CallbackRequest, bool]:
    """Move a scheduled callback without leaving its old dispatch job active."""
    row = await owned(session, CallbackRequest, callback_id, tenant_id, for_update=True)
    if row.status != "scheduled":
        raise Conflict("Only a scheduled callback can be rescheduled")
    timezone_for_contact(timezone_name)
    if scheduled_at.tzinfo is None:
        raise Validation("scheduled_at must include a timezone offset")
    moment = as_utc(scheduled_at)
    if moment > utcnow() + timedelta(days=31):
        raise Validation("Callbacks may be scheduled at most 31 days ahead")
    if as_utc(row.scheduled_at) == moment and row.timezone == timezone_name:
        return row, False
    if row.job_id is not None:
        cancelled = await cancel_job(session, tenant_id=tenant_id, job_id=row.job_id)
        if cancelled not in {"cancelled", "already:cancelled"}:
            raise Conflict("The existing callback dispatch could not be cancelled safely")
    row.scheduled_at = moment
    row.next_attempt_at = moment
    row.timezone = timezone_name
    await _enqueue_attempt(
        session,
        row,
        available_at=moment,
        idempotency_key=f"acd-callback:{row.id}:reschedule:{uuid.uuid4().hex}",
    )
    entry = await owned(session, QueueEntry, row.queue_entry_id, tenant_id)
    entry.outcome = "callback_rescheduled"
    await session.flush()
    return row, True


async def _active_reservation(
    session: AsyncSession, callback: CallbackRequest
) -> AgentReservation | None:
    return (
        await session.execute(
            select(AgentReservation).where(
                AgentReservation.tenant_id == callback.tenant_id,
                AgentReservation.kind == "callback",
                AgentReservation.resource_id == callback.id,
                AgentReservation.status == "active",
            )
        )
    ).scalar_one_or_none()


async def execute_callback(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    callback_id: uuid.UUID,
    adapter: TwilioAdapter | None = None,
) -> SoftphoneCall | None:
    """Execute one callback attempt without repeating an ambiguous provider side effect.

    The running row, reservation, and idempotency receipt are committed before
    Twilio is called. Any provider or post-provider exception therefore fails
    closed: it never schedules another automatic attempt from this code path.
    """
    adapter = adapter or TwilioAdapter()
    if not adapter.configured():
        raise NotConfigured("Twilio account SID and auth token are required")
    callback = await owned(session, CallbackRequest, callback_id, tenant_id, for_update=True)
    if callback.status in {"completed", "failed", "cancelled"}:
        return None
    active = (
        await session.execute(
            select(SoftphoneCall).where(
                SoftphoneCall.tenant_id == tenant_id,
                SoftphoneCall.callback_id == callback.id,
                SoftphoneCall.status.in_(("dialing", "waiting_for_agent", "in_progress", "held")),
            )
        )
    ).scalar_one_or_none()
    if active is not None:
        return active
    if callback.status == "running":
        # A previous worker crossed the provider boundary or is still doing so.
        # With no active durable call there is no proof that redialing is safe.
        return None
    if callback.next_attempt_at and as_utc(callback.next_attempt_at) > utcnow():
        return None
    if callback.attempts >= settings.callback_max_attempts:
        callback.status = "failed"
        callback.failure_category = "attempts_exhausted"
        await session.flush()
        return None
    original = await session.get(Call, callback.call_id)
    tenant = await session.get(Tenant, tenant_id)
    if original is None or original.tenant_id != tenant_id or tenant is None:
        raise NotFound("callback scope")
    try:
        destination = _destination(original)
    except ValueError as exc:
        callback.status = "failed"
        callback.failure_category = "invalid_destination"
        await session.flush()
        raise Validation("Callback destination is invalid") from exc
    blocked, reason = await dnc.is_blocked(
        session,
        tenant_id,
        destination,
        environment_id=callback.environment_id,
        lead_id=original.lead_id,
    )
    if blocked:
        callback.status = "cancelled"
        callback.failure_category = (reason or "dnc")[:64]
        callback.cancelled_at = utcnow()
        await session.flush()
        return None
    agents = await available_agent_ids(
        session, tenant_id, require_softphone=True, environment_id=callback.environment_id
    )
    registration = None
    agent_id = None
    for candidate_id in agents:
        candidate = await active_softphone_session(session, tenant_id, candidate_id)
        if candidate is not None and candidate.environment_id == callback.environment_id:
            agent_id, registration = candidate_id, candidate
            break
    if agent_id is None or registration is None:
        callback.status = "scheduled"
        callback.next_attempt_at = utcnow() + timedelta(seconds=settings.callback_retry_seconds)
        await _enqueue_attempt(session, callback, available_at=callback.next_attempt_at)
        await session.flush()
        return None
    caller = tenant.outbound_caller_id or tenant.twilio_number
    probe = DialerPolicy(
        tenant_id=tenant_id,
        environment_id=callback.environment_id,
        caller_id_number=caller or "",
    )
    await verified_caller_id(session, probe)
    callback.attempts += 1
    callback.assigned_agent_id = agent_id
    callback.status = "running"
    callback.failure_category = ""
    await reserve_agent(
        session,
        tenant_id=tenant_id,
        environment_id=callback.environment_id,
        user_id=agent_id,
        kind="callback",
        resource_id=callback.id,
        require_softphone=True,
    )
    attempt = callback.attempts
    idempotency_key = f"callback:{callback.id}:attempt:{attempt}"
    payload = {
        "callback_id": str(callback.id),
        "attempt": attempt,
        "destination": destination,
        "caller_id_number": caller,
    }
    claim = await claim_request(
        session,
        tenant_id=tenant_id,
        environment_id=callback.environment_id,
        operation="contact_center_v2.callback.dial",
        key=idempotency_key,
        request_data=payload,
    )
    if not claim.created:
        resource_id = claim.receipt.resource_id
        await session.rollback()  # discard the duplicate reservation/attempt increment
        existing = await session.get(SoftphoneCall, uuid.UUID(resource_id))
        if existing is None or existing.tenant_id != tenant_id:
            raise NotFound("callback call")
        return existing
    answer_url = _public_url(f"/telephony/voice?cc2=callback-customer&callback_id={callback.id}")
    await session.commit()
    receipt_id = claim.receipt.id
    try:
        from app.contact_center_v2.softphone import run_provider

        result = await run_provider(
            adapter.create_outbound,
            to_number=destination,
            from_number=caller or "",
            answer_url=answer_url,
        )
    except Exception as exc:
        # A transport exception does not prove that Twilio rejected the call.
        # Keep the running row/reservation and require provider reconciliation.
        await session.rollback()
        await _mark_callback_ambiguous(
            session,
            tenant_id=tenant_id,
            callback_id=callback_id,
            receipt_id=receipt_id,
            category="provider_outcome_ambiguous",
        )
        log.warning(
            "cc2_callback_provider_ambiguous",
            tenant_id=str(tenant_id),
            callback_id=str(callback_id),
            error_type=type(exc).__name__,
        )
        return None

    # Provider success has occurred. Nothing in this block may schedule a retry.
    try:
        canonical = Call(
            tenant_id=tenant_id,
            environment_id=callback.environment_id,
            call_sid=result.call_id,
            direction=CallDirection.OUTBOUND,
            from_number=result.from_number or caller,
            to_number=result.to_number or destination,
            status=CallStatus.RINGING,
            lead_id=original.lead_id,
            started_at=utcnow(),
        )
        session.add(canonical)
        await session.flush()
        call = SoftphoneCall(
            tenant_id=tenant_id,
            environment_id=callback.environment_id,
            session_id=registration.id,
            agent_user_id=agent_id,
            origin="callback",
            status="dialing",
            call_id=canonical.id,
            callback_id=callback.id,
            conference_name="pending",
            idempotency_key=idempotency_key,
        )
        session.add(call)
        await session.flush()
        from app.contact_center_v2.softphone import _conference_name

        call.conference_name = _conference_name(call.id)
        session.add(
            SoftphoneLeg(
                tenant_id=tenant_id,
                environment_id=callback.environment_id,
                softphone_call_id=call.id,
                call_id=canonical.id,
                kind="customer",
                provider_call_id=result.call_id,
                idempotency_key=f"customer:{call.id}",
                status="ringing",
                destination_redacted=phone.redact(destination),
            )
        )
        receipt = await session.get(RequestIdempotencyReceipt, receipt_id)
        if receipt is None:
            raise LookupError("callback idempotency receipt disappeared")
        await complete_request(
            session, receipt, resource_type="softphone_call", resource_id=call.id
        )
        await session.commit()
        return call
    except Exception as exc:
        try:
            await session.rollback()
            await _mark_callback_ambiguous(
                session,
                tenant_id=tenant_id,
                callback_id=callback_id,
                receipt_id=receipt_id,
                category="post_provider_persistence_ambiguous",
            )
        except Exception as persistence_exc:
            log.error(
                "cc2_callback_ambiguity_persistence_failed",
                tenant_id=str(tenant_id),
                callback_id=str(callback_id),
                error_type=type(persistence_exc).__name__,
            )
        log.error(
            "cc2_callback_post_provider_failure",
            tenant_id=str(tenant_id),
            callback_id=str(callback_id),
            error_type=type(exc).__name__,
        )
        return None


async def _mark_callback_ambiguous(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    callback_id: uuid.UUID,
    receipt_id: uuid.UUID,
    category: str,
) -> None:
    """Best-effort durable marker; never accesses expired pre-rollback ORM rows."""
    current = await session.get(CallbackRequest, callback_id)
    if current is not None and current.tenant_id == tenant_id and current.status == "running":
        current.failure_category = category
        # Deliberately retain status=running and the active reservation. This
        # blocks another attempt until an operator reconciles the provider.
        await session.flush()
    receipt = await session.get(RequestIdempotencyReceipt, receipt_id)
    if receipt is not None and receipt.tenant_id == tenant_id:
        await fail_request(session, receipt, category=category)
    await session.commit()

async def finish_callback(
    session: AsyncSession, *, callback_id: uuid.UUID, outcome: str
) -> None:
    callback = await session.get(CallbackRequest, callback_id)
    if callback is None or callback.status in {"completed", "failed", "cancelled"}:
        return
    reservation = await _active_reservation(session, callback)
    if reservation is not None:
        await release_agent(
            session, reservation, wrap_up=outcome in {"completed", "transferred"}
        )
    entry = await session.get(QueueEntry, callback.queue_entry_id)
    if outcome in {"completed", "transferred"}:
        callback.status = "completed"
        callback.completed_at = utcnow()
        callback.failure_category = ""
        if entry is not None:
            entry.outcome = "callback_completed"
    elif callback.attempts < settings.callback_max_attempts:
        callback.status = "scheduled"
        callback.failure_category = outcome[:64]
        callback.next_attempt_at = utcnow() + timedelta(seconds=settings.callback_retry_seconds)
        await _enqueue_attempt(session, callback, available_at=callback.next_attempt_at)
        if entry is not None:
            entry.outcome = "callback_retry_scheduled"
    else:
        callback.status = "failed"
        callback.failure_category = outcome[:64]
        if entry is not None:
            entry.outcome = "callback_failed"
    await session.flush()


async def list_callbacks(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    status: str | None = None,
    agent_user_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[CallbackRequest]:
    stmt = select(CallbackRequest).where(CallbackRequest.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(CallbackRequest.status == status)
    if agent_user_id is not None:
        stmt = stmt.where(
            (CallbackRequest.requested_by == agent_user_id)
            | (CallbackRequest.assigned_agent_id == agent_user_id)
        )
    return (
        await session.execute(
            stmt.order_by(CallbackRequest.next_attempt_at, CallbackRequest.created_at).limit(limit)
        )
    ).scalars().all()


__all__ = [
    "cancel_callback",
    "ensure_legacy_callback",
    "execute_callback",
    "finish_callback",
    "list_callbacks",
    "schedule_callback",
]
