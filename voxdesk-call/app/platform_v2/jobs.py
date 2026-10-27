"""Durable-job handlers for Batch 07 (workforce, white-label, mobile).

Registered at import time by :mod:`app.jobs.registry` (``HANDLER_MODULES``), which
means the worker executes these through the same claim/lease/dead-letter path as
everything else. There is no second scheduler, no ``asyncio.create_task`` in a
request, and no "cron did it" escape hatch.

Each handler:

1. opens its own session (the worker owns no transaction for a handler),
2. validates the payload, raising ``PermanentJobError`` for anything malformed so a
   bad row dead-letters instead of being retried forever,
3. calls the domain function, and commits only on success,
4. maps a domain refusal to the right retry class:
   * ``InsufficientHistory`` — permanent. More history appears with time, but this
     *payload* asked for a forecast from data that does not exist; retrying the same
     job at the same instant cannot help. The daily cycle enqueues a new one.
   * ``InfeasibleSchedule``/``SolverUnavailable`` — retryable; a solver timeout at
     03:00 is worth another attempt.
   * anything unexpected — retryable, so the failure is visible in the dead-letter
     queue after the attempt budget rather than lost.

A handler never returns a made-up success: it either performed the work or it
raised.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

import structlog

from app.db.session import get_sessionmaker
from app.jobs.models import PermanentJobError, RetryableJobError
from app.jobs.types import JobType, register_handler_function
from app.platform_v2.errors import (
    InfeasibleSchedule,
    InsufficientHistory,
    NotFound,
    PlatformV2Error,
    SolverUnavailable,
    ValidationFailed,
)

log = structlog.get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _payload(job: Any) -> dict[str, Any]:
    payload = getattr(job, "payload", None)
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise PermanentJobError("invalid_payload", "job payload must be an object")
    return payload


def _uuid_payload(payload: dict[str, Any], key: str, *, required: bool = True) -> uuid.UUID | None:
    raw = payload.get(key)
    if raw in (None, ""):
        if required:
            raise PermanentJobError("invalid_payload", f"{key} is required")
        return None
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise PermanentJobError("invalid_payload", f"{key} must be a UUID") from exc


def _tenant_id(job: Any) -> uuid.UUID:
    tenant = getattr(job, "tenant_id", None)
    if not tenant:
        raise PermanentJobError("invalid_job", "job has no tenant_id")
    return tenant


def _classify(exc: Exception) -> Exception:
    """Translate a domain error into the worker's retry vocabulary."""
    if isinstance(exc, InsufficientHistory):
        return PermanentJobError("insufficient_history", exc.message)
    if isinstance(exc, NotFound):
        return PermanentJobError("not_found", exc.message)
    if isinstance(exc, ValidationFailed):
        return PermanentJobError("invalid_payload", exc.message)
    if isinstance(exc, (InfeasibleSchedule, SolverUnavailable)):
        return RetryableJobError(str(exc))
    if isinstance(exc, PlatformV2Error):
        return RetryableJobError(exc.message)
    return RetryableJobError(f"{type(exc).__name__}: {exc}")


# ----------------------------------------------------------------- rollups ---
async def handle_history_rollup(job: Any) -> dict[str, Any]:
    """Rebuild interval history for one environment."""
    from app.platform_v2.wfm import jobs as wfm_jobs

    payload = _payload(job)
    tenant_id = _tenant_id(job)
    environment_id = _uuid_payload(payload, "environment_id")
    days = int(payload.get("days") or 2)
    end_date = payload.get("end_date")
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            result = await wfm_jobs.rollup(
                session,
                tenant_id=tenant_id,
                environment_id=environment_id,  # type: ignore[arg-type]
                days=max(1, min(days, 90)),
                end_date=date.fromisoformat(end_date) if end_date else None,
                zone_name=payload.get("zone"),
                interval_minutes=payload.get("interval_minutes"),
            )
            await session.commit()
        except PlatformV2Error as exc:
            await session.rollback()
            raise _classify(exc) from exc
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            raise _classify(exc) from exc
    log.info("platform_v2.job.rollup_done", environment_id=str(environment_id), **{
        key: value for key, value in result.items() if isinstance(value, (int, float, str))
    })
    return result


# ---------------------------------------------------------------- forecasts ---
async def handle_forecast_generate(job: Any) -> dict[str, Any]:
    """Generate a forecast for one environment/queue."""
    from app.platform_v2.wfm import jobs as wfm_jobs

    payload = _payload(job)
    tenant_id = _tenant_id(job)
    environment_id = _uuid_payload(payload, "environment_id")
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            forecast = await wfm_jobs.generate_forecast(
                session,
                tenant_id=tenant_id,
                environment_id=environment_id,  # type: ignore[arg-type]
                queue_key=str(payload.get("queue_key") or ""),
                start_date=date.fromisoformat(payload["start_date"]) if payload.get("start_date") else None,
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            raise _classify(exc) from exc
        return {
            "forecast_id": str(forecast.id),
            "queue_key": forecast.queue_key,
            "horizon_days": forecast.horizon_days,
        }


async def handle_forecast_score(job: Any) -> dict[str, Any]:
    """Score forecasts whose horizon has partly elapsed."""
    from app.platform_v2.wfm import jobs as wfm_jobs

    payload = _payload(job)
    tenant_id = _tenant_id(job)
    environment_id = _uuid_payload(payload, "environment_id")
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            result = await wfm_jobs.score_due_forecasts(
                session,
                tenant_id=tenant_id,
                environment_id=environment_id,  # type: ignore[arg-type]
                queue_key=payload.get("queue_key"),
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            raise _classify(exc) from exc
        return {"scored": len(result.get("scored", [])), "skipped": len(result.get("skipped", []))}


# --------------------------------------------------------------- adherence ---
async def handle_adherence_sweep(job: Any) -> dict[str, Any]:
    """Evaluate a day's shifts against the agent-state ledger."""
    from app.platform_v2.wfm import jobs as wfm_jobs

    payload = _payload(job)
    tenant_id = _tenant_id(job)
    environment_id = _uuid_payload(payload, "environment_id")
    day = payload.get("day")
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            result = await wfm_jobs.adherence_sweep(
                session,
                tenant_id=tenant_id,
                environment_id=environment_id,  # type: ignore[arg-type]
                day=date.fromisoformat(day) if day else None,
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            raise _classify(exc) from exc
        return {
            "day": result.get("day"),
            "shifts": result.get("shifts"),
            "exceptions": result.get("exceptions"),
        }


# ----------------------------------------------------------------- publish ---
async def handle_schedule_publish(job: Any) -> dict[str, Any]:
    """Publish a schedule version and queue a rota notice for each affected person.

    The notice is *enqueued*, not sent here: a publish that happens at 09:00 must
    not block on FCM, and a push that fails must be retried by the durable queue
    rather than lost in a publish transaction.
    """
    from app.db.platform_v2_models import WfmSchedule
    from app.jobs.queue import enqueue
    from app.platform_v2.wfm import publish as publish_mod

    payload = _payload(job)
    tenant_id = _tenant_id(job)
    schedule_id = _uuid_payload(payload, "schedule_id")
    version_id = _uuid_payload(payload, "version_id", required=False)
    notify = bool(payload.get("notify", True))
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            schedule = await session.get(WfmSchedule, schedule_id)
            if schedule is None or schedule.tenant_id != tenant_id:
                raise PermanentJobError("not_found", "schedule not found")
            version = (
                await publish_mod.version_by_id(session, tenant_id=tenant_id, version_id=version_id)
                if version_id is not None
                else await publish_mod.current_version(session, schedule=schedule)
            )
            if version is None:
                raise PermanentJobError("invalid_payload", "schedule has no version to publish")
            publisher = payload.get("published_by_user_id") or version.created_by_user_id or schedule.created_by_user_id
            if not publisher:
                raise PermanentJobError(
                    "invalid_payload",
                    "published_by_user_id is required when neither the version nor the schedule records an author",
                )
            result = await publish_mod.publish_version(
                session,
                schedule=schedule,
                version=version,
                published_by_user_id=uuid.UUID(str(publisher)),
                commit=False,
            )
            queued = 0
            if notify:
                for notice in result["notifications"]:
                    created = await enqueue(
                        session,
                        tenant_id=tenant_id,
                        job_type=JobType.MOBILE_PUSH_RETRY,
                        idempotency_key=f"wfm.publish:{version.id}:{notice['user_id']}",
                        environment_id=version.environment_id,
                        payload={
                            "user_id": notice["user_id"],
                            "template": "rota_published",
                            "version_id": str(version.id),
                            "title": "Your rota has changed",
                            "body": "Open VoxDesk to see your updated schedule.",
                        },
                    )
                    queued += 1 if created else 0
            await session.commit()
        except PermanentJobError:
            await session.rollback()
            raise
        except PlatformV2Error as exc:
            await session.rollback()
            raise _classify(exc) from exc
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            raise _classify(exc) from exc
        return {
            "schedule_id": str(schedule_id),
            "version_id": result["version_id"],
            "version_number": result["version_number"],
            "changed_people": len(result["notifications"]),
            "push_jobs_queued": queued,
        }


# ------------------------------------------------------------- white-label ---
async def handle_domain_reverify(job: Any) -> dict[str, Any]:
    from app.platform_v2.whitelabel import domains as domains_mod

    payload = _payload(job)
    tenant_id = _tenant_id(job)
    domain_id = _uuid_payload(payload, "domain_id")
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            outcome = await domains_mod.verify(
                session, tenant_id=tenant_id, domain_id=domain_id  # type: ignore[arg-type]
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            raise _classify(exc) from exc
        return {"domain_id": str(domain_id), "passed": outcome.passed, "status": outcome.status}


async def handle_cert_refresh(job: Any) -> dict[str, Any]:
    from app.platform_v2.whitelabel import tls as tls_mod

    payload = _payload(job)
    tenant_id = _tenant_id(job)
    domain_id = _uuid_payload(payload, "domain_id")
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            result = await tls_mod.refresh_certificate(
                session, tenant_id=tenant_id, domain_id=domain_id  # type: ignore[arg-type]
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            raise _classify(exc) from exc
        return {
            "domain_id": str(domain_id),
            "state": result.get("state"),
            "activated": result.get("activated"),
        }


async def handle_sender_domain_check(job: Any) -> dict[str, Any]:
    from app.platform_v2.whitelabel import email_domains as email_mod

    payload = _payload(job)
    tenant_id = _tenant_id(job)
    sender_id = _uuid_payload(payload, "sender_id")
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            result = await email_mod.verify(session, tenant_id=tenant_id, sender_id=sender_id)  # type: ignore[arg-type]
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            raise _classify(exc) from exc
        return {"sender_id": str(sender_id), "verified": result["verified"], "status": result["status"]}


# ------------------------------------------------------------------- mobile ---
async def handle_push_retry(job: Any) -> dict[str, Any]:
    """Retry transient push failures, or deliver one specific pending notice."""
    from app.platform_v2.mobile import push as push_mod

    payload = _payload(job)
    tenant_id = _tenant_id(job)
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            user_id = payload.get("user_id")
            if user_id:
                reports = await push_mod.send_to_user(
                    session,
                    tenant_id=tenant_id,
                    user_id=uuid.UUID(str(user_id)),
                    notification=push_mod.Notification(
                        template=str(payload.get("template") or "rota_published"),
                        title=str(payload.get("title") or "Your rota has changed"),
                        body=str(payload.get("body") or "Open VoxDesk to see your updated schedule."),
                        data={"version_id": str(payload.get("version_id") or "")},
                        collapse_key=f"wfm-{payload.get('version_id') or 'rota'}",
                    ),
                )
                await session.commit()
                return {"attempts": len(reports), "statuses": [report.status for report in reports]}
            result = await push_mod.retry_failed(session, tenant_id=tenant_id, limit=int(payload.get("limit") or 25))
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            raise _classify(exc) from exc
        return result


async def handle_token_gc(job: Any) -> dict[str, Any]:
    from app.platform_v2.mobile import devices as devices_mod

    payload = _payload(job)
    tenant_id = _tenant_id(job)
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            deactivated = await devices_mod.purge_stale(
                session,
                tenant_id=tenant_id,
                older_than_days=payload.get("older_than_days"),
                commit=False,
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            raise _classify(exc) from exc
        return {"deactivated": deactivated}


register_handler_function(JobType.WFM_HISTORY_ROLLUP, handle_history_rollup)
register_handler_function(JobType.WFM_FORECAST_GENERATE, handle_forecast_generate)
register_handler_function(JobType.WFM_FORECAST_SCORE, handle_forecast_score)
register_handler_function(JobType.WFM_ADHERENCE_SWEEP, handle_adherence_sweep)
register_handler_function(JobType.WFM_SCHEDULE_PUBLISH, handle_schedule_publish)
register_handler_function(JobType.WL_DOMAIN_REVERIFY, handle_domain_reverify)
register_handler_function(JobType.WL_CERT_REFRESH, handle_cert_refresh)
register_handler_function(JobType.WL_SENDER_DOMAIN_CHECK, handle_sender_domain_check)
register_handler_function(JobType.MOBILE_PUSH_RETRY, handle_push_retry)
register_handler_function(JobType.MOBILE_TOKEN_GC, handle_token_gc)


__all__ = [
    "CYCLES",
    "LOOP_NAMES",
    "adherence_cycle",
    "cert_refresh_cycle",
    "domain_reverify_cycle",
    "forecast_cycle",
    "forecast_score_cycle",
    "handle_adherence_sweep",
    "handle_cert_refresh",
    "handle_domain_reverify",
    "handle_forecast_generate",
    "handle_forecast_score",
    "handle_history_rollup",
    "handle_push_retry",
    "handle_schedule_publish",
    "handle_sender_domain_check",
    "handle_token_gc",
    "push_retry_cycle",
    "rollup_cycle",
    "sender_domain_cycle",
    "token_gc_cycle",
]


# ------------------------------------------------------------------ cycles ---
#: How often each pass runs. Chosen so nothing is hot: interval history only
#: changes when a bucket closes, so an hourly rewrite is enough; forecasting runs
#: once a day; the domain and certificate passes run often enough that a broken
#: domain is noticed the same day it breaks.
ROLLUP_INTERVAL_SECONDS = 3600.0
FORECAST_INTERVAL_SECONDS = 86_400.0
SCORE_INTERVAL_SECONDS = 6 * 3600.0
ADHERENCE_INTERVAL_SECONDS = 3600.0
DOMAIN_REVERIFY_INTERVAL_SECONDS = 1800.0
CERT_REFRESH_INTERVAL_SECONDS = 900.0
SENDER_CHECK_INTERVAL_SECONDS = 6 * 3600.0
PUSH_RETRY_INTERVAL_SECONDS = 300.0
TOKEN_GC_INTERVAL_SECONDS = 86_400.0

#: Every cycle name, in the order the scheduler registers them. Read by
#: ``tests/test_scheduler_wires_all_v2_jobs.py`` and by the Helm CronJobs, so a new
#: pass cannot be added without a tick and a schedule.
LOOP_NAMES = (
    "wfm_rollup",
    "wfm_forecast",
    "wfm_forecast_score",
    "wfm_adherence",
    "wl_domain_reverify",
    "wl_cert_refresh",
    "wl_sender_domain",
    "mobile_push_retry",
    "mobile_token_gc",
)


async def _tenant_environments(session: Any) -> list[tuple[uuid.UUID, uuid.UUID]]:
    """Every (tenant, environment) pair that owns operational data."""
    from sqlalchemy import select

    from app.db.models import Environment

    rows = await session.execute(
        select(Environment.tenant_id, Environment.id).where(Environment.status == "active")
    )
    return [(tenant_id, environment_id) for tenant_id, environment_id in rows.all()]


async def rollup_cycle(session: Any) -> dict[str, Any]:
    """Enqueue a history rollup for every active environment.

    Enqueued rather than executed inline: a rollup over a busy week is real work, and
    the scheduler loop must not be the process holding it.
    """
    from app.jobs.queue import enqueue

    stamp = _now().strftime("%Y%m%d%H")
    created = 0
    for tenant_id, environment_id in await _tenant_environments(session):
        _, fresh = await enqueue(
            session,
            tenant_id=tenant_id,
            job_type=JobType.WFM_HISTORY_ROLLUP,
            idempotency_key=f"wfm.rollup:{environment_id}:{stamp}",
            payload={"environment_id": str(environment_id), "days": 2},
            environment_id=environment_id,
        )
        created += 1 if fresh else 0
    await session.commit()
    return {"environments": created}


async def forecast_cycle(session: Any) -> dict[str, Any]:
    """Enqueue the daily forecast for every active environment."""
    from app.jobs.queue import enqueue

    today = _now().date().isoformat()
    created = 0
    for tenant_id, environment_id in await _tenant_environments(session):
        _, fresh = await enqueue(
            session,
            tenant_id=tenant_id,
            job_type=JobType.WFM_FORECAST_GENERATE,
            idempotency_key=f"wfm.forecast:{environment_id}:{today}",
            payload={"environment_id": str(environment_id)},
            environment_id=environment_id,
        )
        created += 1 if fresh else 0
    await session.commit()
    return {"environments": created}


async def forecast_score_cycle(session: Any) -> dict[str, Any]:
    """Score every forecast whose horizon has partly elapsed."""
    from app.platform_v2.wfm import jobs as wfm_jobs

    scored = 0
    for tenant_id, environment_id in await _tenant_environments(session):
        result = await wfm_jobs.score_due_forecasts(
            session, tenant_id=tenant_id, environment_id=environment_id, commit=False
        )
        scored += len(result.get("scored", []))
    await session.commit()
    return {"scored": scored}


async def adherence_cycle(session: Any) -> dict[str, Any]:
    """Evaluate yesterday's rota for every active environment."""
    from app.platform_v2.wfm import jobs as wfm_jobs

    totals = {"environments": 0, "exceptions": 0}
    for tenant_id, environment_id in await _tenant_environments(session):
        result = await wfm_jobs.adherence_sweep(
            session, tenant_id=tenant_id, environment_id=environment_id, persist=True, commit=False
        )
        totals["environments"] += 1
        # ``adherence_sweep`` reports the rows it created as ``exceptions_created``;
        # reading a bare "exceptions" key made this counter silently always zero.
        totals["exceptions"] += int(result.get("exceptions_created") or 0)
    await session.commit()
    return totals


async def domain_reverify_cycle(session: Any) -> dict[str, Any]:
    """Re-check every custom domain whose verification window has elapsed."""
    from app.platform_v2.whitelabel import domains as domains_mod

    due = await domains_mod.reverify_due(session, limit=25)
    checked = 0
    for row in due:
        await domains_mod.verify(session, tenant_id=row.tenant_id, domain_id=row.id)
        checked += 1
    await session.commit()
    return {"checked": checked}


async def cert_refresh_cycle(session: Any) -> dict[str, Any]:
    """Observe certificates for domains waiting on issuance."""
    from app.platform_v2.whitelabel import tls as tls_mod

    results = await tls_mod.sweep(session, limit=25)
    return {
        "checked": len(results),
        "activated": sum(1 for row in results if row.get("activated")),
    }


async def sender_domain_cycle(session: Any) -> dict[str, Any]:
    """Re-check sender domains that are not verified yet."""
    from sqlalchemy import select

    from app.db.platform_v2_models import EmailSenderDomain
    from app.platform_v2.whitelabel import email_domains as email_mod

    rows = (
        await session.execute(
            select(EmailSenderDomain).where(EmailSenderDomain.status.in_(("pending", "failed")))
        )
    ).scalars()
    checked = 0
    for row in rows:
        await email_mod.verify(session, tenant_id=row.tenant_id, sender_id=row.id)
        checked += 1
    await session.commit()
    return {"checked": checked}


async def push_retry_cycle(session: Any) -> dict[str, Any]:
    """Re-send notifications that failed for a transient reason."""
    from app.platform_v2.mobile import push as push_mod

    retried = 0
    sent = 0
    for tenant_id, _environment_id in await _tenant_environments(session):
        result = await push_mod.retry_failed(session, tenant_id=tenant_id, limit=25, commit=False)
        retried += int(result["attempted"])
        sent += int(result["sent"])
    await session.commit()
    return {"attempted": retried, "sent": sent}


async def token_gc_cycle(session: Any) -> dict[str, Any]:
    """Deactivate push tokens belonging to stale devices."""
    from app.platform_v2.mobile import devices as devices_mod

    deactivated = 0
    for tenant_id, _environment_id in await _tenant_environments(session):
        deactivated += await devices_mod.purge_stale(session, tenant_id=tenant_id, commit=False)
    await session.commit()
    return {"deactivated": deactivated}


#: Name -> coroutine, so the scheduler table, the one-shot runner and the tests
#: all read the same mapping.
CYCLES = {
    "wfm_rollup": rollup_cycle,
    "wfm_forecast": forecast_cycle,
    "wfm_forecast_score": forecast_score_cycle,
    "wfm_adherence": adherence_cycle,
    "wl_domain_reverify": domain_reverify_cycle,
    "wl_cert_refresh": cert_refresh_cycle,
    "wl_sender_domain": sender_domain_cycle,
    "mobile_push_retry": push_retry_cycle,
    "mobile_token_gc": token_gc_cycle,
}
