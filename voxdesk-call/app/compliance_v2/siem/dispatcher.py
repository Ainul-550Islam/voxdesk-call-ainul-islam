"""Cursor-driven delivery of audit rows to every configured SIEM sink.

Why a cursor rather than the outbox: the outbox carries *business* events that a
producer publishes inside its own transaction. Audit rows are written by
:mod:`app.audit.service` from dozens of call sites, some of them before any
business row exists (a denied login has no business transaction). Projecting
them through the outbox would mean either editing every call site or inventing
a second producer path. A per-sink read cursor over the existing ``audit_logs``
table is the honest equivalent: it is monotone, restart-safe, and shared by
every replica because it lives in the database.

Delivery contract, stated precisely:

* the cursor is a ``(created_at, id)`` watermark, advanced **only** past events
  that were accepted or permanently dead-lettered — never past a retryable one;
* a batch that fails retryably leaves the watermark untouched and schedules the
  next attempt with exponential backoff, so a crash mid-batch replays the whole
  batch rather than skipping it;
* a permanently rejected event is written to the dead-letter table *and* the
  batch advances past it, which keeps one poison record from wedging the
  pipeline forever;
* the circuit breaker is per sink and lives in the cursor row: after
  ``SIEM_CIRCUIT_THRESHOLD`` consecutive failures the sink is skipped until its
  cooldown elapses, so an unreachable destination costs one connection attempt
  per cooldown instead of one per tick.

Every sink therefore sees every audit row at least once, and the cursor for a
given row advances exactly once.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

from prometheus_client import Counter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.compliance_v2.settings import ComplianceV2Settings, get_compliance_v2_settings
from app.compliance_v2.siem.events import (
    from_audit_log,
    is_after_watermark,
    watermark_of,
)
from app.compliance_v2.siem.sinks_base import (
    CATEGORY_NOT_CONFIGURED,
    SiemSink,
    SinkError,
    SinkResult,
)
from app.db.compliance_v2_models import SiemCursor, SiemDeadLetter, SiemSink as SiemSinkRow
from app.db.models import AuditLog

SIEM_EVENTS = Counter(
    "siem_events_total",
    "Audit events handed to a SIEM sink, by sink kind and outcome",
    ["sink", "outcome"],
)
SIEM_BATCHES = Counter(
    "siem_batches_total",
    "SIEM delivery attempts, by sink kind and outcome",
    ["sink", "outcome"],
)


def _utcnow(moment: dt.datetime | None = None) -> dt.datetime:
    current = moment or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=dt.timezone.utc)
    return current.astimezone(dt.timezone.utc)


def backoff_seconds(attempt: int, settings: ComplianceV2Settings) -> int:
    """Exponential backoff with the configured ceiling."""
    exponent = max(0, attempt - 1)
    return min(settings.siem_retry_base_seconds * (2**exponent), settings.siem_retry_max_seconds)


def build_sink(row: SiemSinkRow, settings: ComplianceV2Settings) -> SiemSink:
    """Construct the transport for one stored sink definition.

    Secrets are opened from the existing encrypted secret store at delivery
    time, never stored on the row and never cached across ticks.
    """
    from app.compliance_v2.siem.sink_datadog import DatadogLogsSink
    from app.compliance_v2.siem.sink_elastic import ElasticBulkSink
    from app.compliance_v2.siem.sink_s3_jsonl import S3JsonlSink
    from app.compliance_v2.siem.sink_splunk_hec import SplunkHecSink
    from app.compliance_v2.siem.sink_syslog_cef import SyslogCefSink

    config: dict[str, Any] = dict(row.config or {})
    secret = open_sink_secret(row) if row.secret_ref else ""
    timeout = float(config.get("timeout_seconds") or settings.siem_http_timeout_seconds)

    if row.kind == "splunk_hec":
        return SplunkHecSink(
            endpoint=row.endpoint,
            token=secret,
            index=str(config.get("index") or ""),
            sourcetype=str(config.get("sourcetype") or "voxdesk:audit"),
            channel=str(config.get("channel") or settings.siem_splunk_channel),
            timeout=timeout,
        )
    if row.kind == "datadog_logs":
        return DatadogLogsSink(
            api_key=secret,
            site=str(config.get("site") or "us1"),
            endpoint=str(config.get("intake_url") or ""),
            service=str(config.get("service") or "voxdesk"),
            tags=str(config.get("tags") or ""),
            timeout=timeout,
        )
    if row.kind == "elastic_bulk":
        return ElasticBulkSink(
            endpoint=row.endpoint,
            index=str(config.get("index") or "voxdesk-audit"),
            api_key=secret if config.get("auth") != "basic" else "",
            username=str(config.get("username") or ""),
            password=secret if config.get("auth") == "basic" else "",
            timeout=timeout,
        )
    if row.kind == "syslog_cef":
        return SyslogCefSink(
            host=row.endpoint,
            port=int(config.get("port") or 6514),
            use_tls=bool(config.get("tls", True)),
            ca_file=str(config.get("ca_file") or ""),
            timeout=timeout,
        )
    if row.kind == "s3_jsonl":
        return S3JsonlSink(
            bucket=row.endpoint,
            prefix=str(config.get("prefix") or "audit"),
            region=str(config.get("region") or ""),
            kms_key_id=str(config.get("kms_key_id") or ""),
            object_lock_mode=str(config.get("object_lock_mode") or ""),
            object_lock_days=int(config.get("object_lock_days") or 0),
            endpoint_url=str(config.get("endpoint_url") or ""),
        )
    raise SinkError(f"unknown sink kind {row.kind!r}")


def open_sink_secret(row: SiemSinkRow) -> str:
    """Open a sink's stored credential reference through the secret store."""
    from app.security.secret_store import get_secret_store

    return get_secret_store().get(str(row.tenant_id), "siem_sink", row.secret_ref)


def seal_sink_secret(tenant_id: uuid.UUID, value: str) -> str:
    """Store a sink credential and return its opaque reference."""
    from app.security.secret_store import get_secret_store

    if not value:
        raise ValueError("refusing to store an empty sink credential")
    return get_secret_store().put(str(tenant_id), "siem_sink", value)


async def _pending_events(
    session: AsyncSession, cursor: SiemCursor, limit: int
) -> list[AuditLog]:
    """Rows after the cursor watermark, ordered by the watermark tuple."""
    stmt = select(AuditLog).where(AuditLog.tenant_id == cursor.tenant_id)
    if cursor.watermark_created_at is not None:
        stmt = stmt.where(AuditLog.created_at >= _utcnow(cursor.watermark_created_at))
    stmt = stmt.order_by(AuditLog.created_at, AuditLog.id).limit(max(1, limit) * 4)
    rows = list((await session.execute(stmt)).scalars())
    fresh = [
        row
        for row in rows
        if is_after_watermark(
            row.created_at,
            row.id,
            watermark_at=cursor.watermark_created_at,
            watermark_id=cursor.watermark_id,
        )
    ]
    return fresh[: max(1, limit)]


async def ensure_cursor(session: AsyncSession, sink_row: SiemSinkRow) -> SiemCursor:
    """Return the sink's cursor, creating it on first use (idempotent)."""
    cursor = await session.scalar(
        select(SiemCursor).where(SiemCursor.sink_id == sink_row.id)
    )
    if cursor is not None:
        return cursor
    cursor = SiemCursor(tenant_id=sink_row.tenant_id, sink_id=sink_row.id, state="idle")
    session.add(cursor)
    await session.flush()
    return cursor


@dataclass(frozen=True)
class SinkTickResult:
    sink_id: str
    kind: str
    outcome: str
    delivered: int = 0
    dead_lettered: int = 0
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "sink_id": self.sink_id,
            "kind": self.kind,
            "outcome": self.outcome,
            "delivered": self.delivered,
            "dead_lettered": self.dead_lettered,
            "message": self.message,
        }


async def dispatch_sink(
    session: AsyncSession,
    sink_row: SiemSinkRow,
    *,
    now: dt.datetime | None = None,
    settings: ComplianceV2Settings | None = None,
    sink: SiemSink | None = None,
) -> SinkTickResult:
    """Deliver one batch to one sink and update its cursor.

    Flushes; the caller commits. ``sink`` may be injected by tests; production
    callers let :func:`build_sink` construct it.
    """
    cfg = settings or get_compliance_v2_settings()
    moment = _utcnow(now)
    cursor = await ensure_cursor(session, sink_row)
    kind = sink_row.kind

    if not sink_row.enabled:
        return SinkTickResult(str(sink_row.id), kind, "disabled")
    if cursor.circuit_open_until is not None and _utcnow(cursor.circuit_open_until) > moment:
        return SinkTickResult(str(sink_row.id), kind, "circuit_open", message=cursor.last_error)
    if cursor.next_attempt_at is not None and _utcnow(cursor.next_attempt_at) > moment:
        return SinkTickResult(str(sink_row.id), kind, "backoff", message=cursor.last_error)

    batch = await _pending_events(session, cursor, int(sink_row.batch_size or cfg.siem_batch_size))
    if not batch:
        cursor.state = "idle"
        return SinkTickResult(str(sink_row.id), kind, "idle")

    events = [from_audit_log(row) for row in batch]
    try:
        active_sink = sink or build_sink(sink_row, cfg)
    except Exception as exc:  # noqa: BLE001 - construction failures are configuration failures
        cursor.consecutive_failures = int(cursor.consecutive_failures) + 1
        cursor.state = "circuit_open"
        cursor.circuit_open_until = moment + dt.timedelta(seconds=cfg.siem_circuit_cooldown_seconds)
        cursor.last_error = f"{type(exc).__name__}: {exc}"[:500]
        SIEM_BATCHES.labels(sink=kind, outcome="not_configured").inc()
        return SinkTickResult(
            str(sink_row.id), kind, CATEGORY_NOT_CONFIGURED, message=cursor.last_error
        )

    try:
        result = await active_sink.send_batch(events)
    except SinkError as exc:
        result = SinkResult.retry(exc.category, str(exc))
    except Exception as exc:  # noqa: BLE001 - a transport library may raise anything
        result = SinkResult.retry("transport", f"{type(exc).__name__}: {exc}")
    finally:
        await active_sink.aclose()

    cursor.attempts = int(cursor.attempts) + 1

    if result.retryable:
        cursor.consecutive_failures = int(cursor.consecutive_failures) + 1
        cursor.last_error = (result.message or result.category)[:500]
        cursor.state = "retrying"
        cursor.next_attempt_at = moment + dt.timedelta(
            seconds=backoff_seconds(int(cursor.consecutive_failures), cfg)
        )
        if int(cursor.consecutive_failures) >= cfg.siem_circuit_threshold:
            cursor.state = "circuit_open"
            cursor.circuit_open_until = moment + dt.timedelta(
                seconds=cfg.siem_circuit_cooldown_seconds
            )
        SIEM_BATCHES.labels(sink=kind, outcome="retryable").inc()
        SIEM_EVENTS.labels(sink=kind, outcome="deferred").inc(len(events))
        log.warning(
            "compliance.siem.retry",
            sink=str(sink_row.id),
            kind=kind,
            category=result.category,
            failures=int(cursor.consecutive_failures),
        )
        return SinkTickResult(
            str(sink_row.id), kind, "retryable", message=cursor.last_error
        )

    # Accepted (possibly with individually rejected events).
    dead_lettered = 0
    permanent = set(result.permanent_event_ids)
    if permanent:
        for event, row in zip(events, batch):
            if event.event_id not in permanent:
                continue
            session.add(
                SiemDeadLetter(
                    tenant_id=sink_row.tenant_id,
                    sink_id=sink_row.id,
                    audit_id=row.id,
                    payload=event.as_dict(),
                    error_category=result.category,
                    error_message=(result.message or "rejected")[:500],
                    attempts=int(cursor.attempts),
                )
            )
            dead_lettered += 1

    last_row = batch[-1]
    watermark_at, watermark_id = watermark_of(last_row)
    cursor.watermark_created_at = watermark_at
    cursor.watermark_id = watermark_id
    cursor.events_delivered = int(cursor.events_delivered) + result.accepted
    cursor.consecutive_failures = 0
    cursor.state = "dead_lettered" if permanent and not result.accepted else "idle"
    cursor.circuit_open_until = None
    cursor.next_attempt_at = None
    cursor.last_error = "" if not permanent else f"{len(permanent)} event(s) dead-lettered"
    cursor.last_success_at = moment

    SIEM_BATCHES.labels(sink=kind, outcome="delivered").inc()
    SIEM_EVENTS.labels(sink=kind, outcome="delivered").inc(result.accepted)
    if dead_lettered:
        SIEM_EVENTS.labels(sink=kind, outcome="dead_lettered").inc(dead_lettered)
    log.info(
        "compliance.siem.delivered",
        sink=str(sink_row.id),
        kind=kind,
        accepted=result.accepted,
        dead_lettered=dead_lettered,
    )
    return SinkTickResult(
        str(sink_row.id),
        kind,
        "delivered",
        delivered=result.accepted,
        dead_lettered=dead_lettered,
    )


async def dispatch_tick(
    session: AsyncSession,
    *,
    now: dt.datetime | None = None,
    tenant_id: uuid.UUID | None = None,
    limit_sinks: int = 50,
    settings: ComplianceV2Settings | None = None,
) -> dict[str, Any]:
    """One pass over every enabled sink. Idempotent and safe to run anywhere."""
    cfg = settings or get_compliance_v2_settings()
    if not cfg.siem_enabled:
        return {"sinks": 0, "delivered": 0, "results": [], "disabled": True}

    stmt = select(SiemSinkRow).where(SiemSinkRow.enabled.is_(True))
    if tenant_id is not None:
        stmt = stmt.where(SiemSinkRow.tenant_id == tenant_id)
    sinks = list(
        (await session.execute(stmt.order_by(SiemSinkRow.created_at).limit(max(1, limit_sinks))))
        .scalars()
    )

    results: list[SinkTickResult] = []
    for sink_row in sinks:
        results.append(await dispatch_sink(session, sink_row, now=now, settings=cfg))

    return {
        "sinks": len(sinks),
        "delivered": sum(item.delivered for item in results),
        "dead_lettered": sum(item.dead_lettered for item in results),
        "results": [item.as_dict() for item in results],
    }


async def redrive_dead_letter(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    dead_letter_id: uuid.UUID,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Re-send one dead-lettered event on the next tick.

    The cursor is *rewound* to just before the event's position, which is what
    makes the redrive a real retry rather than a bookkeeping note: the event
    will be selected again by the next batch.
    """
    row = await session.scalar(
        select(SiemDeadLetter).where(
            SiemDeadLetter.id == dead_letter_id,
            SiemDeadLetter.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise LookupError("dead-lettered event not found")
    cursor = await session.scalar(select(SiemCursor).where(SiemCursor.sink_id == row.sink_id))
    audit = await session.get(AuditLog, row.audit_id)
    if audit is None:
        raise LookupError("the audit row this dead letter refers to no longer exists")
    if cursor is not None:
        cursor.watermark_created_at = _utcnow(audit.created_at) - dt.timedelta(microseconds=1)
        cursor.watermark_id = None
        cursor.next_attempt_at = None
        cursor.circuit_open_until = None
        cursor.state = "idle"
    row.redrive_count = int(row.redrive_count) + 1
    row.redriven_at = _utcnow(now)
    await session.flush()
    return {"dead_letter_id": str(row.id), "sink_id": str(row.sink_id), "redrive_count": row.redrive_count}


__all__ = [
    "SIEM_BATCHES",
    "SIEM_EVENTS",
    "SinkTickResult",
    "backoff_seconds",
    "build_sink",
    "dispatch_sink",
    "dispatch_tick",
    "ensure_cursor",
    "open_sink_secret",
    "redrive_dead_letter",
    "seal_sink_secret",
]
