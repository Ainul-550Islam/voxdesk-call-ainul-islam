"""Idempotent post-call writeback ledger + transactional outbox enqueue + DLQ redrive + signed recording URLs."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import log
from app.db import models as _db_models  # noqa: F401
from app.db.integrations_v2_models import IntegrationConnection, WritebackLedger
from app.integrations_v2.core.errors import IntegrationV2Error, redact_secrets
from app.outbox.models import OutboxEvent, OutboxEventStatus
from app.outbox.publisher import publish as publish_outbox_event
from app.telephony import recording as telephony_recording

OUTBOX_EVENT_TYPE_INT2_WRITEBACK = "integrations_v2.call_writeback"


@dataclass(frozen=True)
class CallWritebackInput:
    """Canonical post-call writeback payload shared across Helpdesk + CRM v2 adapters."""

    call_id: str
    subject: str
    summary: str
    disposition: str = "completed"
    duration_seconds: int = 0
    direction: str = "inbound"
    caller_phone: str = ""
    caller_email: str = ""
    external_contact_id: str = ""
    external_ticket_id: str = ""
    recording_id: uuid.UUID | None = None
    recording_ttl_seconds: int = 900
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    extra: dict[str, Any] = field(default_factory=dict)


def compute_writeback_idempotency_key(
    *,
    connection_id: uuid.UUID | str,
    call_id: str,
    action: str,
    provider: str = "",
    entity: str = "call_log",
) -> str:
    tag = provider or str(connection_id)
    return f"int2wb:{tag}:{call_id}:{entity}:{action}"[:160]


def _payload_sha256(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def issue_signed_recording_url(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: str,
    recording_id: uuid.UUID | None = None,
    ttl_seconds: int = 900,
) -> str:
    """Resolve a tenant-owned ``CallRecording`` and mint a time-boxed signed playback URL."""
    cls = telephony_recording.CallRecording
    stmt = select(cls).where(cls.tenant_id == tenant_id)
    if recording_id is not None:
        stmt = stmt.where(cls.id == recording_id)
    else:
        try:
            call_uuid = uuid.UUID(str(call_id))
        except ValueError:
            return ""
        stmt = stmt.where(cls.call_id == call_uuid)
    stmt = stmt.order_by(cls.created_at.desc()).limit(1)
    rec = (await session.execute(stmt)).scalar_one_or_none()
    if rec is None or not rec.storage_key:
        return ""
    bounded_ttl = max(60, min(int(ttl_seconds), 900))
    grant = telephony_recording.issue(tenant_id, rec.id, ttl_seconds=bounded_ttl)
    base_cfg = getattr(settings, "public_base_url", "")
    base = (base_cfg if base_cfg else "https://app.voxdesk.ai").rstrip("/")
    return f"{base}/api/recordings/{rec.id}/stream?grant={grant.token}&exp={grant.expires_at}"


async def enqueue_call_writeback(
    session: AsyncSession,
    *,
    connection: IntegrationConnection,
    writeback_input: CallWritebackInput,
    action: str = "log_call",
    entity: str = "call_log",
) -> tuple[WritebackLedger, bool]:
    """Idempotently record a ``WritebackLedger`` row keyed by (call_id, provider, entity) + outbox."""
    tenant_id = connection.tenant_id
    idem_key = compute_writeback_idempotency_key(
        connection_id=connection.id,
        call_id=writeback_input.call_id,
        action=action,
        provider=connection.provider,
        entity=entity,
    )

    existing = (
        await session.execute(
            select(WritebackLedger).where(
                WritebackLedger.call_id == writeback_input.call_id[:120],
                WritebackLedger.provider == connection.provider,
                WritebackLedger.entity == entity[:64],
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = (
            await session.execute(
                select(WritebackLedger).where(
                    WritebackLedger.tenant_id == tenant_id,
                    WritebackLedger.idempotency_key == idem_key,
                )
            )
        ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    signed_url = await issue_signed_recording_url(
        session,
        tenant_id=tenant_id,
        call_id=writeback_input.call_id,
        recording_id=writeback_input.recording_id,
        ttl_seconds=writeback_input.recording_ttl_seconds,
    )

    payload: dict[str, Any] = {
        "call_id": writeback_input.call_id,
        "subject": writeback_input.subject,
        "summary": writeback_input.summary,
        "disposition": writeback_input.disposition,
        "duration_seconds": int(writeback_input.duration_seconds),
        "direction": writeback_input.direction,
        "caller_phone": writeback_input.caller_phone,
        "caller_email": writeback_input.caller_email,
        "external_contact_id": writeback_input.external_contact_id,
        "external_ticket_id": writeback_input.external_ticket_id,
        "occurred_at": writeback_input.occurred_at.isoformat(),
        "recording_url": signed_url,
    }
    payload_hash = _payload_sha256(payload)

    outbox_row, _ = await publish_outbox_event(
        session,
        tenant_id=tenant_id,
        event_type=OUTBOX_EVENT_TYPE_INT2_WRITEBACK,
        aggregate_type="call",
        aggregate_id=str(writeback_input.call_id)[:120],
        payload={
            "connection_id": str(connection.id),
            "provider": connection.provider,
            "entity": entity,
            "action": action,
            "writeback": payload,
        },
        idempotency_key=idem_key[:120],
    )

    ledger = WritebackLedger(
        tenant_id=tenant_id,
        connection_id=connection.id,
        call_id=writeback_input.call_id[:120],
        provider=connection.provider[:48],
        entity=entity[:64],
        action=action[:48],
        idempotency_key=idem_key,
        status="pending",
        payload_hash=payload_hash,
        outbox_event_id=str(outbox_row.id),
    )
    session.add(ledger)
    await session.flush()
    return ledger, True


async def execute_writeback_now(
    session: AsyncSession,
    *,
    connection: IntegrationConnection,
    writeback_input: CallWritebackInput,
    dispatcher: Callable[[dict[str, Any]], Awaitable[str]],
    action: str = "log_call",
    entity: str = "call_log",
) -> tuple[WritebackLedger, bool]:
    """Execute writeback immediately with strict ledger deduplication.

    Never marks ``succeeded`` unless ``dispatcher`` returned a non-empty external ID.
    """
    ledger, _ = await enqueue_call_writeback(
        session,
        connection=connection,
        writeback_input=writeback_input,
        action=action,
        entity=entity,
    )
    if ledger.status == "succeeded" and ledger.external_id:
        return ledger, False

    signed_url = await issue_signed_recording_url(
        session,
        tenant_id=connection.tenant_id,
        call_id=writeback_input.call_id,
        recording_id=writeback_input.recording_id,
        ttl_seconds=writeback_input.recording_ttl_seconds,
    )

    dispatch_payload: dict[str, Any] = {
        "call_id": writeback_input.call_id,
        "subject": writeback_input.subject,
        "summary": writeback_input.summary,
        "disposition": writeback_input.disposition,
        "duration_seconds": int(writeback_input.duration_seconds),
        "direction": writeback_input.direction,
        "caller_phone": writeback_input.caller_phone,
        "caller_email": writeback_input.caller_email,
        "external_contact_id": writeback_input.external_contact_id,
        "external_ticket_id": writeback_input.external_ticket_id,
        "occurred_at": writeback_input.occurred_at.isoformat(),
        "recording_url": signed_url,
        "idempotency_key": ledger.idempotency_key,
        **writeback_input.extra,
    }

    ledger.attempts += 1
    try:
        external_id = await dispatcher(dispatch_payload)
        if not external_id or not str(external_id).strip():
            raise IntegrationV2Error(
                f"{connection.provider} writeback returned empty external_id",
                provider=connection.provider,
            )
        ledger.external_id = str(external_id).strip()[:160]
        ledger.status = "succeeded"
        ledger.last_error = ""
        ledger.updated_at = datetime.now(timezone.utc)
        await session.flush()
        log.info(
            "int2.writeback.succeeded",
            provider=connection.provider,
            call_id=writeback_input.call_id,
            external_id=ledger.external_id,
        )
        return ledger, True
    except Exception as exc:
        ledger.status = "failed"
        ledger.last_error = redact_secrets(str(exc), max_len=480)
        ledger.updated_at = datetime.now(timezone.utc)
        await session.flush()
        raise


async def writeback_call_outcome_multi(
    session: AsyncSession,
    *,
    targets: list[tuple[IntegrationConnection, str, Callable[[dict[str, Any]], Awaitable[str]]]],
    writeback_input: CallWritebackInput,
) -> dict[str, WritebackLedger]:
    """Orchestrate writeback across multiple providers/entities, isolating partial failures in the ledger."""
    results: dict[str, WritebackLedger] = {}
    for conn, entity, dispatcher in targets:
        key = f"{conn.provider}:{entity}"
        try:
            ledger, _ = await execute_writeback_now(
                session,
                connection=conn,
                writeback_input=writeback_input,
                dispatcher=dispatcher,
                entity=entity,
            )
            results[key] = ledger
        except Exception:
            failed_row = (
                await session.execute(
                    select(WritebackLedger).where(
                        WritebackLedger.call_id == writeback_input.call_id[:120],
                        WritebackLedger.provider == conn.provider,
                        WritebackLedger.entity == entity[:64],
                    )
                )
            ).scalar_one()
            results[key] = failed_row
    return results


async def redrive_dead_letter_writeback(
    session: AsyncSession,
    *,
    ledger_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> WritebackLedger:
    """Re-queue a failed/dead-lettered writeback ledger entry and its associated OutboxEvent."""
    ledger = (
        await session.execute(
            select(WritebackLedger).where(
                WritebackLedger.id == ledger_id,
                WritebackLedger.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if ledger is None:
        raise IntegrationV2Error("Writeback ledger entry not found")
    if ledger.status == "succeeded":
        return ledger

    ledger.status = "pending"
    ledger.updated_at = datetime.now(timezone.utc)
    if ledger.outbox_event_id:
        try:
            ev_uuid = uuid.UUID(ledger.outbox_event_id)
            ev = await session.get(OutboxEvent, ev_uuid)
            if ev is not None and ev.tenant_id == tenant_id:
                ev.status = OutboxEventStatus.PENDING
                ev.attempt_count = 0
                ev.last_error = ""
        except ValueError:
            pass
    await session.flush()
    return ledger
