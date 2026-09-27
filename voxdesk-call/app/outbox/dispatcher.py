"""Outbox delivery through the durable job system (Batch 07).

No second worker architecture: the dispatcher's whole job is to turn due
``outbox_events`` rows into ``jobs`` rows of type ``outbox.delivery``, and
the registered handler below executes one delivery round on the ordinary
worker path (claim → lease → heartbeat → guarded ack). The actual HTTP send
is the *existing* ``app.webhooks.delivery`` — signed (HMAC ``t=,v1=``),
SSRF-guarded, redirect-refusing — and the per-subscription
``webhook_deliveries`` ledger keeps redelivery idempotent.

Round ownership is a compare-and-set on the event row: ``dispatch_due``
bumps ``attempt_count`` from its expected value in a guarded UPDATE, so two
scheduler instances (or one tick racing itself) enqueue exactly one job per
round — the loser's UPDATE matches zero rows. The job's idempotency key
carries the round number, so even a duplicated enqueue collapses onto the
same ``jobs`` row.

Failure handling per round (the event is the durable record; each job is
one attempt with ``max_attempts=1``):

* every matched subscription succeeded → event ``delivered``;
* any permanent failure (4xx auth/validation, redirect, invalid endpoint,
  unopenable secret) → event ``dead_letter`` immediately — retrying a
  condition that cannot change by itself is a loop, not a policy;
* only transient failures (timeout, network, 408/429/5xx) → event stays
  ``pending`` with ``available_at`` pushed out on the existing bounded
  exponential curve, until the attempt budget exhausts into ``dead_letter``.

A round whose job dies before committing (crash, DB blip) self-heals: the
event is still ``pending`` and due, so the next tick CAS-claims the next
round and enqueues a fresh job. Delivery is at-least-once end to end; a
consumer that already answered 2xx is never re-sent to (ledger check), and
consumers deduplicate on the ``X-VoxDesk-Event`` id.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import log
from app.db.models import DurableJob, WebhookSubscription
from app.db.session import get_sessionmaker
from app.jobs import queue
from app.jobs.models import PermanentJobError
from app.jobs.types import JobType, register_handler_function
from app.outbox.idempotency import already_delivered, begin_delivery, delivery_job_key
from app.outbox.models import OutboxEvent, OutboxEventStatus
from app.outbox.retry import is_exhausted, next_delivery_delay
from app.webhooks.delivery import HttpResult, deliver
from app.webhooks.repository import open_secret

#: One delivery round is one job attempt. Retrying the *round* is the
#: dispatcher's CAS loop, not the job's own retry budget — two nested retry
#: loops would square the attempt count.
JOB_MAX_ATTEMPTS = 1


def _now(moment: datetime | None) -> datetime:
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current


async def dispatch_due(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 25,
) -> dict:
    """Enqueue one durable delivery job per due event round. Idempotent.

    Safe from every scheduler instance on every tick: round ownership is a
    compare-and-set on ``attempt_count`` and job identity is the tenant
    idempotency key. Flushes; the caller commits (the tick and its jobs are
    one transaction).
    """
    moment = _now(now)
    due = list(
        (
            await session.execute(
                select(OutboxEvent)
                .where(
                    OutboxEvent.status == OutboxEventStatus.PENDING.value,
                    OutboxEvent.available_at <= moment,
                )
                .order_by(OutboxEvent.available_at, OutboxEvent.created_at)
                .limit(max(1, min(int(limit), 200)))
            )
        ).scalars()
    )
    scheduled = 0
    exhausted = 0
    skipped_in_flight = 0
    for event in due:
        expected = int(event.attempt_count)
        if expected > 0:
            # Is the current round's job still alive? A round is "in flight"
            # while its job is claimable/claimed/waiting-retry; only when it
            # is gone or terminal may the next round be admitted. Without
            # this, a dispatch tick that runs faster than the worker would
            # stack duplicate rounds onto the same event.
            in_flight = (
                await session.execute(
                    select(DurableJob.status).where(
                        DurableJob.tenant_id == event.tenant_id,
                        DurableJob.idempotency_key
                        == delivery_job_key(event.id, attempt=expected),
                    )
                )
            ).scalar_one_or_none()
            if in_flight in ("queued", "running", "retry_scheduled"):
                skipped_in_flight += 1
                continue
        if expected >= int(event.max_attempts):
            # Budget spent (e.g. the final round's job died before it could
            # record the outcome). Park the event instead of looping.
            parked = await session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.id == event.id,
                    OutboxEvent.status == OutboxEventStatus.PENDING.value,
                    OutboxEvent.attempt_count >= OutboxEvent.max_attempts,
                )
                .values(
                    status=OutboxEventStatus.DEAD_LETTER.value,
                    last_error_category="attempts_exhausted",
                    last_error="delivery attempt budget exhausted",
                ), execution_options={"synchronize_session": False})
            if parked.rowcount == 1:
                event.status = OutboxEventStatus.DEAD_LETTER.value
                event.last_error_category = "attempts_exhausted"
                exhausted += 1
            continue
        claimed = await session.execute(
            update(OutboxEvent)
            .where(
                OutboxEvent.id == event.id,
                OutboxEvent.status == OutboxEventStatus.PENDING.value,
                OutboxEvent.available_at <= moment,
                OutboxEvent.attempt_count == expected,
            )
            .values(attempt_count=expected + 1), execution_options={"synchronize_session": False})
        if claimed.rowcount != 1:
            continue  # another dispatcher owns this round, or the event moved on
        event.attempt_count = expected + 1
        await queue.enqueue(
            session,
            tenant_id=event.tenant_id,
            job_type=JobType.OUTBOX_DELIVERY,
            idempotency_key=delivery_job_key(event.id, attempt=expected + 1),
            payload={"event_id": str(event.id)},
            environment_id=event.environment_id,
            max_attempts=JOB_MAX_ATTEMPTS,
        )
        scheduled += 1
    await session.flush()
    if scheduled or exhausted:
        log.info("outbox.dispatched", scheduled=scheduled, exhausted=exhausted, due=len(due))
    return {
        "due": len(due),
        "scheduled": scheduled,
        "exhausted": exhausted,
        "in_flight": skipped_in_flight,
    }


def event_envelope(event: OutboxEvent) -> bytes:
    """The signed request body: the event, its version and its reference payload.

    Canonical JSON (sorted keys, compact separators) so the signature is
    reproducible. The tenant id is included because the endpoint *is* the
    tenant's own; nothing about any other tenant can appear — the row was
    loaded under the job's persisted scope.
    """
    envelope = {
        "event_id": str(event.id),
        "tenant_id": str(event.tenant_id),
        "event_type": event.event_type,
        "event_version": int(event.event_version),
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "payload": event.payload or {},
        "occurred_at": event.created_at.isoformat() if event.created_at else "",
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


async def matching_subscriptions(
    session: AsyncSession, event: OutboxEvent
) -> list[WebhookSubscription]:
    """Enabled subscriptions that should receive this event.

    Matching rules (tenant first, always): the subscription's tenant must be
    the event's tenant; ``event_types`` empty means "everything", otherwise
    the event type must be listed; a subscription bound to an environment
    only receives events from that same environment, while an
    environment-wide subscription (``environment_id IS NULL``) receives the
    tenant's events regardless of their environment.
    """
    rows = list(
        (
            await session.execute(
                select(WebhookSubscription).where(
                    WebhookSubscription.tenant_id == event.tenant_id,
                    WebhookSubscription.enabled.is_(True),
                )
            )
        ).scalars()
    )
    matched = []
    for sub in rows:
        wanted = list(sub.event_types or [])
        if wanted and event.event_type not in wanted:
            continue
        if sub.environment_id is not None and sub.environment_id != event.environment_id:
            continue
        matched.append(sub)
    return matched


async def deliver_event(
    session: AsyncSession,
    event: OutboxEvent,
    *,
    now: datetime | None = None,
    transport=None,
) -> str:
    """Run one delivery round for an event. Returns the event's new status.

    Idempotent by state: an event that is not ``pending`` (cancelled,
    delivered, dead-lettered — possibly by a concurrent worker or operator)
    is a no-op, which is what makes a redelivered job safe. ``transport``
    exists for tests; production uses ``app.webhooks.delivery.deliver``.
    """
    moment = _now(now)
    if event.status != OutboxEventStatus.PENDING.value:
        return event.status

    subs = await matching_subscriptions(session, event)
    if not subs:
        # Nowhere to deliver is not a failure: the event is durably recorded
        # and closes as delivered. (Subscriptions created later receive
        # events published from then on — the outbox fans out at delivery
        # time, it does not retro-broadcast history.)
        event.status = OutboxEventStatus.DELIVERED.value
        event.delivered_at = moment
        event.last_error_category = "no_subscriptions"
        event.last_error = ""
        await session.flush()
        return event.status

    body = event_envelope(event)
    send = transport or deliver
    kinds: list[str] = []
    categories: list[str] = []
    for sub in subs:
        delivery, _created = await begin_delivery(
            session,
            tenant_id=event.tenant_id,
            subscription_id=sub.id,
            event_id=event.id,
            environment_id=event.environment_id,
        )
        if already_delivered(delivery):
            kinds.append("success")  # answered 2xx once; never re-sent
            continue
        try:
            secret = open_secret(event.tenant_id, sub.secret_envelope)
        except Exception as exc:
            delivery.attempt += 1
            delivery.status = "permanent_failure"
            delivery.completed_at = moment
            delivery.last_error_category = "secret_unavailable"
            kinds.append("permanent")
            categories.append("secret_unavailable")
            log.info(
                "outbox.secret_unavailable",
                event_id=str(event.id),
                subscription_id=str(sub.id),
                error=type(exc).__name__,
            )
            continue
        result: HttpResult = await send(
            url=sub.endpoint, body=body, secret=secret, event_id=str(event.id)
        )
        delivery.attempt += 1
        delivery.last_error_category = (result.category or "")[:64]
        if result.kind == "success":
            delivery.status = "succeeded"
            delivery.completed_at = moment
            delivery.next_attempt_at = None
            kinds.append("success")
        elif result.kind == "retryable":
            delivery.status = "queued"
            delivery.next_attempt_at = moment + timedelta(
                seconds=next_delivery_delay(delivery.attempt, key=str(event.id))
            )
            kinds.append("retryable")
            categories.append(result.category or "delivery_retryable")
        else:
            delivery.status = "permanent_failure"
            delivery.completed_at = moment
            kinds.append("permanent")
            categories.append(result.category or "delivery_permanent")

    if "permanent" in kinds:
        event.status = OutboxEventStatus.DEAD_LETTER.value
        event.last_error_category = categories[-1][:64] if categories else "delivery_permanent"
        event.last_error = f"permanent delivery failure: {event.last_error_category}"[:500]
    elif "retryable" in kinds:
        if is_exhausted(event.attempt_count, max_attempts=event.max_attempts):
            event.status = OutboxEventStatus.DEAD_LETTER.value
            event.last_error_category = "attempts_exhausted"
            event.last_error = "delivery attempt budget exhausted"
        else:
            event.available_at = moment + timedelta(
                seconds=next_delivery_delay(event.attempt_count, key=str(event.id))
            )
            event.last_error_category = categories[0][:64] if categories else "delivery_retryable"
            event.last_error = f"transient delivery failure: {event.last_error_category}"[:500]
    else:
        event.status = OutboxEventStatus.DELIVERED.value
        event.delivered_at = moment
        event.last_error_category = ""
        event.last_error = ""
    await session.flush()
    log.info(
        "outbox.round_complete",
        event_id=str(event.id),
        tenant_id=str(event.tenant_id),
        status=event.status,
        attempt=event.attempt_count,
    )
    return event.status


def make_outbox_handler(session_factory: async_sessionmaker[AsyncSession] | None = None):
    """Build the ``outbox.delivery`` handler bound to a session factory.

    Production registers the default (the application sessionmaker) below;
    tests and dedicated workers pass their own factory through
    ``JobWorker(handlers={...})`` without mutating the global registry.
    """

    async def handle_outbox_delivery(job: DurableJob) -> None:
        """Validate the persisted scope, then run one delivery round.

        The event must exist and belong to the job's tenant, and a job bound
        to an environment must not deliver an event bound to a different
        one; scope failures are permanent (a boundary is not a transient
        condition). The handler opens its own session — the worker's session
        stays single-threaded for the claim/ack writes and the heartbeat
        runs on its own sessions beside both.
        """
        raw = str((job.payload or {}).get("event_id") or "")
        try:
            event_id = uuid.UUID(raw)
        except (ValueError, AttributeError, TypeError):
            raise PermanentJobError(
                "invalid_payload", "outbox job payload lacks a valid event_id"
            )
        maker = session_factory or get_sessionmaker()
        async with maker() as session:
            event = await session.get(OutboxEvent, event_id)
            if event is None or event.tenant_id != job.tenant_id:
                # Missing and cross-tenant are the same answer: a permanent
                # failure with no detail, so a poisoned payload cannot probe
                # another tenant's event ids.
                raise PermanentJobError("event_not_found")
            if (
                job.environment_id is not None
                and event.environment_id is not None
                and job.environment_id != event.environment_id
            ):
                raise PermanentJobError("environment_mismatch")
            outcome = await deliver_event(session, event)
            await session.commit()
        if outcome == OutboxEventStatus.DEAD_LETTER.value:
            log.info("outbox.event_dead_letter", event_id=str(event_id), job_id=str(job.id))

    return handle_outbox_delivery


#: The registered production handler. Importing this module registers it —
#: the scheduler entrypoint imports it explicitly; nothing registers by
#: accident and no payload can ever name a different callable.
handle_outbox_delivery = make_outbox_handler()
register_handler_function(JobType.OUTBOX_DELIVERY, handle_outbox_delivery)
