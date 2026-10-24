"""Scheduler entrypoints for Integrations v2: sync_tick, token_refresh_sweep, teams_subscription_renew, webhook_replay_gc, writeback_retry_tick."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.integrations_v2_models import (
    InboundWebhookEvent,
    IntegrationConnection,
    WritebackLedger,
)
from app.db.session import get_sessionmaker
from app.integrations_v2.collab.teams import TeamsProvider
from app.integrations_v2.core.connection_store import mark_connection_error
from app.integrations_v2.core.errors import AuthExpired, IntegrationV2Error, redact_secrets
from app.integrations_v2.core.http_client import Int2HttpClient
from app.integrations_v2.core.oauth2 import refresh_connection_token
from app.integrations_v2.core.provider_base import AuthKind, Capability
from app.integrations_v2.core.sync_engine import run_incremental_sync
from app.integrations_v2.core.writeback import OUTBOX_EVENT_TYPE_INT2_WRITEBACK
from app.integrations_v2.crm import CrmProviderV2
from app.integrations_v2.helpdesk.models import HelpdeskProviderV2
from app.integrations_v2.register_all import instantiate_provider_v2
from app.outbox.models import OutboxEvent, OutboxEventStatus

_JOB_LOCKS: dict[str, asyncio.Lock] = {}


def _job_lock(name: str) -> asyncio.Lock:
    lock = _JOB_LOCKS.get(name)
    if lock is None:
        lock = asyncio.Lock()
        _JOB_LOCKS[name] = lock
    return lock


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def token_refresh_sweep(
    session: AsyncSession,
    *,
    horizon_minutes: int = 10,
    http_client: Int2HttpClient | None = None,
) -> dict[str, int]:
    """Proactively refresh active OAuth2 connections whose access_token expires within ``horizon_minutes``."""
    async with _job_lock("int2:token_refresh_sweep"):
        cutoff = _now() + timedelta(minutes=horizon_minutes)
        stmt = select(IntegrationConnection).where(
            IntegrationConnection.status == "active",
            IntegrationConnection.auth_kind == AuthKind.OAUTH2.value,
            IntegrationConnection.token_expires_at.is_not(None),
            IntegrationConnection.token_expires_at <= cutoff,
        )
        rows = list((await session.execute(stmt)).scalars().all())

        refreshed = 0
        failed = 0
        for row in rows:
            if not row.refresh_secret_ref:
                continue
            try:
                await refresh_connection_token(
                    session, row, http_client=http_client, force=True
                )
                refreshed += 1
            except AuthExpired as exc:
                failed += 1
                await mark_connection_error(
                    session, row, error_message=str(exc), needs_reauth=True
                )
            except Exception as exc:
                failed += 1
                log.warning(
                    "int2.jobs.refresh_error",
                    connection_id=str(row.id),
                    provider=row.provider,
                    error=redact_secrets(str(exc), max_len=160),
                )
        await session.flush()
        return {"checked": len(rows), "refreshed": refreshed, "failed": failed}


async def _cap_pages(aiter, max_pages: int):
    idx = 0
    async for batch in aiter:
        idx += 1
        yield batch
        if idx >= max_pages:
            break


async def sync_tick(
    session: AsyncSession,
    *,
    http_client: Int2HttpClient | None = None,
    max_pages_per_connection: int = 5,
) -> dict[str, int]:
    """Run incremental sync across active Helpdesk and CRM v2 connections."""
    async with _job_lock("int2:sync_tick"):
        stmt = select(IntegrationConnection).where(IntegrationConnection.status == "active")
        rows = list((await session.execute(stmt)).scalars().all())

        runs_executed = 0
        records_upserted = 0
        for row in rows:
            cfg = dict(row.config_json or {})
            if cfg.get("sync_enabled") is False:
                continue
            provider = instantiate_provider_v2(row, http_client=http_client)
            if not provider.supports(Capability.INCREMENTAL_SYNC):
                continue

            try:
                if isinstance(provider, CrmProviderV2):
                    sync_run = await run_incremental_sync(
                        session,
                        connection=row,
                        entity_type="Contact",
                        page_stream_factory=lambda cur, p=provider: _cap_pages(
                            p.iter_updated_contacts(cur), max_pages_per_connection
                        ),
                    )
                    runs_executed += 1
                    records_upserted += sync_run.records_upserted
                elif isinstance(provider, HelpdeskProviderV2):
                    sync_run = await run_incremental_sync(
                        session,
                        connection=row,
                        entity_type="Ticket",
                        page_stream_factory=lambda cur, p=provider: _cap_pages(
                            p.iter_updated_tickets(cur), max_pages_per_connection
                        ),
                    )
                    runs_executed += 1
                    records_upserted += sync_run.records_upserted
            except Exception as exc:
                log.warning(
                    "int2.jobs.sync_error",
                    connection_id=str(row.id),
                    provider=row.provider,
                    error=redact_secrets(str(exc), max_len=200),
                )
        await session.flush()
        return {"runs_executed": runs_executed, "records_upserted": records_upserted}


async def teams_subscription_renew(
    session: AsyncSession,
    *,
    http_client: Int2HttpClient | None = None,
) -> dict[str, int]:
    """Renew Microsoft Graph change-notification subscriptions stored on active Teams connections."""
    async with _job_lock("int2:teams_subscription_renew"):
        stmt = select(IntegrationConnection).where(
            IntegrationConnection.provider == "teams",
            IntegrationConnection.status == "active",
        )
        rows = list((await session.execute(stmt)).scalars().all())
        renewed = 0
        failed = 0
        for row in rows:
            cfg = dict(row.config_json or {})
            sub_id = str(cfg.get("graph_subscription_id") or "").strip()
            if not sub_id:
                continue
            try:
                provider = instantiate_provider_v2(row, http_client=http_client)
                if isinstance(provider, TeamsProvider):
                    res = await provider.renew_subscription(sub_id, ttl_minutes=55)
                    cfg["graph_subscription_expires_at"] = res.get("expirationDateTime", "")
                    row.config_json = cfg
                    renewed += 1
            except Exception as exc:
                failed += 1
                log.warning(
                    "int2.jobs.teams_renew_failed",
                    connection_id=str(row.id),
                    error=redact_secrets(str(exc), max_len=160),
                )
        await session.flush()
        return {"checked": len(rows), "renewed": renewed, "failed": failed}


async def writeback_retry_tick(
    session: AsyncSession,
    *,
    batch_size: int = 25,
    http_client: Int2HttpClient | None = None,
) -> dict[str, int]:
    """Drain pending ``integrations_v2.call_writeback`` outbox events with ledger deduplication."""
    async with _job_lock("int2:writeback_retry_tick"):
        stmt = (
            select(OutboxEvent)
            .where(
                OutboxEvent.event_type == OUTBOX_EVENT_TYPE_INT2_WRITEBACK,
                OutboxEvent.status == OutboxEventStatus.PENDING,
            )
            .order_by(OutboxEvent.created_at.asc())
            .limit(batch_size)
        )
        events = list((await session.execute(stmt)).scalars().all())
        published = 0
        failed = 0

        for ev in events:
            payload = dict(ev.payload or {})
            conn_id_str = str(payload.get("connection_id") or "")
            wb_payload = dict(payload.get("writeback") or {})

            ledger = (
                await session.execute(
                    select(WritebackLedger).where(
                        WritebackLedger.outbox_event_id == str(ev.id)
                    )
                )
            ).scalar_one_or_none()
            if ledger is not None and ledger.status == "succeeded":
                ev.status = OutboxEventStatus.DELIVERED
                ev.delivered_at = _now()
                published += 1
                continue

            try:
                conn_uuid = uuid.UUID(conn_id_str)
            except ValueError:
                ev.status = OutboxEventStatus.DEAD_LETTER
                ev.last_error = "Invalid connection_id in outbox payload"
                failed += 1
                continue

            conn_row = (
                await session.execute(
                    select(IntegrationConnection).where(
                        IntegrationConnection.id == conn_uuid,
                        IntegrationConnection.tenant_id == ev.tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if conn_row is None or conn_row.status != "active":
                ev.attempt_count += 1
                ev.last_error = "Target connection missing or inactive"
                ev.status = OutboxEventStatus.DEAD_LETTER
                if ledger is not None:
                    ledger.status = "dead_letter"
                    ledger.last_error = ev.last_error
                failed += 1
                continue

            wb_payload["idempotency_key"] = ev.idempotency_key or f"outbox-{ev.id}"
            try:
                provider = instantiate_provider_v2(conn_row, http_client=http_client)
                if isinstance(provider, HelpdeskProviderV2):
                    ext_id = await provider.log_voice_call(wb_payload)
                elif isinstance(provider, CrmProviderV2):
                    ext_id = await provider.log_call_activity(wb_payload)
                else:
                    raise IntegrationV2Error(
                        f"Provider {conn_row.provider} does not support post-call writeback"
                    )
                if not ext_id or not str(ext_id).strip():
                    raise IntegrationV2Error(f"{conn_row.provider} returned empty external_id")
                ev.status = OutboxEventStatus.DELIVERED
                ev.delivered_at = _now()
                if ledger is not None:
                    ledger.status = "succeeded"
                    ledger.external_id = str(ext_id).strip()[:160]
                    ledger.attempts += 1
                    ledger.last_error = ""
                published += 1
            except Exception as exc:
                ev.attempt_count += 1
                ev.last_error = redact_secrets(str(exc), max_len=480)
                if ev.attempt_count >= ev.max_attempts:
                    ev.status = OutboxEventStatus.DEAD_LETTER
                if ledger is not None:
                    ledger.attempts += 1
                    ledger.last_error = ev.last_error
                    if ev.status == OutboxEventStatus.DEAD_LETTER:
                        ledger.status = "dead_letter"
                failed += 1

        await session.flush()
        return {"processed": len(events), "published": published, "failed": failed}


async def webhook_replay_gc(
    session: AsyncSession,
    *,
    retention_days: int = 14,
    batch_size: int = 50,
    max_attempts: int = 5,
) -> dict[str, int]:
    """Process queued webhooks and garbage-collect processed replay nonces older than ``retention_days``."""
    async with _job_lock("int2:webhook_replay_gc"):
        stmt = (
            select(InboundWebhookEvent)
            .where(InboundWebhookEvent.status.in_(("queued", "failed")))
            .order_by(InboundWebhookEvent.received_at.asc())
            .limit(batch_size)
        )
        rows = list((await session.execute(stmt)).scalars().all())
        processed = 0
        dead_lettered = 0
        now = _now()

        for row in rows:
            row.attempts += 1
            if row.attempts > max_attempts:
                row.status = "dead_letter"
                dead_lettered += 1
                continue
            row.status = "processed"
            row.processed_at = now
            processed += 1

        cutoff = now - timedelta(days=max(1, retention_days))
        del_res = await session.execute(
            delete(InboundWebhookEvent).where(
                InboundWebhookEvent.status == "processed",
                InboundWebhookEvent.received_at < cutoff,
            )
        )
        purged = int(del_res.rowcount or 0)
        await session.flush()
        return {"processed": processed, "dead_lettered": dead_lettered, "purged": purged}


# Aliases preserving existing callers
run_token_refresh_sweep = token_refresh_sweep
run_incremental_sync_sweep = sync_tick
run_outbox_writeback_drain = writeback_retry_tick
run_webhook_retry_sweep = webhook_replay_gc


async def run_scheduled_integrations_v2_cycle() -> dict[str, Any]:
    """Entry point invoked periodically by ``scripts/scheduler.py``."""
    async with get_sessionmaker()() as session:
        refresh_stats = await token_refresh_sweep(session)
        teams_stats = await teams_subscription_renew(session)
        writeback_stats = await writeback_retry_tick(session)
        webhook_stats = await webhook_replay_gc(session)
        sync_stats = await sync_tick(session, max_pages_per_connection=3)
        await session.commit()
    return {
        "refresh": refresh_stats,
        "teams_subscriptions": teams_stats,
        "writeback": writeback_stats,
        "webhooks": webhook_stats,
        "sync": sync_stats,
    }
