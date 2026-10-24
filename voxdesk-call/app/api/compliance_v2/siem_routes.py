"""SIEM sink configuration and delivery status.

The sink definition is customer configuration, so it is tenant-scoped and
permission-gated. Credentials are write-only: a POST or PATCH may carry a
plaintext ``secret``, which is sealed by the existing secret store and replaced
by an opaque ``secret://`` reference; no endpoint ever returns it.

``POST /siem/sinks/{sink_id}/test`` sends a single synthetic event through the
*real* transport, so an operator can prove a destination works before enabling
it. The synthetic event carries no customer data and is marked as a test, which
keeps it distinguishable from a real audit record at the destination.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.compliance_v2.common import (
    audit_action,
    claim_idempotent,
    complete_idempotent,
    fail_idempotent,
    http_error,
    not_found,
    replay_reference,
    require_human,
    scoped_tenant,
)
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.compliance_v2.settings import (
    SIEM_SINK_KINDS,
    get_compliance_v2_settings,
)
from app.compliance_v2.siem.dispatcher import (
    build_sink,
    dispatch_tick,
    redrive_dead_letter,
    seal_sink_secret,
)
from app.compliance_v2.siem.events import probe_event
from app.compliance_v2.siem.sinks_base import SinkResult
from app.db.compliance_v2_models import SiemCursor, SiemDeadLetter, SiemSink
from app.db.session import get_session

router = APIRouter(prefix="/siem", tags=["compliance-v2-siem"])

#: Sinks that authenticate with a stored credential.
_SECRET_KINDS = frozenset({"splunk_hec", "datadog_logs", "elastic_bulk"})


class SinkCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str
    endpoint: str = Field(min_length=1, max_length=500)
    secret: str = Field(default="", max_length=4096, description="Write-only credential")
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    batch_size: int = Field(default=200, ge=1, le=5000)

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        kind = (value or "").strip().lower()
        if kind not in SIEM_SINK_KINDS:
            raise ValueError(f"kind must be one of {sorted(SIEM_SINK_KINDS)}")
        return kind

    @field_validator("name", "endpoint")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class SinkUpdate(BaseModel):
    endpoint: str | None = Field(default=None, max_length=500)
    secret: str | None = Field(default=None, max_length=4096)
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    batch_size: int | None = Field(default=None, ge=1, le=5000)


class SinkOut(BaseModel):
    id: str
    name: str
    kind: str
    endpoint: str
    enabled: bool
    batch_size: int
    has_credential: bool
    config: dict[str, Any]
    created_at: dt.datetime | None = None


class CursorOut(BaseModel):
    sink_id: str
    sink_name: str
    kind: str
    enabled: bool
    state: str
    events_delivered: int
    consecutive_failures: int
    watermark_created_at: dt.datetime | None = None
    next_attempt_at: dt.datetime | None = None
    circuit_open_until: dt.datetime | None = None
    last_error: str


class DeadLetterOut(BaseModel):
    id: str
    sink_id: str
    audit_id: str
    error_category: str
    error_message: str
    attempts: int
    redrive_count: int
    created_at: dt.datetime | None = None


class SinkTestOut(BaseModel):
    sink_id: str
    delivered: bool
    category: str
    message: str
    detail: dict[str, Any]


class SinkStatsOut(BaseModel):
    """Delivery state for one sink, in the shape an operator dashboard needs."""

    sink_id: str
    sink_name: str
    kind: str
    enabled: bool
    state: str
    events_delivered: int
    attempts: int
    consecutive_failures: int
    dead_letter_count: int
    circuit_open_until: dt.datetime | None = None
    next_attempt_at: dt.datetime | None = None
    last_success_at: dt.datetime | None = None
    last_error: str


def _sink_out(row: SiemSink) -> SinkOut:
    return SinkOut(
        id=str(row.id),
        name=row.name,
        kind=row.kind,
        endpoint=row.endpoint,
        enabled=bool(row.enabled),
        batch_size=int(row.batch_size),
        has_credential=bool(row.secret_ref),
        config=dict(row.config or {}),
        created_at=row.created_at,
    )


async def _load_sink(session: AsyncSession, tenant_id: uuid.UUID, sink_id: uuid.UUID) -> SiemSink:
    row = await session.scalar(
        select(SiemSink).where(SiemSink.id == sink_id, SiemSink.tenant_id == tenant_id)
    )
    if row is None:
        raise not_found("siem_sink")
    return row


@router.get("/sinks", response_model=list[SinkOut])
async def list_sinks(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_SIEM_READ)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    rows = await session.execute(
        select(SiemSink).where(SiemSink.tenant_id == tenant_id).order_by(SiemSink.name)
    )
    return [_sink_out(row) for row in rows.scalars()]


@router.post("/sinks", response_model=SinkOut, status_code=status.HTTP_201_CREATED)
async def create_sink(
    tenant_id: uuid.UUID,
    payload: SinkCreate,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_SIEM_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    cfg = get_compliance_v2_settings()
    # The idempotency claim is taken *before* the name check: a retried request
    # is a replay of a sink that already exists, and must return that sink
    # instead of tripping over its own name.
    claim = await claim_idempotent(
        session,
        ctx=ctx,
        request=request,
        operation="compliance_v2.siem_sink.create",
        payload=payload.model_dump(exclude={"secret"}),
    )
    replayed = replay_reference(claim, resource_type="siem_sink")
    if replayed is not None:
        replayed_row = await session.get(SiemSink, uuid.UUID(replayed))
        if replayed_row is None:
            raise not_found("siem_sink")
        return _sink_out(replayed_row)
    existing = await session.scalar(
        select(SiemSink).where(SiemSink.tenant_id == tenant_id, SiemSink.name == payload.name)
    )
    if existing is not None:
        raise http_error(
            status.HTTP_409_CONFLICT, "siem_sink_exists", "a sink with that name already exists"
        )
    if payload.kind in _SECRET_KINDS and not payload.secret:
        raise http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "siem_secret_required",
            f"sink kind {payload.kind!r} requires a credential",
        )

    try:
        row = SiemSink(
            tenant_id=tenant_id,
            name=payload.name,
            kind=payload.kind,
            endpoint=payload.endpoint,
            secret_ref=seal_sink_secret(tenant_id, payload.secret) if payload.secret else "",
            config=dict(payload.config or {}),
            enabled=bool(payload.enabled),
            batch_size=int(payload.batch_size),
            created_by=ctx.user_id,
        )
        cfg.refuse_sink_without_secret(kind=row.kind, secret_ref=row.secret_ref)
        session.add(row)
        await session.flush()
        await audit_action(
            session,
            ctx=ctx,
            request=request,
            event_type="siem_sink_created",
            resource_type="siem_sink",
            resource_id=row.id,
            detail={"kind": row.kind, "enabled": row.enabled, "endpoint_host": _host(row.endpoint)},
        )
        await complete_idempotent(session, claim, resource_type="siem_sink", resource_id=row.id)
        await session.commit()
        await session.refresh(row)
        return _sink_out(row)
    except Exception as exc:
        await fail_idempotent(session, claim, category=type(exc).__name__)
        raise


@router.patch("/sinks/{sink_id}", response_model=SinkOut)
async def update_sink(
    tenant_id: uuid.UUID,
    sink_id: uuid.UUID,
    payload: SinkUpdate,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_SIEM_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    row = await _load_sink(session, tenant_id, sink_id)
    changes: dict[str, Any] = {}
    if payload.endpoint is not None:
        row.endpoint = payload.endpoint.strip()
        changes["endpoint"] = True
    if payload.secret is not None:
        row.secret_ref = seal_sink_secret(tenant_id, payload.secret) if payload.secret else ""
        changes["credential"] = bool(row.secret_ref)
    if payload.config is not None:
        row.config = dict(payload.config)
        changes["config"] = True
    if payload.enabled is not None:
        row.enabled = bool(payload.enabled)
        changes["enabled"] = row.enabled
    if payload.batch_size is not None:
        row.batch_size = int(payload.batch_size)
        changes["batch_size"] = row.batch_size
    get_compliance_v2_settings().refuse_sink_without_secret(
        kind=row.kind, secret_ref=row.secret_ref if row.enabled else "sealed"
    )
    await session.flush()
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="siem_sink_updated",
        resource_type="siem_sink",
        resource_id=row.id,
        detail={"changed": sorted(changes)},
    )
    await session.commit()
    await session.refresh(row)
    return _sink_out(row)


@router.delete("/sinks/{sink_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sink(
    tenant_id: uuid.UUID,
    sink_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_SIEM_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    row = await _load_sink(session, tenant_id, sink_id)
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="siem_sink_deleted",
        resource_type="siem_sink",
        resource_id=row.id,
        detail={"kind": row.kind, "name": row.name},
    )
    await session.delete(row)
    await session.commit()
    return None


@router.post("/sinks/{sink_id}/test", response_model=SinkTestOut)
async def test_sink(
    tenant_id: uuid.UUID,
    sink_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_SIEM_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Send one synthetic event through the configured transport."""
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    row = await _load_sink(session, tenant_id, sink_id)
    cfg = get_compliance_v2_settings()
    event = probe_event(tenant_id=str(tenant_id), actor=str(ctx.user_id))
    try:
        transport = build_sink(row, cfg)
    except Exception as exc:  # noqa: BLE001 - a construction failure is the answer
        await audit_action(
            session,
            ctx=ctx,
            request=request,
            event_type="siem_sink_test",
            resource_type="siem_sink",
            resource_id=row.id,
            result="failure",
            detail={"error": type(exc).__name__},
        )
        await session.commit()
        return SinkTestOut(
            sink_id=str(row.id),
            delivered=False,
            category="not_configured",
            message=str(exc)[:300],
            detail={},
        )

    result: SinkResult = await transport.send_batch([event])
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="siem_sink_test",
        resource_type="siem_sink",
        resource_id=row.id,
        result="success" if result.delivered_all and not result.retryable else "failure",
        detail={"category": result.category, "accepted": result.accepted},
    )
    await session.commit()
    return SinkTestOut(
        sink_id=str(row.id),
        delivered=bool(result.accepted and not result.retryable),
        category=result.category,
        message=result.message,
        detail=dict(result.detail),
    )


@router.get("/sinks/{sink_id}/stats", response_model=SinkStatsOut)
async def sink_stats(
    tenant_id: uuid.UUID,
    sink_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_SIEM_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Counters for one destination.

    ``events_delivered`` is the cursor's own total, not a recount of the audit
    log: it is the number this sink has actually acknowledged, which is the only
    figure that answers "is this destination keeping up".
    """
    scoped_tenant(ctx, tenant_id)
    sink = await _load_sink(session, tenant_id, sink_id)
    cursor = await session.scalar(
        select(SiemCursor).where(
            SiemCursor.sink_id == sink.id, SiemCursor.tenant_id == tenant_id
        )
    )
    dead_letters = await session.scalar(
        select(func.count(SiemDeadLetter.id)).where(
            SiemDeadLetter.sink_id == sink.id, SiemDeadLetter.tenant_id == tenant_id
        )
    )
    return SinkStatsOut(
        sink_id=str(sink.id),
        sink_name=sink.name,
        kind=sink.kind,
        enabled=bool(sink.enabled),
        state=cursor.state if cursor is not None else "idle",
        events_delivered=int(cursor.events_delivered) if cursor is not None else 0,
        attempts=int(cursor.attempts) if cursor is not None else 0,
        consecutive_failures=int(cursor.consecutive_failures) if cursor is not None else 0,
        dead_letter_count=int(dead_letters or 0),
        circuit_open_until=cursor.circuit_open_until if cursor is not None else None,
        next_attempt_at=cursor.next_attempt_at if cursor is not None else None,
        last_success_at=cursor.last_success_at if cursor is not None else None,
        last_error=(cursor.last_error if cursor is not None else "") or "",
    )


@router.get("/status", response_model=list[CursorOut])
async def delivery_status(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_SIEM_READ)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    rows = await session.execute(
        select(SiemCursor, SiemSink)
        .join(SiemSink, SiemSink.id == SiemCursor.sink_id)
        .where(SiemCursor.tenant_id == tenant_id)
        .order_by(SiemSink.name)
    )
    output: list[CursorOut] = []
    for cursor, sink in rows.all():
        output.append(
            CursorOut(
                sink_id=str(sink.id),
                sink_name=sink.name,
                kind=sink.kind,
                enabled=bool(sink.enabled),
                state=cursor.state,
                events_delivered=int(cursor.events_delivered),
                consecutive_failures=int(cursor.consecutive_failures),
                watermark_created_at=cursor.watermark_created_at,
                next_attempt_at=cursor.next_attempt_at,
                circuit_open_until=cursor.circuit_open_until,
                last_error=cursor.last_error or "",
            )
        )
    return output


@router.get("/dead-letters", response_model=list[DeadLetterOut])
async def list_dead_letters(
    tenant_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_SIEM_READ)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    rows = await session.execute(
        select(SiemDeadLetter)
        .where(SiemDeadLetter.tenant_id == tenant_id)
        .order_by(SiemDeadLetter.created_at.desc())
        .limit(limit)
    )
    return [
        DeadLetterOut(
            id=str(row.id),
            sink_id=str(row.sink_id),
            audit_id=str(row.audit_id),
            error_category=row.error_category,
            error_message=row.error_message,
            attempts=int(row.attempts),
            redrive_count=int(row.redrive_count),
            created_at=row.created_at,
        )
        for row in rows.scalars()
    ]


@router.post("/dead-letters/{dead_letter_id}/redrive")
async def redrive(
    tenant_id: uuid.UUID,
    dead_letter_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_SIEM_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Put a dead-lettered event back in front of its sink."""
    scoped_tenant(ctx, tenant_id)
    try:
        outcome = await redrive_dead_letter(
            session, tenant_id=tenant_id, dead_letter_id=dead_letter_id
        )
    except KeyError:
        raise not_found("siem_dead_letter") from None
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="siem_dead_letter_redrive",
        resource_type="siem_dead_letter",
        resource_id=dead_letter_id,
        detail=dict(outcome),
    )
    await session.commit()
    return outcome


@router.post("/dispatch")
async def dispatch_now(
    tenant_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_SIEM_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Run one dispatch tick for this tenant, outside the scheduler cadence."""
    scoped_tenant(ctx, tenant_id)
    outcome = await dispatch_tick(session, tenant_id=tenant_id)
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="siem_dispatch_manual",
        resource_type="siem_dispatch",
        detail={"sinks": outcome.get("sinks", 0), "delivered": outcome.get("delivered", 0)},
    )
    await session.commit()
    return outcome


def _host(endpoint: str) -> str:
    from urllib.parse import urlsplit

    try:
        return (urlsplit(endpoint).hostname or "")[:200]
    except ValueError:
        return ""


__all__ = ["router"]
