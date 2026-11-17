"""
Ingestion job dispatch.

There is no Celery here, and that is a decision rather than an omission. The
project already has a worker pattern -- `scripts/scheduler.py`, a plain
asyncio process with polling loops -- and introducing a broker, a result
backend and a second deployment unit to index a few PDFs would be
infrastructure the product does not need.

So this is a small abstraction with two implementations:

* **inline** (default, and correct for development): the work runs in a
  FastAPI `BackgroundTask` after the response is sent. The HTTP request is not
  blocked, and there is nothing extra to deploy.
* **worker**: the upload only writes the row; `scripts/scheduler.py` polls for
  UPLOADED documents and processes them. This is what production runs, because
  an app process restarting mid-ingestion should not lose the job.

Both paths call the same `process_document()`, so behaviour cannot diverge.
Choosing between them is `settings.knowledge_ingest_mode`.

Safety properties in both modes:

* Each job opens its **own** database session. Reusing the request's session
  in a background task is a use-after-close bug waiting to happen, and the
  request's transaction is already committed by then anyway.
* Jobs are idempotent. A document processed twice ends in the same state with
  the same chunks -- see the delete-then-insert in `ingest.process_document`.
* A job never raises into its caller. A failure becomes a FAILED document.
"""
from __future__ import annotations

import asyncio
import uuid

from app.core.config import settings
from app.core.logging import log
from app.core.metrics import record_side_effect

#: Documents currently being processed in this process. Stops a double-click
#: on "reindex" from running two overlapping jobs for the same document.
#: Process-local by design: the cross-process guarantee is the PROCESSING
#: status plus the unique constraint on (document_id, version, chunk_index),
#: not this set.
_in_flight: set[str] = set()


async def run_ingestion(document_id: uuid.UUID, *, reindex: bool = False) -> None:
    """
    Process one document in its own session and transaction.

    Safe to call from anywhere: a background task, the worker loop, or a test.
    """
    key = str(document_id)
    if key in _in_flight:
        log.info("knowledge.job_already_running", document_id=key)
        return
    _in_flight.add(key)

    try:
        from app.db.models import DocumentStatus, KnowledgeDocument
        from app.db.session import get_sessionmaker
        from app.knowledge.ingest import claim_uploaded_document, process_document

        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            document = await session.get(KnowledgeDocument, document_id)
            if document is None:
                log.warning("knowledge.job_document_missing", document_id=key)
                return
            if document.status == DocumentStatus.ARCHIVED:
                # Archived between enqueue and execution. Honour the archive.
                log.info("knowledge.job_skipped_archived", document_id=key)
                return

            # Step 6 (scale-compliance): claim atomically. Two workers that
            # both saw this document UPLOADED must not both extract and embed
            # it -- the second embedding pass is a real (paid) external side
            # effect. The conditional UPDATE lets exactly one worker win.
            if not await claim_uploaded_document(session, document_id):
                record_side_effect("knowledge_ingest", "duplicate")
                log.info("knowledge.job_claimed_by_another", document_id=key)
                return
            record_side_effect("knowledge_ingest", "attempt")

            await process_document(session, document, bump_version=reindex)
            if document.status is DocumentStatus.READY:
                record_side_effect("knowledge_ingest", "success")
            elif document.status is DocumentStatus.FAILED:
                record_side_effect("knowledge_ingest", "failure")
    except Exception as exc:
        # The job must never take the worker or the request handler with it.
        log.error(
            "knowledge.job_crashed", document_id=key, error=str(exc)[:300]
        )
    finally:
        _in_flight.discard(key)


def enqueue_ingestion(background, document_id: uuid.UUID, *, reindex: bool = False) -> None:
    """
    Schedule ingestion for a document.

    `background` is the FastAPI `BackgroundTasks` from the request. In worker
    mode it is ignored entirely and the scheduler picks the document up from
    its UPLOADED status instead.
    """
    mode = (getattr(settings, "knowledge_ingest_mode", "inline") or "inline").lower()

    if mode == "worker":
        log.info("knowledge.job_queued_for_worker", document_id=str(document_id))
        return

    if background is not None:
        background.add_task(run_ingestion, document_id, reindex=reindex)
    else:
        # No request context (a script, a test). Fire and forget on the
        # running loop, or run it synchronously if there isn't one.
        try:
            asyncio.get_running_loop().create_task(
                run_ingestion(document_id, reindex=reindex)
            )
        except RuntimeError:
            asyncio.run(run_ingestion(document_id, reindex=reindex))


async def process_pending(limit: int = 5) -> int:
    """
    One pass of the worker loop: reap stuck jobs, then process what's waiting.

    Returns how many documents were handled, so the caller can back off when
    there is nothing to do.
    """
    from app.db.session import get_sessionmaker
    from app.knowledge.ingest import pending_documents, reap_stuck_documents

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        # Before claiming new work, release anything a dead worker abandoned.
        await reap_stuck_documents(
            session,
            older_than_seconds=getattr(
                settings, "knowledge_processing_timeout_seconds", 900
            ),
        )
        documents = await pending_documents(session, limit=limit)
        ids = [document.id for document in documents]

    for document_id in ids:
        await run_ingestion(document_id)
    return len(ids)


# ---------------------------------------------------------------------------
# PART 14 / P14-08 -- diff-aware re-crawl.
#
# `plan_recrawl` (app.knowledge.sitemap) decides *what* to fetch; this decides
# *how* to apply it: a page that vanished is archived, not purged, so an
# operator can see what a bad sitemap would have deleted before it is gone.
# ---------------------------------------------------------------------------


def recrawl_summary(plan: list[dict]) -> dict[str, int]:
    """Counts by action, for the sync report the dashboard shows."""
    counts: dict[str, int] = {"ingest": 0, "refetch": 0, "delete": 0}
    for item in plan:
        action = str(item.get("action") or "")
        if action in counts:
            counts[action] += 1
    counts["total"] = sum(counts.values())
    return counts


async def apply_recrawl(
    *,
    plan: list[dict],
    fetch: "callable",
    ingest: "callable",
    archive: "callable",
    limit: int = 100,
) -> dict:
    """Apply a re-crawl plan. One failing page never aborts the run.

    `fetch`, `ingest` and `archive` are injected so this is testable without a
    network and without a database, and so a dry run can pass no-op callables.
    """
    applied = {"ingested": 0, "refetched": 0, "archived": 0, "failed": []}
    for item in plan[:limit]:
        url = str(item.get("url") or "")
        action = str(item.get("action") or "")
        try:
            if action == "delete":
                await _maybe_await(archive(url))
                applied["archived"] += 1
            elif action in ("ingest", "refetch"):
                body = await _maybe_await(fetch(url))
                await _maybe_await(ingest(url, body))
                applied["ingested" if action == "ingest" else "refetched"] += 1
        except Exception as exc:  # a single 404 must not stop a 400-page crawl
            applied["failed"].append({"url": url, "error": str(exc)[:200]})
    return applied


async def _maybe_await(value):
    import inspect

    if inspect.isawaitable(value):
        return await value
    return value
