"""
Background worker: fires appointment reminders and outbound campaign batches.

Run it as a second process next to the API:

    python -m scripts.scheduler

It is deliberately a separate process. If a campaign loop hangs, your phone
webhooks must keep answering -- a stuck scheduler should never make the
business miss an incoming call.
"""
from __future__ import annotations

import asyncio
import signal

import structlog
from sqlalchemy import select

from app.core import observability
from app.core.config import settings
from app.core.logging import log  # noqa: F401  (configures structlog)
from app.db.models import Campaign, Tenant
from app.db.session import get_sessionmaker
from app.billing.reconciliation import run_reconciliation_tick
from app.integrations.crm.service import run_sync_tick
from app.integrations.reminders import run_reminder_tick
from app.telephony.outbound import run_campaign_tick

logger = structlog.get_logger()

REMINDER_INTERVAL_SECONDS = 120
CAMPAIGN_INTERVAL_SECONDS = 60

_stop = asyncio.Event()

#: Ingestion polling. Short, because a tenant who just uploaded a price list
#: is watching the dashboard for it to turn READY.
KNOWLEDGE_INTERVAL_SECONDS = 15
KNOWLEDGE_BATCH_SIZE = 5


#: CRM delivery polling. Slower than knowledge ingestion because nobody is
#: watching a screen for it, and faster than the reminder loop because a lead
#: that reaches the CRM twenty minutes late is a lead the business called back
#: twenty minutes late.
CRM_INTERVAL_SECONDS = 20


#: Usage reconciliation. Hourly: it rebuilds a cache and reports
#: discrepancies, so running it more often buys nothing and running it less
#: often means a metering bug is invisible for a day.
BILLING_INTERVAL_SECONDS = 3600

#: Data retention. Daily: recordings and transcripts past CALL_RETENTION_DAYS
#: are purged (app/core/retention.py). A day is plenty; the window is large.
RETENTION_INTERVAL_SECONDS = 86400

#: Step 7 observability: how often to re-publish the stuck-side-effect gauge.
#: Read-only counts, so a minute is cheap and keeps the dashboard/alert current.
STUCK_SWEEP_INTERVAL_SECONDS = 60

#: Batch 07 durable job platform. The worker cycle is cheap when the queue is
#: empty (one claim query) and drains back-to-back while work exists, so the
#: interval only bounds the idle poll.
JOBS_INTERVAL_SECONDS = 10

#: Batch 07 outbox dispatch: turning due ``outbox_events`` rows into durable
#: delivery jobs. Delivery itself runs on the worker cycle above; this loop
#: only admits rounds, which is why a slightly slower cadence is fine.
OUTBOX_INTERVAL_SECONDS = 15


async def billing_reconciliation_loop() -> None:
    """
    Rebuild usage summaries and report discrepancies.

    Deliberately does **not** contact a billing provider. Requirement 37 keeps
    Stripe out of the voice path; this keeps it out of the periodic path too,
    so a Stripe outage cannot stall the worker that every other loop shares.
    Provider state is refreshed by webhooks and by an explicit reconcile
    request.
    """
    maker = get_sessionmaker()
    while not _stop.is_set():
        try:
            async with maker() as session:
                result = await run_reconciliation_tick(session)
            if result.get("discrepancies"):
                logger.warning("scheduler.billing_discrepancies", **result)
            elif result.get("tenants"):
                logger.info("scheduler.billing_reconciled", **result)
            observability.record_job_run("billing_reconciliation", ok=True)
        except Exception as exc:
            observability.record_job_run("billing_reconciliation", ok=False)
            logger.error("scheduler.billing_failed", error=str(exc))
        await _sleep(BILLING_INTERVAL_SECONDS)


async def crm_sync_loop() -> None:
    """
    Deliver queued CRM syncs.

    In the same process as the other loops, for the reason STEP 4 gave for
    keeping ingestion here: the product needs a background worker, not a
    distributed system, and requirement 14 explicitly says not to introduce a
    second job system.

    Each pass reaps syncs abandoned in PROCESSING by a worker that died, so a
    restart at the wrong moment cannot strand a lead forever.
    """
    maker = get_sessionmaker()
    while not _stop.is_set():
        try:
            async with maker() as session:
                result = await run_sync_tick(
                    session, limit=settings.crm_sync_batch_size
                )
            if result["attempted"]:
                logger.info("scheduler.crm_sync", **result)
            observability.record_job_run("crm_sync", ok=True)
        except Exception as exc:
            # The loop must survive anything. A CRM outage is routine; a
            # scheduler that exits because of one is not.
            observability.record_job_run("crm_sync", ok=False)
            logger.error("scheduler.crm_sync_failed", error=str(exc))
        await _sleep(settings.crm_sync_interval_seconds)


async def reminder_loop() -> None:
    maker = get_sessionmaker()
    while not _stop.is_set():
        try:
            async with maker() as session:
                result = await run_reminder_tick(session)
            if result["total"]:
                logger.info("scheduler.reminders", **result)
            observability.record_job_run("reminders", ok=True)
        except Exception as exc:
            observability.record_job_run("reminders", ok=False)
            logger.error("scheduler.reminder_failed", error=str(exc))
        await _sleep(REMINDER_INTERVAL_SECONDS)


async def campaign_loop() -> None:
    from datetime import datetime, timezone
    from app.db.enterprise_models import BatchCall, BatchStatus
    from app.telephony.outbound import sync_batch_from_calls

    maker = get_sessionmaker()
    while not _stop.is_set():
        try:
            async with maker() as session:
                now_utc = datetime.now(timezone.utc)
                due_batches = (
                    await session.execute(
                        select(BatchCall).where(
                            BatchCall.status == BatchStatus.SCHEDULED.value,
                            BatchCall.scheduled_at.is_not(None),
                            BatchCall.scheduled_at <= now_utc,
                        )
                    )
                ).scalars().all()
                for sb in due_batches:
                    sb.status = BatchStatus.RUNNING.value
                    sb.started_at = sb.started_at or now_utc
                    if sb.campaign_id:
                        sc = await session.get(Campaign, sb.campaign_id)
                        if sc is not None and sc.tenant_id == sb.tenant_id:
                            sc.is_active = True
                if due_batches:
                    await session.commit()

                campaigns = (await session.execute(
                    select(Campaign).where(Campaign.is_active.is_(True))
                )).scalars().all()
                for campaign in campaigns:
                    tenant = await session.get(Tenant, campaign.tenant_id)
                    if tenant is None or not tenant.outbound_enabled:
                        continue
                    result = await run_campaign_tick(session, tenant, campaign)
                    if result.get("dialed"):
                        logger.info("scheduler.campaign",
                                    campaign=campaign.name, **result)

                running_batches = (
                    await session.execute(
                        select(BatchCall).where(BatchCall.status == BatchStatus.RUNNING.value)
                    )
                ).scalars().all()
                for rb in running_batches:
                    await sync_batch_from_calls(session, rb)
                if running_batches:
                    await session.commit()
            observability.record_job_run("campaigns", ok=True)
        except Exception as exc:
            observability.record_job_run("campaigns", ok=False)
            logger.error("scheduler.campaign_failed", error=str(exc))
        await _sleep(CAMPAIGN_INTERVAL_SECONDS)


async def retention_loop() -> None:
    """Daily purge of calls/transcripts past the retention window, plus the
    webhook-replay receipts that only need to outlive their redelivery
    window."""
    from app.core.retention import prune_webhook_receipts, purge_expired_calls

    maker = get_sessionmaker()
    while not _stop.is_set():
        try:
            async with maker() as session:
                await purge_expired_calls(session)
                await prune_webhook_receipts(session)
            observability.record_job_run("retention", ok=True)
        except Exception as exc:
            observability.record_job_run("retention", ok=False)
            logger.error("scheduler.retention_failed", error=str(exc))
        await _sleep(RETENTION_INTERVAL_SECONDS)


async def knowledge_ingestion_loop() -> None:
    """
    Index documents that are waiting.

    Runs in the same process as the other loops for the same reason they do:
    the product needs a background worker, not a distributed system. Each pass
    also reaps documents abandoned in PROCESSING by a worker that died, so a
    restart at the wrong moment cannot strand a job forever.

    In the default "inline" ingest mode the API already indexes uploads in a
    background task, so this loop normally finds nothing and costs one query
    per interval. Setting knowledge_ingest_mode="worker" makes this the only
    path, which is what a production deployment should do -- an app process
    restarting mid-ingestion then loses nothing.
    """
    from app.knowledge.jobs import process_pending

    while not _stop.is_set():
        try:
            handled = await process_pending(limit=KNOWLEDGE_BATCH_SIZE)
            if handled:
                logger.info("scheduler.knowledge_indexed", documents=handled)
            observability.record_job_run("knowledge", ok=True)
        except Exception as exc:
            # Never let an ingestion problem kill the loop; the next pass
            # retries, and the document itself carries its own FAILED state.
            observability.record_job_run("knowledge", ok=False)
            logger.error("scheduler.knowledge_failed", error=str(exc)[:300])
        await _sleep(KNOWLEDGE_INTERVAL_SECONDS)


async def _sleep(seconds: int) -> None:
    """Sleep that wakes immediately on shutdown."""
    try:
        await asyncio.wait_for(_stop.wait(), timeout=seconds)
    except asyncio.TimeoutError:
        pass


async def stuck_sweep_loop() -> None:
    """Publish the stuck-side-effect gauge (Step 7 observability).

    Read-only: the *recovery* of a stuck row is the reapers' job
    (``crm.service.reap_stuck_syncs``, ``knowledge.ingest.reap_stuck_documents``).
    This loop only counts what is still stuck past its window and mirrors it
    into ``voxdesk_stuck_side_effects`` so an alert can fire before a customer
    notices a reminder or CRM write that never arrived.
    """
    maker = get_sessionmaker()
    while not _stop.is_set():
        try:
            async with maker() as session:
                counts = await observability.stuck_side_effect_counts(session)
            observability.set_stuck_side_effects(counts)
            if any(counts.values()):
                logger.warning("scheduler.stuck_side_effects", **counts)
        except Exception as exc:
            logger.error("scheduler.stuck_sweep_failed", error=str(exc))
        await _sleep(STUCK_SWEEP_INTERVAL_SECONDS)


async def durable_jobs_loop() -> None:
    """Batch 07: execute registered durable jobs (leases, heartbeats, DLQ).

    One ``JobWorker`` per process, cycling: reap abandoned leases → claim
    under database-counted concurrency limits → commit the lease → run the
    registered handler with a heartbeat → guarded ack/retry/DLQ. Every step
    is a conditional database operation, so running several schedulers (or
    restarting one mid-cycle) is safe: claims have exactly one winner, an
    interrupted cycle leaves a lease that expires, and expired leases are
    reclaimed by whichever instance reaps next. Nothing about the schedule
    or the queue lives in this process's memory.

    Only *registered* job types are claimed (``app.jobs.types`` registry —
    today ``outbox.delivery``; domains adopt the platform by registering
    handlers, not by being migrated here), so legacy receipt rows of
    unadopted types are never dragged into execution.
    """
    from app.jobs.worker import JobWorker
    from app.outbox import dispatcher as _outbox_dispatcher  # noqa: F401 (registers handler)

    worker = JobWorker(get_sessionmaker())
    while not _stop.is_set():
        job = None
        try:
            job = await worker.run_once()
            if job is not None:
                logger.info(
                    "scheduler.durable_job",
                    job_type=job.job_type,
                    status=job.status,
                    job_id=str(job.id),
                )
            observability.record_job_run("durable_jobs", ok=True)
        except Exception as exc:
            # The loop must survive anything: a poisoned cycle is a DLQ row,
            # not a dead scheduler.
            observability.record_job_run("durable_jobs", ok=False)
            logger.error("scheduler.durable_jobs_failed", error=str(exc)[:300])
        # Drain while work exists; idle polls wait the interval.
        await _sleep(1 if job is not None else JOBS_INTERVAL_SECONDS)


async def outbox_dispatch_loop() -> None:
    """Batch 07: admit due outbox events into the durable job system.

    Each pass CAS-claims one delivery round per due event and enqueues its
    ``outbox.delivery`` job in the same transaction — idempotent under
    repeated ticks and multiple instances (round ownership is a guarded
    update; the job key dedupes). Actual delivery, retries and dead-lettering
    belong to the worker cycle; this loop never sends HTTP itself.
    """
    from app.outbox.dispatcher import dispatch_due

    maker = get_sessionmaker()
    while not _stop.is_set():
        try:
            async with maker() as session:
                result = await dispatch_due(session)
                await session.commit()
            if result.get("scheduled") or result.get("exhausted"):
                logger.info("scheduler.outbox_dispatch", **result)
            observability.record_job_run("outbox_dispatch", ok=True)
        except Exception as exc:
            observability.record_job_run("outbox_dispatch", ok=False)
            logger.error("scheduler.outbox_dispatch_failed", error=str(exc)[:300])
        await _sleep(OUTBOX_INTERVAL_SECONDS)


def _start_metrics_server() -> None:
    """Serve /metrics for the scheduler's own Prometheus registry.

    The worker is a separate process from the API, so its job metrics
    (``voxdesk_job_runs_total``, ``voxdesk_job_last_success_timestamp_seconds``,
    ``voxdesk_stuck_side_effects``) are not visible through the API's scrape
    target. prometheus_client ships a tiny threaded HTTP server; binding it on
    a dedicated port gives Prometheus a second job to scrape with no web
    framework added. 0 (or unset) disables it.
    """
    port = settings.scheduler_metrics_port
    if port and port > 0:
        from prometheus_client import start_http_server

        start_http_server(port)
        logger.info("scheduler.metrics_server", port=port)


async def main() -> None:
    from app.core.graceful_shutdown import begin_drain, drain_and_shutdown, reset_drain_state

    reset_drain_state()
    loop = asyncio.get_running_loop()

    def _on_signal() -> None:
        begin_drain(reason="scheduler_signal")
        _stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _on_signal)

    _start_metrics_server()
    logger.info("scheduler.started")
    await asyncio.gather(
        reminder_loop(), campaign_loop(), knowledge_ingestion_loop(),
        crm_sync_loop(), billing_reconciliation_loop(), retention_loop(),
        stuck_sweep_loop(),
        # Batch 07: the durable job platform and the outbox admission cycle.
        durable_jobs_loop(), outbox_dispatch_loop(),
    )
    await drain_and_shutdown(
        reason="scheduler_shutdown",
        drain_timeout_seconds=5.0,
        flush_outbox=True,
    )
    logger.info("scheduler.stopped")


if __name__ == "__main__":
    asyncio.run(main())